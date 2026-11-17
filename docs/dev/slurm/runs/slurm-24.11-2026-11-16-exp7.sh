#!/bin/bash
# Which cancellations show in sacct as plain CANCELLED and which as
# "CANCELLED by <uid>". Run as root on login; jobs are tester's.
T(){ su - tester -c "cd /work && $*"; }
sub(){ T "sbatch --parsable -p short -o /dev/null $*"; }
show(){ sleep 3; sacct -n -P -j "$1" -o JobID,State,ExitCode,Reason; }
sinfo --version; id -u tester
echo "=== V1 tester cancels own held job"
j=$(sub -H --wrap true); T scancel $j; show $j
echo "=== V2 tester cancels own running job"
j=$(sub --wrap "'sleep 60'"); sleep 3; T scancel $j; show $j
echo "=== V3 root cancels tester's running job"
j=$(sub --wrap "'sleep 60'"); sleep 3; scancel $j; show $j
echo "=== V4 root cancels tester's held job"
j=$(sub -H --wrap true); scancel $j; show $j
echo "=== V5 parent fails, dependent with --kill-on-invalid-dep=yes"
p=$(sub --wrap "'exit 1'"); c=$(sub --kill-on-invalid-dep=yes -d afterok:$p --wrap true); sleep 5; show $c
echo "=== V6 parent cancelled by tester, dependent with --kill-on-invalid-dep=yes"
p=$(sub --wrap "'sleep 60'"); c=$(sub --kill-on-invalid-dep=yes -d afterok:$p --wrap true); sleep 3; T scancel $p; sleep 5; show $c
echo "=== V7 tester cancels one task of a running array, then the rest"
j=$(sub -a 1-3 --wrap "'sleep 60'"); sleep 3; T scancel ${j}_2; sleep 2; T scancel $j; show $j
echo "=== V8 tester cancels a pending array (held)"
j=$(sub -H -a 1-3 --wrap true); T scancel $j; show $j
echo "=== V9 scancel --signal=TERM on a running job (not a cancel)"
j=$(sub --wrap "'sleep 60'"); sleep 3; T scancel --signal=TERM --batch $j; sleep 2; show $j
echo "=== done"
