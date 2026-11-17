#!/bin/bash
# What `srun --export=<list>` gives the job. Run on login (24.11.5) through
# `su - tester`, so HOME, USER and LOGNAME are set in the calling shell.
echo "caller: HOME=[$HOME] USER=[$USER] LOGNAME=[$LOGNAME]"
cd /work; export MARK=from-caller
P='echo HOME=[$HOME] USER=[$USER] LOGNAME=[$LOGNAME] MARK=[${MARK:-unset}] PATH=$PATH nvars=$(/usr/bin/env | /usr/bin/grep -vc ^SLURM)'
echo "--- NONE, command by name"; srun --export=NONE bash -c "$P"
echo "--- NONE"; srun --export=NONE /bin/bash -c "$P"
echo "--- HOME,USER,LOGNAME"; srun --export=HOME,USER,LOGNAME /bin/bash -c "$P"
echo "--- HOME,USER,LOGNAME,MARK=x"; srun --export=HOME,USER,LOGNAME,MARK=x /bin/bash -c "$P"
echo "--- HOME,USER,LOGNAME,PATH"; srun --export=HOME,USER,LOGNAME,PATH bash -c "$P"; echo rc=$?
