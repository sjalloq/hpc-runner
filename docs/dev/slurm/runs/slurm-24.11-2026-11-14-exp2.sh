#!/bin/bash
# Second round on the Slurm 24.11 testbed (Debian slurm-wlm 24.11.5). Run as tester on login.
W=/work/val2-$(date +%s); mkdir -p $W; cd $W
sb(){ sbatch --parsable "$@" | cut -d';' -f1; }
st(){ sacct -n -X -P -j $1 -o State | head -1; }
waitj(){ for i in $(seq 120); do case "$(st $1)" in COMPLETED|FAILED|CANCELLED*|TIMEOUT|NODE_FAIL|OUT_OF_MEMORY) return 0;; esac; sleep 1; done; echo "TIMEOUT waiting $1 (state $(st $1))"; }
acct(){ sacct -P -j $1 -o JobID,JobName,State,ExitCode,Reason; }
now(){ date +%s.%N; }

echo "=== T1 is the sacct row there when sbatch returns? (5 held jobs)"
for i in 1 2 3 4 5; do j=$(sb -H -o /dev/null --wrap true); r=$(sacct -n -X -P -j $j -o JobID,State,Reason); echo "job $j: sacct at once -> [${r}]  squeue -> [$(squeue -h -j $j -o '%t %r')]"; done
scancel -u tester; sleep 2
echo "=== T2 time from the controller showing the job finished to sacct showing it (5 jobs)"
for i in 1 2 3 4 5; do j=$(sb -o /dev/null --wrap true); tc=""; ts=""
  for k in $(seq 400); do [ -z "$tc" ] && scontrol show job $j 2>/dev/null | grep -q "JobState=COMPLETED" && tc=$(now); [ -z "$ts" ] && [ "$(st $j)" = COMPLETED ] && ts=$(now); [ -n "$tc" ] && [ -n "$ts" ] && break; sleep 0.05; done
  echo "job $j: controller COMPLETED at $tc, sacct COMPLETED at $ts, sacct minus controller = $(echo "$ts - $tc" | bc) s"; done
