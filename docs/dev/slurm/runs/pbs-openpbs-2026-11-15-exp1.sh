#!/bin/bash
# Experiments on the OpenPBS testbed (v23.06.06). Run as tester on login.
# Nodes: exec1 ncpus=2 scratch=1gb fast=True ngpus=1; exec2 ncpus=2 scratch=1gb.
# Server: lic=2. Custom resources: scratch (size, nh), fast (boolean, h),
# lic (long, q), ngpus (long, nh).
W=/work/pval-$(date +%s); mkdir -p $W; cd $W
qs(){ qsub "$@" 2>&1; }
waitj(){ for i in $(seq 90); do [ "$(qstat -x -f $1 2>/dev/null | awk '/job_state/{print $3}')" = F ] && return 0; sleep 1; done; echo "TIMEOUT waiting $1"; }
info(){ qstat -x -f $1 | grep -E "job_state|Exit_status|exec_host|exec_vnode|Resource_List|comment|substate" | sed 's/^ *//'; }
cat > env.sh <<'S'
#!/bin/bash
echo "JOB=$PBS_JOBID IDX=[${PBS_ARRAY_INDEX:-unset}] NCPUS=[${NCPUS:-unset}] PWD=$(pwd) O_WORKDIR=$PBS_O_WORKDIR HOST=$(hostname)"
echo "PATH=$PATH"; echo "HOME=[$HOME] MARK=[${MARK:-unset}] TMPDIR=[${TMPDIR:-unset}]"
echo "login=$(shopt -q login_shell && echo yes || echo no) fn: $(type -t myfn || echo undefined) module: $(type -t module || echo undefined)"
S
chmod +x env.sh

