#!/bin/bash
# Per-test cleanup on `login` (TESTBED_PLAN.md section 3):
#   clean.sh [<workdir-under-/work>]
# Deletes every user's jobs, waits until the queue is empty (30 s cap, forced
# deletion after 15 s), then removes the given /work subdirectory.
set -uo pipefail

SUDO=""
[ "$(id -u)" -eq 0 ] || SUDO="sudo"

active() { qstat -u '*' 2>/dev/null | awk 'NR > 2 { n++ } END { print n + 0 }'; }

if [ "$(active)" -gt 0 ]; then
    $SUDO qdel -u '*' 2>&1 | grep -Ev 'no jobs|does not exist' || true
fi

forced=0
deadline=$((SECONDS + 30))
while [ "$(active)" -gt 0 ]; do
    if [ "$SECONDS" -ge "$deadline" ]; then
        echo "clean.sh: jobs still present after 30 s:" >&2
        qstat -u '*' >&2
        exit 1
    fi
    if [ $forced -eq 0 ] && [ "$SECONDS" -ge $((deadline - 15)) ]; then
        $SUDO qdel -f -u '*' >/dev/null 2>&1 || true
        forced=1
    fi
    sleep 1
done

if [ $# -ge 1 ] && [ -n "$1" ]; then
    case $1 in
        /work/*) dir=$1 ;;
        *) dir=/work/$1 ;;
    esac
    [ "$dir" != /work ] && [ "$dir" != /work/cache ] && $SUDO rm -rf -- "$dir"
fi
exit 0
