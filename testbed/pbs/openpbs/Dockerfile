# syntax=docker/dockerfile:1
# Testbed image for PBS: OpenPBS v23.06.06 built from source on Rocky Linux 8
# (OpenPBS publishes no current packages). Rocky 8 because the release
# includes Python's eval.h, which Python 3.11 removed (Debian bookworm fails
# on it) and Debian bullseye's package archive no longer resolves. One image
# for the master, exec and login roles; the role comes from PBS_ROLE at run
# time (entrypoint.sh). Build context is the repo root.
FROM rockylinux:8 AS build

# The CentOS-8 list from OpenPBS's INSTALL.
RUN dnf install -y dnf-plugins-core && dnf config-manager --set-enabled powertools \
    && dnf install -y gcc make rpm-build libtool hwloc-devel libX11-devel \
        libXt-devel libedit-devel libical-devel ncurses-devel perl \
        postgresql-devel postgresql-contrib python3-devel tcl-devel tk-devel \
        swig expat-devel openssl-devel libXext libXft autoconf automake \
        gcc-c++ \
    && dnf clean all
ARG OPENPBS_VERSION=23.06.06
RUN curl -fsSL "https://github.com/openpbs/openpbs/archive/refs/tags/v${OPENPBS_VERSION}.tar.gz" \
        | tar -xz -C /tmp \
    && cd "/tmp/openpbs-${OPENPBS_VERSION}" \
    && ./autogen.sh \
    && ./configure --prefix=/opt/pbs \
    && make -j"$(nproc)" \
    && make install DESTDIR=/out

FROM rockylinux:8

ENV UV_PYTHON_INSTALL_DIR=/opt/uv/python \
    UV_LINK_MODE=copy \
    PATH=/opt/pbs/bin:/opt/pbs/sbin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Run-time list from INSTALL (CentOS), without sendmail.
RUN dnf install -y expat libedit postgresql-server postgresql-contrib python3 \
        sudo tcl tk libical hwloc-libs libXt libXft libXext libX11 \
        environment-modules procps-ng less vim-minimal hostname which \
        openssh-clients util-linux findutils \
    && dnf clean all
COPY --from=build /out/ /
COPY --from=docker:28-cli /usr/local/bin/docker /usr/local/bin/docker

COPY testbed/common/users.sh /opt/testbed/common/users.sh
RUN /opt/testbed/common/users.sh

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
COPY testbed/pbs/openpbs/entrypoint.sh testbed/pbs/common/ready.sh testbed/pbs/common/clean.sh /opt/testbed/
RUN chmod 0755 /opt/testbed/*.sh /opt/testbed/common/*.sh

ENTRYPOINT ["/opt/testbed/entrypoint.sh"]
