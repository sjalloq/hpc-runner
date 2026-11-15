#!/bin/bash
cd ~/val
sub(){ qsub -terse "$@" | cut -d. -f1; }
waitj(){ for i in $(seq 60); do qstat -j $1 >/dev/null 2>&1 || return 0; sleep 1; done; echo "TIMEOUT waiting $1"; }
echo "=== F1 bash functions through -V: one-line body, multi-line body; what the job sees"
cat > fn2.sh <<'S'
#!/bin/bash
echo "BASH_FUNC vars in job env:"; env | grep -c '^BASH_FUNC' ; env | grep -A3 '^BASH_FUNC' | head -12
for f in one multi; do type -t $f || echo "$f: not defined"; done
one 2>&1; multi 2>&1
echo "PLAINVAR=[$PLAINVAR]"
S
chmod +x fn2.sh
one(){ echo one-ran; }; multi(){ echo multi-1; echo multi-2; }; export -f one multi
export PLAINVAR=$'line1\nline2'
echo "caller env:"; env | grep -A4 '^BASH_FUNC' | head -12
j=$(sub -V -cwd -j y -o f1.out fn2.sh); waitj $j; cat f1.out
j=$(sub -V -cwd -j y -o f1b.out -S /bin/bash fn2.sh); waitj $j; echo "-- with -S /bin/bash:"; cat f1b.out
unset -f one multi; unset PLAINVAR
echo "=== F2 hostname=!exec1, six jobs; and hostname=!exec2"
for i in 1 2 3 4 5 6; do j=$(sub -cwd -j y -o h$i.out -l 'hostname=!exec1' -b y hostname); waitj $j; cat h$i.out; done | sort | uniq -c
for i in 1 2 3 4 5 6; do j=$(sub -cwd -j y -o g$i.out -l 'hostname=!exec2' -b y hostname); waitj $j; cat g$i.out; done | sort | uniq -c
echo "=== F3 consumable gpu: is it set anywhere? then request it"
qhost -F gpu | head -8; j=$(sub -cwd -j y -o gpu.out -l gpu=1 -b y hostname); echo "rc=$? job=$j"; sleep 8; qstat -u tester | grep " $j "; qstat -j $j | grep -A4 "scheduling info" | head -6; qdel $j >/dev/null
echo "=== F4 qrsh client killed (SIGINT, SIGHUP, SIGTERM): what happens to the remote job"
for sig in INT HUP TERM; do
  qrsh -now no "sleep 300" & p=$!; sleep 6; jid=$(qstat -u tester | awk '/QRLOGIN|sleep/{print $1}' | tail -1); echo "sig $sig: qrsh pid $p job $jid before:"; qstat -u tester | grep " $jid "
  kill -$sig $p; wait $p; echo "qrsh rc=$?"; sleep 6; echo "after:"; qstat -u tester | grep " $jid " || echo "job $jid gone from qstat"; for i in $(seq 30); do qacct -j $jid 2>/dev/null | grep -E "^(exit_status|failed)" && break; sleep 1; done
  qdel $jid >/dev/null 2>&1
done
echo "=== F5 login shell? job with -S /bin/bash and default; does ~/.bash_profile or ~/.bashrc run"
echo 'echo PROFILE_RAN' >> ~/.bash_profile; echo 'echo BASHRC_RAN' >> ~/.bashrc
j=$(sub -cwd -j y -o ls1.out env.sh); waitj $j; cat ls1.out | grep -v PATH
j=$(sub -V -cwd -j y -o ls2.out env.sh); waitj $j; echo "-- with -V:"; cat ls2.out | grep -v PATH
echo "-- qrsh command:"; qrsh -now no 'shopt -q login_shell && echo login=yes || echo login=no' 2>&1
sed -i '/PROFILE_RAN/d' ~/.bash_profile; sed -i '/BASHRC_RAN/d' ~/.bashrc
echo "=== done"
