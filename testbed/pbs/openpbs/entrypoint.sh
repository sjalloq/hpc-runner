#!/bin/bash
# Entrypoint for the master, exec and login containers of the OpenPBS stack;
# PBS_ROLE selects the role. docs/dev/slurm/PLAN.md, "Order of work".
set -euo pipefail

exec > >(tee -a /var/log/testbed.log) 2>&1

MARKER_DIR=/run/hpc-testbed
WAIT_FOR=/opt/testbed/common/wait-for.sh
PYTHONS="3.10 3.11 3.12"
PBS_EXEC=/opt/pbs
PBS_HOME=/var/spool/pbs

log() { echo "testbed[$PBS_ROLE]: $*"; }

common_prologue() {
    for d in /work /home; do
        [ -d "$d" ] || { log "mount $d is missing"; exit 1; }
    done
    for user in alice bob tester; do
        if [ ! -d "/home/$user" ]; then
            cp -a /etc/skel "/home/$user"
            chown -R "$user:$user" "/home/$user"
        fi
    done
    chmod 1777 /work
    mkdir -p "$MARKER_DIR"
}

# pbs_postinstall writes /etc/pbs.conf for the role and creates PBS_HOME.
postinstall() {
    PBS_SERVER=master PBS_EXEC=$PBS_EXEC PBS_HOME=$PBS_HOME \
        "$PBS_EXEC/libexec/pbs_postinstall" "$1"
    # Every daemon reaches the others through pbs_comm on master.
    grep -q '^PBS_LEAF_ROUTERS=' /etc/pbs.conf || echo "PBS_LEAF_ROUTERS=master" >> /etc/pbs.conf
}

wait_server() { "$WAIT_FOR" 120 1 -- qstat -B >/dev/null; }

run_master() {
    postinstall server
    "$PBS_EXEC/libexec/pbs_init.d" start
    wait_server
    # flatuid: a user name is the same account on every host. History: a
    # finished job stays visible to `qstat -x`.
    qmgr -c "set server flatuid = True"
    qmgr -c "set server job_history_enable = True"
    qmgr -c "set server acl_roots += root@*"
    # Something for each kind of request to name: scratch space and a GPU
    # count (per node, consumable), a feature (per node, boolean) and a
    # licence (server-wide, consumable). OpenPBS 23.06.06 has no built-in
    # `ngpus`. The scheduler only looks at resources named in sched_config.
    qmgr -c "create resource scratch type=size,flag=nh" 2>/dev/null || true
    qmgr -c "create resource ngpus type=long,flag=nh" 2>/dev/null || true
    qmgr -c "create resource fast type=boolean,flag=h" 2>/dev/null || true
    qmgr -c "create resource lic type=long,flag=q" 2>/dev/null || true
    qmgr -c "set server resources_available.lic = 2"
    qmgr -c "set server scheduler_iteration = 20"
    for h in exec1 exec2; do
        qmgr -c "create node $h" 2>/dev/null || true
        qmgr -c "set node $h resources_available.ncpus = 2"
        qmgr -c "set node $h resources_available.scratch = 1gb"
    done
    qmgr -c "set node exec1 resources_available.fast = True"
    qmgr -c "set node exec1 resources_available.ngpus = 1"
    if ! grep -q '^resources:.*scratch' "$PBS_HOME/sched_priv/sched_config"; then
        sed -i 's/^resources: "\(.*\)"/resources: "\1, ngpus, scratch, lic, fast"/' \
            "$PBS_HOME/sched_priv/sched_config"
        kill -HUP "$(pgrep -x pbs_sched)"
    fi
    touch "$MARKER_DIR/configured"
    log "master configured"
    trap '"$PBS_EXEC/libexec/pbs_init.d" stop; exit 0' TERM INT
    tail -F "$PBS_HOME"/server_logs/* &
    wait $!
}

run_exec() {
    postinstall execution
    # /home and /work are shared: copy output locally, never over scp.
    cat >> "$PBS_HOME/mom_priv/config" <<'CFG'
$usecp *:/home/ /home/
$usecp *:/work/ /work/
$usecp *:/dev/null /dev/null
CFG
    wait_server
    "$PBS_EXEC/libexec/pbs_init.d" start
    trap '"$PBS_EXEC/libexec/pbs_init.d" stop; exit 0' TERM INT
    tail -F "$PBS_HOME"/mom_logs/* &
    wait $!
}

run_login() {
    postinstall client
    wait_server
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
case "${PBS_ROLE:-}" in
    master) run_master ;;
    exec)   run_exec ;;
    login)  run_login ;;
    *) log "PBS_ROLE must be master, exec or login (got '${PBS_ROLE:-}')"; exit 2 ;;
esac
