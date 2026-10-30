#!/bin/bash
# Poll a command until it succeeds.
#
#   wait-for.sh <timeout-seconds> <interval-seconds> -- <command> [args...]
#
# Exits 0 as soon as the command exits 0. On timeout, prints the command's
# last output to stderr and exits 1, so the caller's failure message shows
# what the probe last saw.
set -uo pipefail

usage() { echo "usage: $0 <timeout-s> <interval-s> -- <command> [args...]" >&2; exit 2; }

[ $# -ge 4 ] || usage
timeout=$1
interval=$2
[ "$3" = "--" ] || usage
shift 3

deadline=$((SECONDS + timeout))
last=""
while :; do
    if last=$("$@" 2>&1); then
        exit 0
    fi
    if [ "$SECONDS" -ge "$deadline" ]; then
        echo "wait-for: '$*' did not succeed within ${timeout}s; last output:" >&2
        printf '%s\n' "$last" >&2
        exit 1
    fi
    sleep "$interval"
done
