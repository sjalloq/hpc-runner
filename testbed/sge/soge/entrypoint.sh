#!/bin/bash
# Entrypoint for every container of the `soge` stack; SGE_ROLE selects the
# role (master | exec | login). See docs/dev/testbed/TESTBED_PLAN.md section 2.2 and the
# Phase 0 decisions in docs/dev/testbed/TESTBED_REVIEW.md (D-T1..D-T9).
set -euo pipefail

exec > >(tee -a /var/log/testbed.log) 2>&1

export SGE_ROOT=${SGE_ROOT:-/var/lib/gridengine}
export SGE_CELL=${SGE_CELL:-default}
CELL=$SGE_ROOT/$SGE_CELL
SPOOL=/var/spool/gridengine
CONF=/opt/testbed/cell
MARKER_DIR=/run/hpc-testbed
WAIT_FOR=/opt/testbed/common/wait-for.sh
PYTHONS="3.10 3.11 3.12"

log() { echo "testbed[$SGE_ROLE]: $*"; }

as_admin() {
    # sgeadmin's shell is /bin/false; keep SGE_ROOT/SGE_CELL for spooldefaults.
    su -s /bin/bash sgeadmin -c "export SGE_ROOT=$SGE_ROOT SGE_CELL=$SGE_CELL; $*"
}

wait_master() {
    "$WAIT_FOR" 90 1 -- qping -info master 6444 qmaster 1 >/dev/null
}

common_prologue() {
    for d in /work /home; do
        [ -d "$d" ] || { log "mount $d is missing"; exit 1; }
    done
    # /home and /work are named volumes, created empty on first use (D-T8).
    for user in alice bob tester; do
        if [ ! -d "/home/$user" ]; then
            cp -a /etc/skel "/home/$user"
            chown -R "$user:$user" "/home/$user"
        fi
    done
    chmod 1777 /work
    mkdir -p "$MARKER_DIR"
}

# qconf -A<x> adds a new object and fails if it exists; -M<x> replaces an
# existing one and fails if it is missing. `-A || -M` is therefore idempotent
# and picks up edits on the next `up` with a persisted spool (D-T7).
add_or_replace() {
    local kind=$1 file=$2
    qconf -A"$kind" "$file" 2>/dev/null || qconf -M"$kind" "$file"
}

configure_master() {
    local tmp
    tmp=$(mktemp -d)
    # qconf -Mconf names the configuration after the file's basename, so the
    # global configuration lives in cell/global (no suffix) and is copied
    # under that name with the accounting flush time substituted (D-T6/D-T7).
    sed -E "s/flush_time=[0-9:]+/flush_time=${ACCOUNTING_FLUSH:-00:00:01}/" \
        "$CONF/global" > "$tmp/global"
    qconf -Mconf "$tmp/global"
    qconf -Msconf "$CONF/sched.conf"
    qconf -Mc "$CONF/complexes.conf"
    add_or_replace p "$CONF/pe-smp.conf"
    # @allhosts first: the queues reference it and init_cluster does not create it.
    add_or_replace hgrp "$CONF/hg-allhosts.conf"
    add_or_replace q "$CONF/q-all.conf"
    add_or_replace q "$CONF/q-short.conf"
    add_or_replace u "$CONF/userset-arusers.conf"
    # Belt and braces over the offline pre-registration below (D-T3).
    for h in exec1 exec2 login; do qconf -ah "$h" 2>/dev/null || true; done
    for h in exec1 exec2 login; do qconf -as "$h" 2>/dev/null || true; done
    rm -rf "$tmp"
}

run_master() {
    mkdir -p "$CELL/common" "$SPOOL/spooldb" "$SPOOL/qmaster"
    # qmaster rewrites act_qmaster as sgeadmin: create it before the chown.
    echo master > "$CELL/common/act_qmaster"
    chown -R sgeadmin:sgeadmin "$CELL" "$SPOOL"

    if [ ! -e "$SPOOL/spooldb/sge" ]; then
        log "fresh spool: running init_cluster"
        as_admin "/usr/share/gridengine/scripts/init_cluster $SGE_ROOT $SGE_CELL $SPOOL/spooldb sgeadmin"
        # Pre-register the hosts offline so execd never meets a master that
        # does not know it yet; execd exits after 5 s in that case and does
        # not retry (D-T3). spooldefaults takes a directory with one file per
        # host.
        local tmp
        tmp=$(mktemp -d)
        mkdir -p "$tmp/adminhosts" "$tmp/submithosts"
        # login is an admin host too: every `qconf -s*` read (ready.sh step 3,
        # the fixture) is refused from a plain submit host.
        for h in exec1 exec2 login; do echo "hostname $h" > "$tmp/adminhosts/$h"; done
        for h in exec1 exec2 login; do echo "hostname $h" > "$tmp/submithosts/$h"; done
        chown -R sgeadmin "$tmp"
        as_admin "/usr/lib/gridengine/spooldefaults adminhosts $tmp/adminhosts"
        as_admin "/usr/lib/gridengine/spooldefaults submithosts $tmp/submithosts"
        rm -rf "$tmp"
    else
        log "spool exists: init_cluster skipped"
    fi

    # Foreground qmaster (SGE_ND); forward TERM/INT so `compose stop` gets a
    # controlled shutdown instead of the grace-period SIGKILL (D-T5).
    SGE_ND=1 /usr/sbin/sge_qmaster &
    local qpid=$!
    trap 'kill -TERM "$qpid" 2>/dev/null' TERM INT

    wait_master
    configure_master
    touch "$MARKER_DIR/configured"
    log "master configured"

    local rc=0
    while kill -0 "$qpid" 2>/dev/null; do
        wait "$qpid" && rc=0 || rc=$?
    done
    log "qmaster exited rc=$rc"
    exit "$rc"
}

run_exec() {
    wait_master
    mkdir -p "$SPOOL/execd"
    chown sgeadmin:sgeadmin "$SPOOL/execd"
    # Started as root (qrsh needs it); execd itself switches to sgeadmin for
    # everything but job launch.
    exec env SGE_ND=1 /usr/sbin/sge_execd
}

run_login() {
    wait_master
    install -d -m 1777 /work/cache /work/cache/scripts /work/cache/exit
    # /src is read-only: hatch-vcs' version-file hook would write
    # src/hpc_runner/_version.py, so hooks are off and the version is fixed.
    # No network at start-up: hatchling, hatch-vcs and editables are in the
    # venv already (image build), so the build runs without isolation and
    # offline.
    for v in $PYTHONS; do
        log "installing /src into /opt/venv/$v (editable, no deps)"
        HATCH_BUILD_NO_HOOKS=true SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0+testbed \
            uv pip install --quiet --offline --no-build-isolation \
            --python "/opt/venv/$v" -e /src --no-deps
    done
    touch "$MARKER_DIR/configured"
    log "login ready"
    exec sleep infinity
}

common_prologue
case "${SGE_ROLE:-}" in
    master) run_master ;;
    exec)   run_exec ;;
    login)  run_login ;;
    *) log "SGE_ROLE must be master, exec or login (got '${SGE_ROLE:-}')"; exit 2 ;;
esac
