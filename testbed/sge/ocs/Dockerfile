# syntax=docker/dockerfile:1
# Testbed image for the `ocs` SGE flavour: Open Cluster Scheduler 9.1.7
# (Univa lineage, HPC-Gridware's binary tarballs) on Debian trixie, so the
# users, Environment Modules and Python venvs match the `soge` image. One
# image for every role; the role comes from SGE_ROLE at run time (see
# entrypoint.sh). Build context is the repo root (compose.yml sets it).
FROM debian:trixie-slim

ARG OCS_VERSION=9.1.7
ENV DEBIAN_FRONTEND=noninteractive \
    SGE_ROOT=/opt/ocs \
    SGE_CELL=default \
    SGE_CLUSTER_NAME=p6444 \
    SGE_QMASTER_PORT=6444 \
    SGE_EXECD_PORT=6445 \
    PATH=/opt/ocs/bin/lx-amd64:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    UV_PYTHON_INSTALL_DIR=/opt/uv/python \
    UV_LINK_MODE=copy

# procps: inst_sge and util/arch_variables need ps. docker-cli: the login
# container drives `docker stop|start|kill <node>` through the mounted socket
# for the failure tests (D-T9). No MTA: mailer is NONE in cell/global.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        environment-modules python3 bash procps less vim-tiny sudo hostname \
        binutils ca-certificates docker-cli \
    && rm -rf /var/lib/apt/lists/*

# The tarballs are not checksummed upstream; these are the sums of the files
# downloaded on 2026-11-02, so a changed upload fails the build.
ADD --checksum=sha256:c24021e991103e609f8d333fcc4b4db7bbad1e8c3eec0cff76d2b647db15fc8f \
    https://open.clusterscheduler.io/download/ocs-${OCS_VERSION}/ocs-${OCS_VERSION}-bin-lx-amd64.tar.gz /tmp/ocs-bin.tar.gz
ADD --checksum=sha256:20fa568ea9c278e4e1b43957ee036f9e74e0cd75440e7f04c35815e2e32aede8 \
    https://open.clusterscheduler.io/download/ocs-${OCS_VERSION}/ocs-${OCS_VERSION}-common.tar.gz /tmp/ocs-common.tar.gz
RUN mkdir -p /opt/ocs \
    && tar xzf /tmp/ocs-common.tar.gz -C /opt/ocs \
    && tar xzf /tmp/ocs-bin.tar.gz -C /opt/ocs \
    && rm /tmp/ocs-*.tar.gz

# sudo resets PATH and the environment: keep the OCS binaries and the cell
# variables for `sudo -u alice qsub ...` (the fixture's run()).
RUN printf '%s\n' \
        'Defaults secure_path="/opt/ocs/bin/lx-amd64:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"' \
        'Defaults env_keep += "SGE_ROOT SGE_CELL SGE_CLUSTER_NAME SGE_QMASTER_PORT SGE_EXECD_PORT"' \
        > /etc/sudoers.d/ocs \
    && chmod 0440 /etc/sudoers.d/ocs \
    && printf '%s\n' '. /opt/ocs/default/common/settings.sh 2>/dev/null || true' > /etc/profile.d/ocs.sh

COPY testbed/common/users.sh /opt/testbed/common/users.sh
RUN /opt/testbed/common/users.sh

# uv and one venv per supported Python with hpc-runner's `test` extra, as in
# testbed/sge/soge/Dockerfile (the login entrypoint adds the editable /src).
COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /uvx /usr/local/bin/
RUN uv python install 3.10 3.11 3.12 \
    && for v in 3.10 3.11 3.12; do uv venv --python "$v" "/opt/venv/$v"; done

COPY pyproject.toml /opt/testbed/stub/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv \
    set -e; cd /opt/testbed/stub; \
    mkdir -p src/hpc_runner; touch src/hpc_runner/__init__.py README.md; \
    for v in 3.10 3.11 3.12; do \
        SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0+testbed \
        uv pip install --python "/opt/venv/$v" "/opt/testbed/stub[test]" \
            hatchling hatch-vcs editables; \
    done

COPY testbed/common/ /opt/testbed/common/
COPY testbed/sge/ocs/entrypoint.sh testbed/sge/common/ready.sh testbed/sge/common/clean.sh /opt/testbed/
COPY testbed/sge/ocs/cell/ /opt/testbed/cell/
RUN chmod 0755 /opt/testbed/*.sh /opt/testbed/common/*.sh

ENTRYPOINT ["/opt/testbed/entrypoint.sh"]
