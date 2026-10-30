#!/bin/bash
# Readiness probe for the `soge` stack; runs on `login` (docs/dev/testbed/TESTBED_PLAN.md
# section 3). Exits 0 when every check passes; otherwise prints the failing
# check and the evidence and exits 1. The caller polls it (wait-for.sh / the
# pytest fixture, 90 s cap).
set -uo pipefail

fail() {
    echo "ready.sh: $1" >&2
    shift
    [ $# -eq 0 ] || printf '%s\n' "$@" >&2
    exit 1
}

# 1. qhost lists both exec hosts with a numeric load.
qhost_out=$(qhost 2>&1) || fail "qhost failed" "$qhost_out"
for h in exec1 exec2; do
    echo "$qhost_out" | awk -v h="$h" '$1 == h && $7 ~ /^[0-9.]+$/ { ok = 1 } END { exit !ok }' \
        || fail "qhost: $h missing or load not numeric" "$qhost_out"
done

# 2. qstat -f shows all.q on both hosts with no alarm/unknown/error/disabled state.
qstat_out=$(qstat -f 2>&1) || fail "qstat -f failed" "$qstat_out"
for h in exec1 exec2; do
    echo "$qstat_out" | awk -v q="all.q@$h" '
        $1 == q { found = 1; if (NF >= 6 && $6 ~ /[auEd]/) bad = $6 }
        END { if (!found) exit 1; if (bad) exit 2 }'
    case $? in
        1) fail "qstat -f: all.q@$h missing" "$qstat_out" ;;
        2) fail "qstat -f: all.q@$h in a bad state" "$qstat_out" ;;
    esac
done

# 3. Execution and submit host lists.
sel=$(qconf -sel 2>&1) || fail "qconf -sel failed" "$sel"
for h in exec1 exec2; do
    grep -qx "$h" <<<"$sel" || fail "qconf -sel: $h missing" "$sel"
done
ss=$(qconf -ss 2>&1) || fail "qconf -ss failed" "$ss"
grep -qx login <<<"$ss" || fail "qconf -ss: login missing" "$ss"

# 4. A job runs end to end as alice and reaches the accounting file.
cd /tmp || exit 1
sub=$(timeout 20 sudo -u alice qsub -sync y -b y -cwd -o /dev/null -j y true 2>&1)
rc=$?
[ $rc -eq 0 ] || fail "qsub -sync y as alice failed (rc=$rc)" "$sub"
jid=$(sed -n 's/^Your job \([0-9]*\) .*/\1/p' <<<"$sub" | head -n1)
[ -n "$jid" ] || fail "could not find the job id in qsub output" "$sub"
deadline=$((SECONDS + 5))
until acct=$(qacct -j "$jid" 2>&1); do
    [ "$SECONDS" -lt "$deadline" ] || fail "qacct -j $jid not available within 5 s" "$acct"
    sleep 0.5
done

echo "ready: exec1 exec2 up, all.q clean, job $jid ran as alice and is in qacct"
