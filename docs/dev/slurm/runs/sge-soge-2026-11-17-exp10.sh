#!/bin/bash
# exp10: $? of `hpc run` (interactive) and of bare qrsh when the launcher or
# the job is killed. SGE soge. Run from the host with the stack up.
P=hpc-testbed-sge-soge
L=$P-login
HPC=/opt/venv/3.12/bin/hpc
in_login() { docker exec -u tester -w /work "$L" bash -c "$1"; }
wait_running() {  # prints the id of tester's one running job
  for _ in $(seq 60); do
    id=$(in_login "qstat -u tester | awk '\$5==\"r\" {print \$1}' | head -1")
    [ -n "$id" ] && { echo "$id"; return; }
    sleep 1
  done
  echo "never ran" >&2
}
bg_login() { docker exec -u tester -w /work "$L" bash -c "$1" & }

for sig in TERM KILL INT HUP; do
  echo "== bare qrsh, qrsh killed with SIG$sig =="
  bg_login "qrsh -now no sleep 300; echo \"qrsh rc=\$?\""; bg=$!
  id=$(wait_running); echo "job $id running"
  in_login "pkill -$sig -x qrsh"; wait $bg
  sleep 5; in_login "qstat -u tester | tail -n +3; qdel -u tester >/dev/null 2>&1"; sleep 3

  echo "== hpc run, qrsh killed with SIG$sig =="
  bg_login "$HPC run sleep 300; echo \"hpc rc=\$?\""; bg=$!
  id=$(wait_running); echo "job $id running"
  in_login "pkill -$sig -x qrsh"; wait $bg
  sleep 5; in_login "qstat -u tester | tail -n +3; qdel -u tester >/dev/null 2>&1"; sleep 3
done

echo "== bare qrsh, job deleted with qdel =="
bg_login "qrsh -now no sleep 300; echo \"qrsh rc=\$?\""; bg=$!
id=$(wait_running); echo "job $id running"
in_login "qdel $id"; wait $bg; sleep 3

echo "== hpc run, job deleted with qdel =="
bg_login "$HPC run sleep 300; echo \"hpc rc=\$?\""; bg=$!
id=$(wait_running); echo "job $id running"
in_login "qdel $id"; wait $bg; sleep 3

echo "== hpc run, the command kills itself with SIGTERM =="
in_login "$HPC run sh -c 'kill -TERM \$\$'; echo \"hpc rc=\$?\""
echo "== bare qrsh, the command kills itself with SIGTERM =="
in_login "qrsh -now no sh -c 'kill -TERM \$\$'; echo \"qrsh rc=\$?\""
