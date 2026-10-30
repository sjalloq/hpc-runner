# syntax=docker/dockerfile:1
# Testbed image for the `soge` SGE flavour: Debian trixie gridengine-* 8.1.9
# (Son of Grid Engine lineage). One image for every role; the role comes from
# SGE_ROLE at run time (see entrypoint.sh). Build context is the repo root
# (compose.yml sets it) so pyproject.toml can be copied in.
FROM debian:trixie-slim

ENV DEBIAN_FRONTEND=noninteractive \
    SGE_ROOT=/var/lib/gridengine \
    SGE_CELL=default \
    UV_PYTHON_INSTALL_DIR=/opt/uv/python \
    UV_LINK_MODE=copy

# 1. Preseed so the gridengine postinst does not run init_cluster at build time
#    (it would bake the build hostname in); the entrypoint runs it on the
#    volumes instead.
# 2. `ssmtp` + `bsd-mailx` satisfy gridengine-common's `mailx` dependency
#    without an MTA daemon (TESTBED_REVIEW.md D-T1; `s-nail` no longer
#    Provides: mailx on trixie).
# 3. docker-cli: the login container drives `docker stop|start|kill <node>`
#    through the mounted socket for the failure tests (D-T9).
RUN set -e; \
    for owner in gridengine-common gridengine-master gridengine-client; do \
        echo "$owner shared/gridengineconfig boolean false"; \
        echo "$owner shared/gridenginecell string default"; \
        echo "$owner shared/gridenginemaster string master"; \
    done | debconf-set-selections \
    && apt-get update \
    && apt-get install -y --no-install-recommends ssmtp bsd-mailx \
    && apt-get install -y --no-install-recommends \
        gridengine-master gridengine-exec gridengine-client gridengine-common \
        environment-modules python3 bash procps less vim-tiny sudo \
        ca-certificates docker-cli \
    && rm -rf /var/lib/apt/lists/*

COPY testbed/common/users.sh /opt/testbed/common/users.sh
RUN /opt/testbed/common/users.sh

# uv and one venv per supported Python, with hpc-runner's `test` extra
# preinstalled from pyproject.toml (so the image is rebuilt when dependencies
# change). The deps come in through a stub project that carries only the real
# pyproject.toml: it installs a placeholder `hpc-runner` 0.0.0+testbed plus
# every dependency; the login entrypoint then replaces the placeholder with an
# editable install of /src (--no-deps), so source edits on the host are live.
# hatchling, hatch-vcs and `editables` are what that editable build needs; it
# runs offline and without build isolation (the stack's network has no DNS).
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
COPY testbed/sge/soge/entrypoint.sh testbed/sge/soge/ready.sh testbed/sge/soge/clean.sh /opt/testbed/
COPY testbed/sge/soge/cell/ /opt/testbed/cell/
RUN chmod 0755 /opt/testbed/*.sh /opt/testbed/common/*.sh

ENTRYPOINT ["/opt/testbed/entrypoint.sh"]
