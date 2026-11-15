#!/bin/bash
# Experiments on the Slurm 24.11 testbed (Debian slurm-wlm 24.11.5). Run as tester on login.
W=/work/val-$(date +%s); mkdir -p $W; cd $W
sb(){ sbatch --parsable "$@" | cut -d';' -f1; }
waitj(){ for i in $(seq 90); do s=$(sacct -n -X -P -j $1 -o State | head -1); case "$s" in COMPLETED|FAILED|CANCELLED*|TIMEOUT|NODE_FAIL|OUT_OF_MEMORY) return 0;; esac; sleep 1; done; echo "TIMEOUT waiting $1 (state $s)"; }
acct(){ sacct -P -j $1 -o JobID,JobName,State,ExitCode,AllocCPUS,ReqMem,NodeList,Reason; }
cat > env.sh <<'S'
#!/bin/bash
echo "JOB=$SLURM_JOB_ID ARRAY=${SLURM_ARRAY_JOB_ID:-}_${SLURM_ARRAY_TASK_ID:-} CPT=[${SLURM_CPUS_PER_TASK:-unset}] NTASKS=[${SLURM_NTASKS:-unset}] ONNODE=[$SLURM_CPUS_ON_NODE] PWD=$(pwd) HOST=$(hostname)"
echo "PATH=$PATH"; echo "HOME=[$HOME] USER=[$USER] MARK=[${MARK:-unset}] VAR2=[${VAR2:-unset}]"
echo "login=$(shopt -q login_shell && echo yes || echo no) nvars=$(env | wc -l) nslurm=$(env | grep -c '^SLURM')"
echo "fn: $(type -t myfn || echo undefined) module: $(type -t module || echo undefined) BASH_FUNC=$(env | grep -c '^BASH_FUNC')"
S
chmod +x env.sh