echo "=== P1 plain qsub: directory, environment, login shell, default output name"
export MARK=from-caller; j=$(qs env.sh); echo "id=$j"; waitj $j; ls; cat env.sh.o${j%%.*}; info $j
echo "=== P2 -V: caller's variables and a bash function"
myfn(){ echo fn-1; echo fn-2; }; export -f myfn; j=$(qs -V -j oe -o p2.out env.sh); waitj $j; cat p2.out; unset -f myfn
echo "=== P3 -v MARK"
j=$(qs -v MARK -j oe -o p3.out env.sh); waitj $j; grep MARK p3.out
echo "=== P4 exit codes: exit 3, kill -9 self, kill -15 self"
for c in 'exit 3' 'kill -9 $$' 'kill -15 $$'; do j=$(qs -j oe -o /dev/null -- /bin/bash -c "$c"); waitj $j; echo "[$c] $(qstat -x -f $j | awk '/Exit_status|job_state/{printf "%s=%s ", $1, $3}')"; done
echo "=== P5 output into a missing directory"
j=$(qs -j oe -o $W/nodir/sub/p5.out env.sh); echo "submit: $j"; sleep 8; info $j; ls nodir 2>&1; qdel $j 2>/dev/null
echo "=== P6 unknown names at submit: job-wide, inside select, queue, bad boolean"
for a in "-l nosuch=1" "-l select=1:nosuch=1" "-q nosuch" "-l select=1:fast=maybe" "-l select=1:ncpus=99" "-l lic=5" "-W depend=afterok:999999.master"; do out=$(qsub -o /dev/null -e /dev/null $a -- /bin/true 2>&1); echo "[$a] rc=$? $out"; done; sleep 4; qstat -a | tail -n +6 | awk '{print $1, $10}'; for j in $(qselect); do echo "$j: $(qstat -f $j | grep -E "comment" | sed 's/^ *//')"; done; qselect | xargs -r qdel; sleep 2
echo "=== P7 size suffixes on scratch: 500mb 500MB 500m 500M 1G 1gb 1GB bare"
for v in 500mb 500MB 500m 500M 1G 1gb 1GB 500; do out=$(qsub -o /dev/null -e /dev/null -l select=1:scratch=$v -- /bin/true 2>&1); rc=$?; echo "[scratch=$v] rc=$rc $out $( [ $rc = 0 ] && qstat -x -f $out | awk '/Resource_List.scratch|Resource_List.select/{printf "%s=%s ", $1, $3}')"; done; sleep 3
echo "=== P8 scratch is booked: two jobs on exec1, 600mb each of 1gb"
a=$(qs -o /dev/null -e /dev/null -l select=1:scratch=600mb:host=exec1 -- /bin/sleep 15); b=$(qs -o /dev/null -e /dev/null -l select=1:scratch=600mb:host=exec1 -- /bin/sleep 15); sleep 5; for j in $a $b; do echo "$j $(qstat -f $j | awk '/job_state/{print $3}') $(qstat -f $j | grep comment | sed 's/^ *//')"; done; pbsnodes exec1 | grep -E "resources_assigned.scratch|resources_available.scratch"; qdel $a $b; sleep 3
echo "=== P9 licence is counted: three jobs, lic=1 each, 2 available"
for i in 1 2 3; do qs -o /dev/null -e /dev/null -l lic=1 -- /bin/sleep 15 >/dev/null; done; sleep 5; for j in $(qselect); do echo "$j $(qstat -f $j | awk '/job_state/{print $3}') $(qstat -f $j | grep comment | sed 's/^ *//')"; done; qstat -Bf | grep -i "lic"; qselect | xargs -r qdel; sleep 3
echo "=== P10 feature and gpu: where they run"
for a in "-l select=1:fast=True" "-l select=1:fast=true" "-l select=1:fast=1" "-l select=1:fast=False" "-l select=1:ngpus=1" "-l select=1:ngpus=2" "-l select=1:host=exec2" "-l select=1:mem=1gb" "-l mem=1gb" "-l select=1:ncpus=2"; do j=$(qsub -j oe -o /dev/null $a -- /bin/hostname 2>&1); rc=$?; sleep 4; echo "[$a] rc=$rc $(qstat -x -f $j 2>&1 | awk '/job_state|exec_host|Resource_List.select|Resource_List.mem /{printf "%s=%s ", $1, $3}') $(qstat -x -f $j 2>/dev/null | grep comment | sed 's/^ *//')"; qdel $j 2>/dev/null; done
echo "=== P11 arrays: ids, per-subjob resources, output names, a failing subjob"
cat > arr.sh <<'S'
#!/bin/bash
echo "JOB=$PBS_JOBID ARRAYID=$PBS_ARRAY_ID IDX=$PBS_ARRAY_INDEX NCPUS=$NCPUS"; sleep 3; [ "$PBS_ARRAY_INDEX" = 2 ] && exit 1; exit 0
S
chmod +x arr.sh; j=$(qs -J 1-3 -l select=1:ncpus=2 arr.sh); echo "id=$j"; sleep 2; qstat -t -a | tail -n +6 | awk '{print $1, $7, $10}'; for i in $(seq 60); do [ "$(qstat -x -f "$j" | awk '/job_state/{print $3}')" = F ] && break; sleep 1; done
qstat -x -t -f "${j%%[*}[1].master" "${j%%[*}[2].master" 2>&1 | grep -E "Job Id|job_state|Exit_status|Resource_List.ncpus|exec_host"; qstat -x -f "$j" | grep -E "job_state|Exit_status|array_state_count"; ls | grep arr; cat arr.sh.o${j%%[*}.1
echo "=== P12 dependencies: afterok on a parent that fails / succeeds"
f=$(qs -o /dev/null -e /dev/null -- /bin/bash -c 'exit 1'); c=$(qs -o /dev/null -e /dev/null -W depend=afterok:$f -- /bin/true); ok=$(qs -o /dev/null -e /dev/null -- /bin/true); d=$(qs -o /dev/null -e /dev/null -W depend=afterok:$ok -- /bin/true); sleep 10
echo "child of failed parent:"; qstat -x -f $c 2>&1 | grep -E "job_state|Exit_status|comment|substate" | sed 's/^ *//'; echo "child of good parent:"; qstat -x -f $d | grep -E "job_state|Exit_status" | sed 's/^ *//'
echo "=== P13 priority, time limit, cancel"
j=$(qs -o /dev/null -e /dev/null -l walltime=00:00:05 -- /bin/sleep 600); for i in $(seq 100); do [ "$(qstat -x -f $j | awk '/job_state/{print $3}')" = F ] && break; sleep 1; done; echo "walltime: $(qstat -x -f $j | grep -E "Exit_status|comment" | sed 's/^ *//' | tr '\n' ' ')"
r=$(qs -o /dev/null -e /dev/null -- /bin/sleep 600); h=$(qs -h -o /dev/null -e /dev/null -- /bin/true); sleep 4; echo "running: $(qstat -f $r | awk '/job_state/{print $3}') held: $(qstat -f $h | awk '/job_state|Hold_Types/{printf "%s ", $3}')"; qdel $r $h; sleep 4; for j in $r $h; do echo "after qdel $j: $(qstat -x -f $j | grep -E "job_state|Exit_status|comment" | sed 's/^ *//' | tr '\n' ' ')"; done
for p in 100 -100 2000; do out=$(qsub -p $p -o /dev/null -e /dev/null -- /bin/true 2>&1); echo "[-p $p] rc=$? $out"; done
echo "=== P14 qstat on an unknown id; history of a finished job"
qstat 999999.master; echo "rc=$?"; qstat -x 999999.master; echo "rc=$?"; qstat $ok; echo "plain qstat on a finished job rc=$?"
echo "=== done"
