# syntax=docker/dockerfile:1
# Testbed image for Slurm 24.11 (Debian trixie slurm-wlm 24.11.5). One image
# for the master, exec and login roles; the role comes from SLURM_ROLE at run
# time (entrypoint.sh). Build context is the repo root.
FROM debian:trixie-slim

ENV DEBIAN_FRONTEND=noninteractive \
    UV_PYTHON_INSTALL_DIR=/opt/uv/python \
    UV_LINK_MODE=copy

# policy-rc.d: the packages must not start daemons at build time.
# docker-cli: login drives `docker stop|start|kill <node>` through the socket.
RUN set -e; \
    printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d; chmod +x /usr/sbin/policy-rc.d; \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        slurmctld slurmd slurmdbd slurm-client munge mariadb-client \
        environment-modules python3 bash procps less vim-tiny sudo \
        ca-certificates docker-cli netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

COPY testbed/common/users.sh /opt/testbed/common/users.sh
RUN /opt/testbed/common/users.sh

# uv and one venv per supported Python, as in the SGE images
# (testbed/sge/soge/Dockerfile explains the stub project).
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
COPY testbed/slurm/common/entrypoint.sh testbed/slurm/common/ready.sh testbed/slurm/common/clean.sh /opt/testbed/
COPY testbed/slurm/common/conf/ /opt/testbed/conf/
RUN chmod 0755 /opt/testbed/*.sh /opt/testbed/common/*.sh

ENTRYPOINT ["/opt/testbed/entrypoint.sh"]