echo "=== S1 plain sbatch: cwd, cpus var, login shell, env size (default --export=ALL)"
export MARK=from-caller; j=$(sb -o s1.out env.sh); waitj $j; cat s1.out; acct $j
echo "=== S2 -c 2 without -n: tasks, cpus"
j=$(sb -c 2 -o s2.out env.sh); waitj $j; grep JOB s2.out; scontrol show job $j | grep -E "NumTasks|NumCPUs|CPUs/Task|TRES="
echo "=== S3 --export=NONE"
j=$(sb --export=NONE -o s3.out env.sh); waitj $j; cat s3.out
echo "=== S4 --export=MARK (explicit list, no ALL)"
j=$(sb --export=MARK -o s4.out env.sh); waitj $j; cat s4.out
echo "=== S5 --export=ALL,VAR2=x with VAR2=caller in the environment: which value wins"
j=$(VAR2=caller sb --export=ALL,VAR2=x -o s5.out env.sh); waitj $j; grep MARK s5.out
echo "=== S6 bash function through ALL and through NONE"
myfn(){ echo fn-1; echo fn-2; }; export -f myfn
j=$(sb -o s6a.out env.sh); waitj $j; grep fn: s6a.out
j=$(sb --export=NONE -o s6b.out env.sh); waitj $j; grep fn: s6b.out; unset -f myfn
echo "=== S7 parent -c 2 submits child without -c: child's SLURM_CPUS_PER_TASK"
cat > parent.sh <<S
#!/bin/bash
echo "parent CPT=\$SLURM_CPUS_PER_TASK JOB=\$SLURM_JOB_ID"
sbatch --parsable -o $W/child.out $W/env.sh
S
chmod +x parent.sh; j=$(sb -c 2 -o parent.out parent.sh); waitj $j; cat parent.out; c=$(tail -1 parent.out); waitj $c; grep JOB child.out
echo "=== S8 output into a missing directory (24.11)"
j=$(sb -o $W/nodir/sub/s8.out env.sh); waitj $j; acct $j; ls -la nodir/sub 2>&1
echo "=== S9 exit codes in sacct: exit 3, kill -9 self, kill -15 self"
for c in 'exit 3' 'kill -9 $$' 'kill -15 $$'; do j=$(sb -o /dev/null --wrap "$c"); waitj $j; echo "[$c]"; acct $j | tail -2; done
echo "=== S10 srun: exit code, signal, output streams, cwd, allocation released"
srun bash -c 'echo out; echo err >&2; pwd; exit 7'; echo "rc=$?"; srun bash -c 'kill -9 $$'; echo "kill9 rc=$?"; srun bash -c 'kill -15 $$'; echo "kill15 rc=$?"; echo "squeue after srun: $(squeue -h | wc -l) jobs"
echo "=== S11 srun failures, with and without SLURM_EXIT_ERROR"
srun -p nosuch true; echo "bad partition rc=$?"; SLURM_EXIT_ERROR=112 srun -p nosuch true; echo "bad partition with SLURM_EXIT_ERROR=112 rc=$?"; SLURM_EXIT_ERROR=112 srun -A nosuch true; echo "bad account rc=$?"; SLURM_EXIT_ERROR=112 srun bash -c 'exit 5'; echo "command exit 5 with SLURM_EXIT_ERROR rc=$?"
echo "=== S12 srun --export=NONE environment"
srun --export=NONE $W/env.sh; echo "rc=$?"
echo "=== S13 srun client killed (INT twice, HUP, TERM)"
for sig in INT HUP TERM; do srun sleep 300 & p=$!; sleep 4; jid=$(squeue -h -u tester -o %i | tail -1); kill -$sig $p; [ $sig = INT ] && { sleep 0.3; kill -INT $p 2>/dev/null; }; wait $p; echo "sig $sig: srun rc=$? job $jid"; sleep 4; acct $jid | tail -2; scancel $jid 2>/dev/null; done
echo "=== S14 sbatch rejections"
for a in "-p nosuch" "-A nosuch" "--gres=nosuch:1" "-C nosuch" "-L nosuch:1" "-L lic:5" "--gres=gpu:5" "--tmp=5000" "--mem=999999" "-d afterok:999999"; do out=$(sbatch -o /dev/null $a --wrap true 2>&1); echo "[$a] rc=$? $out"; done; scancel -u tester; sleep 3
echo "=== S15 accepted requests: where they run"
for a in "-C fast" "--gres=gpu:1" "-L lic:1" "--tmp=800" "--mem=1G" "--mem=1000"; do j=$(sb -o /dev/null $a --wrap hostname); waitj $j; echo "[$a] $(sacct -n -X -P -j $j -o State,NodeList,ReqMem,ReqTRES)"; done
echo "=== S16 --tmp is a filter: two jobs at once on exec1, each --tmp=800 of TmpDisk=1000"
a=$(sb -w exec1 --tmp=800 -o /dev/null --wrap 'sleep 15'); b=$(sb -w exec1 --tmp=800 -o /dev/null --wrap 'sleep 15'); sleep 6; squeue -h -o "%i %T %N %r"; scancel $a $b; sleep 3
echo "=== S17 licences are counted: three jobs, -L lic:1 each, Total=2"
for i in 1 2 3; do sb -L lic:1 -o /dev/null --wrap 'sleep 15' >/dev/null; done; sleep 6; squeue -h -o "%i %T %r"; scontrol show lic | grep Used; scancel -u tester; sleep 3
echo "=== S18 SBATCH_PARTITION against a #SBATCH directive and against the command line"
printf '#!/bin/bash\n#SBATCH -p all\nhostname\n' > part.sh; j=$(SBATCH_PARTITION=short sb -o /dev/null part.sh); waitj $j; echo "directive all, env short -> $(sacct -n -X -P -j $j -o Partition)"; j=$(SBATCH_PARTITION=short sb -p all -o /dev/null part.sh); waitj $j; echo "cmdline all, env short -> $(sacct -n -X -P -j $j -o Partition)"
echo "=== done"
