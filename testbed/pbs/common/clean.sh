#!/bin/bash
# Per-test cleanup on `login`:  clean.sh [<workdir-under-/work>]
# Deletes every user's jobs, waits until none is left (30 s cap), then
# removes the given /work subdirectory.
set -uo pipefail

SUDO=""
[ "$(id -u)" -eq 0 ] || SUDO="sudo"

active() { $SUDO /opt/pbs/bin/qselect 2>/dev/null | wc -l; }

if [ "$(active)" -gt 0 ]; then
    $SUDO /opt/pbs/bin/qselect | xargs -r $SUDO /opt/pbs/bin/qdel -W force >/dev/null 2>&1 || true
fi

deadline=$((SECONDS + 30))
while [ "$(active)" -gt 0 ]; do
    if [ "$SECONDS" -ge "$deadline" ]; then
        echo "clean.sh: jobs still present after 30 s:" >&2
        qstat -a >&2
        exit 1
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
