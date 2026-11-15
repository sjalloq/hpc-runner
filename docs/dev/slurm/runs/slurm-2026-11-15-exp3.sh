#!/bin/bash
# How soon, and how completely, a job is in sacct. Run as tester on login.
cd /work
F=JobID,JobName,Partition,State,ExitCode,ReqMem,Reason
watch(){ # $1 job, $2 seconds: print the sacct -X row each time it changes
  local last="" t0=$(date +%s%N) row
  while :; do
    row=$(sacct -n -X -P -j $1 -o $F 2>&1 | tr '\n' ' ')
    el=$(( ($(date +%s%N) - t0) / 1000000 ))
    [ "$row" != "$last" ] && { echo "  +${el} ms: [${row}]"; last=$row; }
    [ $el -ge $(( $2 * 1000 )) ] && break
    sleep 0.1
  done
}
sinfo --version
echo "=== U1 held job, watched for 40 s"
j=$(sbatch --parsable -H -J heldjob -p short -o /dev/null --wrap true); echo "job $j"; watch $j 40; scancel $j
echo "=== U2 job that runs for 3 s, watched for 40 s"
j=$(sbatch --parsable -J runjob -p short -o /dev/null --wrap 'sleep 3'); echo "job $j"; watch $j 40
echo "=== U3 job that exits at once, watched for 40 s"
j=$(sbatch --parsable -J quickjob -p short -o /dev/null --wrap 'exit 3'); echo "job $j"; watch $j 40
echo "=== U4 the same three ids again, 60 s later"
sleep 20; sacct -X -P -j $((j-2)),$((j-1)),$j -o $F
echo "=== done"
