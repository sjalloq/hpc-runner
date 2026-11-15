#!/bin/bash
# Readiness probe for every PBS flavour's stack; runs on `login`. Exits 0
# when every check passes; otherwise prints the failing check and exits 1.
set -uo pipefail

fail() {
    echo "ready.sh: $1" >&2
    shift
    [ $# -eq 0 ] || printf '%s\n' "$@" >&2
    exit 1
}

# 1. Both nodes free.
for h in exec1 exec2; do
    out=$(pbsnodes "$h" 2>&1) || fail "pbsnodes $h failed" "$out"
    grep -Eq '^ *state = (free|job-busy)$' <<<"$out" || fail "pbsnodes: $h not usable" "$out"
done

# 2. A job runs end to end as alice and its exit status is in the history.
cd /tmp || exit 1
sub=$(timeout 20 sudo -u alice /opt/pbs/bin/qsub -o /dev/null -e /dev/null -- /bin/true 2>&1)
rc=$?
[ $rc -eq 0 ] || fail "qsub as alice failed (rc=$rc)" "$sub"
jid=$sub
deadline=$((SECONDS + 30))
until info=$(qstat -x -f "$jid" 2>&1) && grep -q 'job_state = F' <<<"$info"; do
    [ "$SECONDS" -lt "$deadline" ] || fail "job $jid not finished within 30 s" "$info"
    sleep 0.5
done
grep -q 'Exit_status = 0' <<<"$info" || fail "job $jid did not exit 0" "$info"

echo "ready: exec1 exec2 free, job $jid ran as alice and finished with Exit_status 0"
