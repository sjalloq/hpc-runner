#!/bin/bash
# Readiness probe for every Slurm flavour's stack; runs on `login`. Exits 0
# when every check passes; otherwise prints the failing check and exits 1.
set -uo pipefail

fail() {
    echo "ready.sh: $1" >&2
    shift
    [ $# -eq 0 ] || printf '%s\n' "$@" >&2
    exit 1
}

# 1. Both nodes idle (or busy), none down or unknown.
nodes=$(sinfo -h -N -p all -o '%N %T' 2>&1) || fail "sinfo failed" "$nodes"
for h in exec1 exec2; do
    echo "$nodes" | awk -v h="$h" '$1 == h && $2 ~ /^(idle|mixed|allocated)$/ { ok = 1 } END { exit !ok }' \
        || fail "sinfo: $h missing or not usable" "$nodes"
done

# 2. Accounting answers.
acct=$(sacctmgr -n show cluster 2>&1) || fail "sacctmgr failed" "$acct"

# 3. A job runs end to end as alice and reaches sacct as COMPLETED.
cd /tmp || exit 1
sub=$(timeout 20 sudo -u alice sbatch --parsable -o /dev/null --wrap true 2>&1)
rc=$?
[ $rc -eq 0 ] || fail "sbatch as alice failed (rc=$rc)" "$sub"
jid=${sub%%;*}
[[ $jid =~ ^[0-9]+$ ]] || fail "could not find the job id in sbatch --parsable output" "$sub"
deadline=$((SECONDS + 30))
until state=$(sacct -n -X -P -j "$jid" -o State 2>&1) && [ "$state" = COMPLETED ]; do
    [ "$SECONDS" -lt "$deadline" ] || fail "job $jid not COMPLETED in sacct within 30 s" "$state" "$(scontrol show job "$jid" 2>&1)"
    sleep 0.5
done

echo "ready: exec1 exec2 up, job $jid ran as alice and is COMPLETED in sacct"
