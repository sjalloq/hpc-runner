#!/bin/bash
# Entrypoint for every container of the `ocs` stack; SGE_ROLE selects the
# role (master | exec | login). Same shape as testbed/sge/soge/entrypoint.sh;
# what differs is how the cell is created (inst_sge instead of Debian's
# init_cluster) and where it lives: the `cell` volume at /opt/ocs-cell, with
# /opt/ocs/default a symlink to it. inst_sge refuses any existing cell
# directory, a mount point included, so the master installs into the image's
# /opt/ocs/default and then moves the cell onto the volume.
set -euo pipefail

exec > >(tee -a /var/log/testbed.log) 2>&1

CELL=$SGE_ROOT/$SGE_CELL
CELL_VOLUME=/opt/ocs-cell
EXECD_SPOOL=/var/spool/ocs/execd
CONF=/opt/testbed/cell
MARKER_DIR=/run/hpc-testbed
WAIT_FOR=/opt/testbed/common/wait-for.sh
PYTHONS="3.10 3.11 3.12"

log() { echo "testbed[$SGE_ROLE]: $*"; }

wait_master() {
    "$WAIT_FOR" 90 1 -- qping -info master 6444 qmaster 1 >/dev/null
}

common_prologue() {
    for d in /work /home "$CELL_VOLUME"; do
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

link_cell() {
    [ -L "$CELL" ] || ln -s "$CELL_VOLUME" "$CELL"
}

# qconf -A<x> adds a new object and fails if it exists; -M<x> replaces an
# existing one and fails if it is missing (D-T7).
add_or_replace() {
    local kind=$1 file=$2
    qconf -A"$kind" "$file" 2>/dev/null || qconf -M"$kind" "$file"
}

install_cell() {
    # inst_sge's auto mode reads every answer from this template. Only the
    # master is installed here: hosts in EXEC_HOST_LIST would be installed
    # over ssh. The exec hosts are admin hosts, so their execd registers on
    # first contact.
    local template
    template=$(mktemp)
    cat > "$template" <<TEMPLATE
SGE_ROOT="$SGE_ROOT"
SGE_QMASTER_PORT="$SGE_QMASTER_PORT"
SGE_EXECD_PORT="$SGE_EXECD_PORT"
SGE_ENABLE_SMF="false"
SGE_CLUSTER_NAME="$SGE_CLUSTER_NAME"
CELL_NAME="$SGE_CELL"
ADMIN_USER="root"
QMASTER_SPOOL_DIR="$CELL/spool/qmaster"
EXECD_SPOOL_DIR="$EXECD_SPOOL"
GID_RANGE="20000-20200"
SPOOLING_METHOD="classic"
DB_SPOOLING_DIR="$CELL/spool/bdb"
PAR_EXECD_INST_COUNT="20"
ADMIN_HOST_LIST="master exec1 exec2 login"
SUBMIT_HOST_LIST="exec1 exec2 login"
EXEC_HOST_LIST=""
EXECD_SPOOL_DIR_LOCAL=""
HOSTNAME_RESOLVING="true"
SHELL_NAME="ssh"
COPY_COMMAND="scp"
DEFAULT_DOMAIN="none"
ADMIN_MAIL="none"
ADD_TO_RC="false"
SET_FILE_PERMS="true"
RESCHEDULE_JOBS="wait"
SCHEDD_CONF="1"
SHADOW_HOST=""
EXEC_HOST_LIST_RM=""
REMOVE_RC="false"
CSP_RECREATE="true"
CSP_COPY_CERTS="false"
CSP_COUNTRY_CODE="DE"
CSP_STATE="Germany"
CSP_LOCATION="Building"
CSP_ORGA="Organisation"
CSP_ORGA_UNIT="Organisation_unit"
CSP_MAIL_ADDRESS="none@example.invalid"
TEMPLATE
    # No -munge/-tls: 9.1.7 turns both on only when asked.
    (cd "$SGE_ROOT" && ./inst_sge -m -auto "$template")
    rm -f "$template"
    # inst_sge leaves its own qmaster running; this entrypoint runs it in the
    # foreground instead.
    "$CELL/common/sgemaster" stop || true
    "$WAIT_FOR" 30 1 -- sh -c '! pgrep -x sge_qmaster >/dev/null'
    cp -a "$CELL/." "$CELL_VOLUME/"
    rm -rf "$CELL"
}

configure_master() {
    local tmp
    tmp=$(mktemp -d)
    sed -E "s/flush_time=[0-9:]+/flush_time=${ACCOUNTING_FLUSH:-00:00:01}/" \
        "$CONF/global" > "$tmp/global"
    qconf -Mconf "$tmp/global"
    qconf -Msconf "$CONF/sched.conf"
    qconf -Mc "$CONF/complexes.conf"
    add_or_replace p "$CONF/pe-smp.conf"
    add_or_replace hgrp "$CONF/hg-allhosts.conf"
    add_or_replace q "$CONF/q-all.conf"
    add_or_replace q "$CONF/q-short.conf"
    add_or_replace u "$CONF/userset-arusers.conf"
    for h in exec1 exec2 login; do qconf -ah "$h" 2>/dev/null || true; done
    for h in exec1 exec2 login; do qconf -as "$h" 2>/dev/null || true; done
    rm -rf "$tmp"
}

run_master() {
    if [ ! -e "$CELL_VOLUME/common/bootstrap" ]; then
        log "fresh cell: running inst_sge"
        install_cell
    else
        log "cell exists: inst_sge skipped"
    fi
    link_cell

    # Foreground qmaster (SGE_ND); forward TERM/INT so `compose stop` gets a
    # controlled shutdown (D-T5).
    SGE_ND=1 sge_qmaster &
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
    link_cell
    wait_master
    mkdir -p "$EXECD_SPOOL"
    exec env SGE_ND=1 sge_execd
}

run_login() {
    link_cell
    wait_master
    install -d -m 1777 /work/cache /work/cache/scripts /work/cache/exit
    # As in the soge stack: editable, offline, no build isolation, no hooks.
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
