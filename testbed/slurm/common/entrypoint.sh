#!/bin/bash
# Entrypoint for the master, exec and login containers of every Slurm flavour;
# SLURM_ROLE selects the role. docs/dev/slurm/PLAN.md, "Testbed".
set -euo pipefail

exec > >(tee -a /var/log/testbed.log) 2>&1

CONF=/opt/testbed/conf
MARKER_DIR=/run/hpc-testbed
WAIT_FOR=/opt/testbed/common/wait-for.sh
PYTHONS="3.10 3.11 3.12"
KEY=/etc/munge/munge.key

log() { echo "testbed[$SLURM_ROLE]: $*"; }

common_prologue() {
    for d in /work /home /etc/munge; do
        [ -d "$d" ] || { log "mount $d is missing"; exit 1; }
    done
    for user in alice bob tester; do
        if [ ! -d "/home/$user" ]; then
            cp -a /etc/skel "/home/$user"
            chown -R "$user:$user" "/home/$user"
        fi
    done
    chmod 1777 /work
    mkdir -p "$MARKER_DIR" /etc/slurm
    cp "$CONF/slurm.conf" "$CONF/gres.conf" /etc/slurm/
    # `CgroupPlugin=disabled` exists from 23.02. 22.05 fails on it ("cannot
    # find cgroup plugin for disabled") and its default, cgroup/v2, needs
    # dbus; cgroup/v1 loads and is then never used (as vhpc does).
    case "$(sinfo --version)" in
        *" 22."*) cp "$CONF/cgroup-22.05.conf" /etc/slurm/cgroup.conf ;;
        *) cp "$CONF/cgroup.conf" /etc/slurm/ ;;
    esac
}

# /etc/munge is a volume shared by the stack; the master writes the key once.
start_munge() {
    if [ "$SLURM_ROLE" = master ] && [ ! -s "$KEY" ]; then
        log "generating the munge key"
        head -c 1024 /dev/urandom > "$KEY"
    fi
    "$WAIT_FOR" 60 1 -- test -s "$KEY" >/dev/null
    chown munge:munge /etc/munge "$KEY" 2>/dev/null || true
    chmod 0700 /etc/munge; chmod 0400 "$KEY"
    install -d -o munge -g munge -m 0755 /run/munge
    install -d -o munge -g munge -m 0700 /var/lib/munge /var/log/munge
    runuser -u munge -- /usr/sbin/munged
    "$WAIT_FOR" 20 1 -- munge -n >/dev/null
}

wait_ctld() { "$WAIT_FOR" 120 1 -- scontrol ping >/dev/null; }

run_master() {
    install -o slurm -g slurm -m 0600 "$CONF/slurmdbd.conf" /etc/slurm/slurmdbd.conf
    install -d -o slurm -g slurm /var/spool/slurmctld
    "$WAIT_FOR" 120 1 -- nc -z db 3306 >/dev/null
    /usr/sbin/slurmdbd -D &
    local dpid=$!
    "$WAIT_FOR" 60 1 -- sacctmgr -n show cluster >/dev/null
    sacctmgr -i add cluster testbed >/dev/null 2>&1 || true

    /usr/sbin/slurmctld -D &
    local cpid=$!
    trap 'kill -TERM "$cpid" "$dpid" 2>/dev/null' TERM INT
    wait_ctld
    # Accounts and users, so `project` has something to name.
    sacctmgr -i add account proj Description=testbed Organization=testbed >/dev/null
    sacctmgr -i add account other Description=testbed Organization=testbed >/dev/null
    for u in alice bob tester; do
        sacctmgr -i add user "$u" Account=proj >/dev/null
    done
    touch "$MARKER_DIR/configured"
    log "master configured"

    local rc=0
    while kill -0 "$cpid" 2>/dev/null; do
        wait "$cpid" && rc=0 || rc=$?
    done
    log "slurmctld exited rc=$rc"
    kill -TERM "$dpid" 2>/dev/null || true
    exit "$rc"
}

run_exec() {
    wait_ctld
    install -d /var/spool/slurmd
    exec /usr/sbin/slurmd -D
}

run_login() {
    wait_ctld
    install -d -m 1777 /work/cache /work/cache/scripts /work/cache/exit
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
start_munge
case "${SLURM_ROLE:-}" in
    master) run_master ;;
    exec)   run_exec ;;
    login)  run_login ;;
    *) log "SLURM_ROLE must be master, exec or login (got '${SLURM_ROLE:-}')"; exit 2 ;;
esac
