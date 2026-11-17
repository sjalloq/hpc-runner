#!/bin/bash
# Accounts with AccountingStorageEnforce=associations. Run as tester on
# login. Accounts: proj (every user), other (no user).
cd /work
sinfo --version; scontrol show config | grep -i AccountingStorageEnforce
try(){ out=$("$@" 2>&1); echo "[$*] rc=$? $out"; }
echo "=== W1 sbatch: no account, own account, unknown account, account the user is not in"
try sbatch -o /dev/null --wrap true
try sbatch -o /dev/null -A proj --wrap true
try sbatch -o /dev/null -A nosuch --wrap true
try sbatch -o /dev/null -A other --wrap true
echo "=== W2 srun: the same four"
try srun hostname
try srun -A proj hostname
try srun -A nosuch hostname
try srun -A other hostname
sleep 4
echo "=== W3 what sacct recorded"
sacct -n -P -o JobID,Account,State,ExitCode
echo "=== done"
