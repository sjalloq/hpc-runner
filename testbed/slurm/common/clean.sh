#!/bin/bash
# Per-test cleanup on `login`:  clean.sh [<workdir-under-/work>]
# Cancels every user's jobs, waits until squeue is empty (30 s cap), then
# removes the given /work subdirectory.
set -uo pipefail

SUDO=""
[ "$(id -u)" -eq 0 ] || SUDO="sudo"

active() { squeue -h -o %i 2>/dev/null | wc -l; }

if [ "$(active)" -gt 0 ]; then
    for u in alice bob tester; do $SUDO scancel -u "$u" 2>/dev/null || true; done
fi

deadline=$((SECONDS + 30))
while [ "$(active)" -gt 0 ]; do
    if [ "$SECONDS" -ge "$deadline" ]; then
        echo "clean.sh: jobs still present after 30 s:" >&2
        squeue >&2
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