echo "=== T3 state names: squeue short and long, sacct, for pending / running / held"
r=$(sb -o /dev/null --wrap 'sleep 20'); h=$(sb -H -o /dev/null --wrap true); sleep 3
for j in $r $h; do echo "job $j: squeue %t=[$(squeue -h -j $j -o %t)] %T=[$(squeue -h -j $j -o %T)] reason=[$(squeue -h -j $j -o %r)] sacct=[$(st $j)]"; done
echo "=== T4 suspend, resume, requeue (as root)"
sudo scontrol suspend $r; sleep 2; echo "suspended: squeue=[$(squeue -h -j $r -o '%t %T')] sacct=[$(st $r)]"; sudo scontrol resume $r; sleep 2; echo "resumed: squeue=[$(squeue -h -j $r -o '%t %T')] sacct=[$(st $r)]"
sudo scontrol requeue $r; sleep 2; echo "requeued: squeue=[$(squeue -h -j $r -o '%t %T %r')] sacct=[$(st $r)]"; sacct -P -j $r -D -o JobID,State,ExitCode,Start,End
echo "=== T5 cancel pending and running"
scancel $h; scancel $r; sleep 3; acct $h; acct $r
p=$(sb -o /dev/null --wrap 'sleep 60'); sleep 3; scancel $p; sleep 3; acct $p
echo "=== T6 dependencies: afterok on a parent that fails"
f=$(sb -o /dev/null --wrap 'exit 1'); c1=$(sb -d afterok:$f -o /dev/null --wrap true); c2=$(sb -d afterok:$f --kill-on-invalid-dep=yes -o /dev/null --wrap true); waitj $f; sleep 6
echo "no kill flag:  squeue=[$(squeue -h -j $c1 -o '%t %T %r')]"; acct $c1
echo "kill flag yes: squeue=[$(squeue -h -j $c2 -o '%t %T %r')]"; acct $c2; scontrol show job $c2 | grep -E "JobState|Reason"
scancel $c1; sleep 2; echo "after scancel of the stuck one:"; acct $c1
echo "=== T7 dependencies: afterok / afterany / after on a parent that succeeds; afterok on a parent already finished"
ok=$(sb -o /dev/null --wrap 'sleep 3'); a=$(sb -d afterok:$ok -o /dev/null --wrap true); b=$(sb -d afterany:$ok -o /dev/null --wrap true); c=$(sb -d after:$ok -o /dev/null --wrap true); waitj $a; waitj $b; waitj $c; for j in $ok $a $b $c; do echo "$j $(st $j)"; done
out=$(sbatch --parsable -d afterok:$ok -o /dev/null --wrap true 2>&1); echo "afterok on finished parent $ok: rc=$? [$out]"; sleep 3; echo "state: $(st ${out%%;*})"
echo "=== T8 arrays: ids, throttle, output names, sacct rows, a failing task"
cat > arr.sh <<'S'
#!/bin/bash
echo "JOB=$SLURM_JOB_ID ARRAYJOB=$SLURM_ARRAY_JOB_ID TASK=$SLURM_ARRAY_TASK_ID"
sleep 4; [ "$SLURM_ARRAY_TASK_ID" = 2 ] && exit 1; exit 0
S
chmod +x arr.sh; j=$(sb --array=1-4%2 arr.sh); echo "sbatch --parsable printed: $j"; sleep 2; squeue -h -o "%i %T %r" | sort; echo "sacct while running:"; sacct -P -j $j -o JobID,State,ExitCode
for i in $(seq 40); do [ "$(squeue -h -j $j | wc -l)" = 0 ] && break; sleep 1; done; sleep 2
echo "sacct at the end:"; sacct -P -j $j -o JobID,JobIDRaw,State,ExitCode; echo "sacct -X:"; sacct -X -P -j $j -o JobID,State,ExitCode; ls; cat slurm-${j}_1.out
echo "=== T9 default output file name and where it goes"
mkdir sub; cd sub; j=$(sb --wrap 'echo hi; pwd'); waitj $j; ls; cat slurm-$j.out; cd ..
j=$(sb --chdir=$W/sub -o chd.out --wrap pwd); waitj $j; echo "--chdir: $(cat sub/chd.out 2>&1)"
echo "=== T10 time limit"
j=$(sb -t 0:01 -o /dev/null --wrap 'sleep 600'); waitj $j; acct $j
echo "=== T11 login shell: plain shebang and 'bash -l' shebang; ~/.bash_profile and ~/.bashrc"
echo 'echo PROFILE_RAN' >> ~/.bash_profile; echo 'echo BASHRC_RAN' >> ~/.bashrc
printf '#!/bin/bash\nshopt -q login_shell && echo login=yes || echo login=no\n' > l1.sh; printf '#!/bin/bash -l\nshopt -q login_shell && echo login=yes || echo login=no\n' > l2.sh
for f in l1 l2; do j=$(sb -o $f.out $f.sh); waitj $j; echo "$f: $(tr '\n' ' ' < $f.out)"; done
j=$(sb --export=NONE -o l3.out l1.sh); waitj $j; echo "l1 with --export=NONE: $(tr '\n' ' ' < l3.out)"
sed -i '/PROFILE_RAN/d' ~/.bash_profile; sed -i '/BASHRC_RAN/d' ~/.bashrc
echo "=== T12 srun inside a batch job submitted with -c 2"
j=$(sb -c 2 -o in.out --wrap 'echo batch CPT=$SLURM_CPUS_PER_TASK; srun bash -c "echo step CPT=\${SLURM_CPUS_PER_TASK:-unset} ONNODE=\$SLURM_CPUS_ON_NODE"; echo srun rc=$?'); waitj $j; cat in.out; sacct -P -j $j -o JobID,JobName,State,AllocCPUS
echo "=== T13 --pty: with a terminal (via script) and without"
script -qec "srun --pty bash -c 'tty; echo in-pty'" /dev/null | tr -d '\r'; echo "rc=$?"
srun --pty bash -c 'tty; echo no-terminal' < /dev/null; echo "rc=$?"
echo "=== T14 whitespace in a directive value"
printf '#!/bin/bash\n#SBATCH --job-name=two words\ntrue\n' > ws1.sh; out=$(sbatch --parsable -o /dev/null ws1.sh 2>&1); echo "unquoted: rc=$? [$out] name=[$(sacct -n -X -P -j ${out%%;*} -o JobName 2>/dev/null)]"
printf '#!/bin/bash\n#SBATCH --job-name="two words"\ntrue\n' > ws2.sh; out=$(sbatch --parsable -o /dev/null ws2.sh 2>&1); echo "quoted: rc=$? [$out] name=[$(sacct -n -X -P -j ${out%%;*} -o JobName 2>/dev/null)]"
echo "=== T15 details of a finished job from the controller, and of an unknown id"
j=$(sb -o /dev/null --wrap true); waitj $j; scontrol show job $j | head -3; scontrol show job 999999; echo "rc=$?"; sacct -n -X -P -j 999999 -o State; echo "sacct unknown id rc=$? (empty output above)"
echo "=== done"
