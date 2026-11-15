#!/bin/bash
# Experiments on the soge testbed (SGE 8.1.9). Run as tester on login.
cd ~; W=~/val; rm -rf $W; mkdir -p $W; cd $W
sub(){ qsub -terse "$@" | cut -d. -f1; }
waitj(){ for i in $(seq 60); do qstat -j $1 >/dev/null 2>&1 || return 0; sleep 1; done; echo "TIMEOUT waiting $1"; }
acct(){ for i in $(seq 40); do qacct -j $1 2>/dev/null | grep -E "^(slots|exit_status|failed|qname|hostname|taskid)" && return; sleep 1; done; echo "no qacct for $1"; }
show(){ echo "--- $1"; cat "$1" 2>&1; }
cat > env.sh <<'S'
#!/bin/bash
echo "JOB_ID=$JOB_ID SGE_TASK_ID=$SGE_TASK_ID NSLOTS=$NSLOTS PWD=$(pwd) HOST=$(hostname)"
echo "PATH=$PATH"
echo "shell_opts: $0 login=$(shopt -q login_shell && echo yes || echo no)"
S
chmod +x env.sh

echo "=== E1 no -pe: slots"; j=$(sub -cwd -j y -o e1.out env.sh); waitj $j; show e1.out; acct $j
echo "=== E2 -pe smp 2: slots, host"; j=$(sub -cwd -j y -o e2.out -pe smp 2 env.sh); waitj $j; show e2.out; acct $j
echo "=== E3 default cwd (no -cwd), default PATH (no -V)"; j=$(sub -j y -o $W/e3.out $W/env.sh); waitj $j; show e3.out
echo "=== E4 PATH under -V"; j=$(PATH=/marker/bin:$PATH sub -V -cwd -j y -o e4.out env.sh); waitj $j; show e4.out
echo "=== E5 -v SGE_TASK_ID=77 JOB_ID=5 on a plain job"; j=$(sub -cwd -j y -o e5.out -v SGE_TASK_ID=77,JOB_ID=5 env.sh); echo submitted=$j; waitj $j; show e5.out
echo "=== E6 -V with caller JOB_ID=5 SGE_TASK_ID=9 in env (stale parent values)"; j=$(JOB_ID=5 SGE_TASK_ID=9 sub -V -cwd -j y -o e6.out env.sh); echo submitted=$j; waitj $j; show e6.out
echo "=== E7 bash function through -V"; cat > fn.sh <<'S'
#!/bin/bash
type myfn 2>&1 | head -1; myfn 2>&1
S
chmod +x fn.sh; myfn(){ echo "function ran
second line"; }; export -f myfn; j=$(sub -V -cwd -j y -o e7.out fn.sh); waitj $j; show e7.out; unset -f myfn
echo "=== E8 output to missing directory"; j=$(sub -cwd -j y -o /home/tester/val/nodir/sub/e8.out env.sh); echo submitted=$j; sleep 8; qstat -j $j 2>&1 | grep -E "error reason|job_state" ; qstat -u tester | tail -3; ls -la nodir 2>&1; acct $j; qdel $j >/dev/null 2>&1
echo "=== E9 exit codes: 3, 99, 100"; for c in 3 99 100; do echo "exit $c" > x$c.sh; j=$(sub -cwd -j y -o x$c.out -S /bin/bash x$c.sh); sleep 10; echo "code $c job $j: qstat:"; qstat -u tester | grep " $j " ; qacct -j $j 2>/dev/null | grep -E "^(exit_status|failed)"; qdel $j >/dev/null 2>&1; done
echo "=== E10 unknown resource name"; qsub -cwd -l nosuch=1 env.sh; echo "rc=$?"
echo "=== E11 hostname exclusion"; j=$(sub -cwd -j y -o e11.out -l 'hostname=!exec1' env.sh); echo "rc=$? job=$j"; waitj $j; show e11.out
echo "=== E12 BOOL =1 and =TRUE (display_win_gui is BOOL)"; qsub -cwd -l display_win_gui=1 -w v env.sh; echo "rc=$?"; qsub -cwd -l display_win_gui=TRUE -w v env.sh; echo "rc=$?"; qsub -cwd -l display_win_gui=2 -w v env.sh; echo "rc=$?"
echo "=== E13 qrsh exit codes"; qrsh -now no 'exit 7'; echo "exit7 -> rc=$?"; qrsh -now no 'kill -9 $$'; echo "kill9 -> rc=$?"; qrsh -now no 'kill -15 $$'; echo "kill15 -> rc=$?"; qrsh -now no -q nosuch.q true; echo "badqueue -> rc=$?"; qrsh -now no -l nosuch=1 true; echo "badresource -> rc=$?"
echo "=== E14 qrsh without command rejects -cwd / -V"; qrsh -now no -cwd </dev/null 2>&1 | head -3; echo "rc=${PIPESTATUS[0]}"; qrsh -now no -V </dev/null 2>&1 | head -3; echo "rc=${PIPESTATUS[0]}"
echo "=== E15 qrsh with command: cwd, -cwd, output streaming, PATH"; qrsh -now no 'pwd; echo out; echo err >&2'; qrsh -now no -cwd pwd
echo "=== E16 array child: non-array job submitted with -V from inside an array task"; cat > parent.sh <<'S'
#!/bin/bash
echo "parent JOB_ID=$JOB_ID SGE_TASK_ID=$SGE_TASK_ID"
qsub -terse -V -cwd -j y -o child.out env.sh
S
chmod +x parent.sh; j=$(sub -cwd -j y -o parent.out -t 3-3 parent.sh); waitj $j; sleep 6; show parent.out; show child.out
echo "=== E17 which queue without -q"; for i in 1 2 3; do j=$(sub -cwd -j y -o q$i.out env.sh); waitj $j; acct $j | grep qname; done
echo "=== done"
