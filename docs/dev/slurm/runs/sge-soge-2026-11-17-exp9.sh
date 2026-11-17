#!/bin/bash
# exp9: what `hpc run` leaves in $? on SGE (soge) for four cases.
# Run from the host: bash sge-soge-2026-11-17-exp9.sh
# Needs the stack up: hpc-testbed --scheduler sge up
P=hpc-testbed-sge-soge
L=$P-login
HPC=/opt/venv/3.12/bin/hpc
in_login() { docker exec -u tester -w /work "$L" bash -c "$1"; }
job_of() { in_login "qstat -u tester | awk -v n=$1 '\$3==n {print \$1, \$5}'"; }
wait_running() {  # name
  for _ in $(seq 60); do
    set -- "$1" $(job_of "$1")
    [ "$3" = r ] && { echo "$2"; return; }
    sleep 1
  done
  echo "never ran: $1" >&2
}

echo "== A. skipped: dependent of a job that exited 1 =="
parent=$(in_login "$HPC run -b -N e9par sh -c 'exit 1'" | grep -o '[0-9]\+' | head -1)
echo "parent $parent"
in_login "$HPC run -b -w -N e9dep -D $parent true; echo \"hpc rc=\$?\""
in_login "qacct -j e9dep | grep -E 'jobnumber|failed|exit_status'"

echo "== B1. interactive: the command exits 3 =="
in_login "$HPC run -N e9int sh -c 'exit 3'; echo \"hpc rc=\$?\""

echo "== B2. interactive: qrsh itself killed with SIGTERM =="
docker exec -u tester -w /work "$L" bash -c "$HPC run -N e9term sleep 300; echo \"hpc rc=\$?\"" &
bg=$!
id=$(wait_running e9term); echo "job $id running"
in_login "pgrep -a qrsh; pkill -TERM -x qrsh"
wait $bg
sleep 3; in_login "qstat -u tester"

echo "== B3. interactive: the job deleted with qdel while running =="
docker exec -u tester -w /work "$L" bash -c "$HPC run -N e9qdel sleep 300; echo \"hpc rc=\$?\"" &
bg=$!
id=$(wait_running e9qdel); echo "job $id running"
in_login "qdel $id"
wait $bg

echo "== C. batch --wait: the node killed under the job =="
docker exec -u tester -w /work "$L" bash -c "$HPC run -b -w -N e9lost -S '-l hostname=exec2' sleep 300; echo \"hpc rc=\$?\"" &
bg=$!
id=$(wait_running e9lost); echo "job $id running"
docker kill $P-exec2 >/dev/null; sleep 2; docker start $P-exec2 >/dev/null
wait $bg
in_login "qacct -j $id | grep -E 'jobnumber|failed|exit_status'"
