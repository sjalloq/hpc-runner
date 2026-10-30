"""The job environment on the node: modules, venv, env edits, passthrough, cwd (D3, B4)."""

from __future__ import annotations

import subprocess

import pytest

from hpc_runner.core.resolve import resolve
from hpc_runner.core.result import JobResult, JobStatus
from hpc_runner.core.spec import JobSpec
from tests.integration.cluster import Cluster

MODULES_PATH = "/opt/modulefiles"
#: ``tests/modulefiles/dummy_tool/1.0`` sets these; ``dummy_lib/2.5`` sets DUMMY_LIB_*.
SHOW_MODULES = (
    'printf "%s\\n" "tool=${DUMMY_TOOL_VERSION-unset}" "lib=${DUMMY_LIB_VERSION-unset}" '
    '"loaded=${LOADEDMODULES-unset}"'
)
VENV_311 = "/opt/venv/3.11"
PRINT_PREFIX = "python3 -c 'import sys; print(sys.prefix)'"
#: Quoting-hostile value (B4): spaces, both quote kinds, ``$``, backticks, backslash,
#: a semicolon. Written with ``$$`` (D5: resolve expands ``$VAR`` from the submit
#: environment, ``$$`` is a literal ``$``); the job must see ``$HOME`` unexpanded.
HOSTILE_SPEC = "a b 'c' \"d\" $$HOME `id` \\n ;rm"
HOSTILE = HOSTILE_SPEC.replace("$$", "$")


def _run(cluster: Cluster, spec: JobSpec) -> list[str]:
    """Submit *spec*, wait for COMPLETED, return the job's stdout lines."""
    result: JobResult = cluster.scheduler().submit(resolve(spec, cluster.config(), cluster.host()))
    assert result.job_id is not None
    assert cluster.wait_for(result.job_id, JobStatus.COMPLETED) is JobStatus.COMPLETED
    stdout = result.read_stdout()
    assert stdout is not None
    return stdout.splitlines()


def _module_shell_env(module: str) -> dict[str, str]:
    """The environment of an interactive shell that ran ``module load <module>``."""
    script = (
        f". /etc/profile.d/modules.sh && module use {MODULES_PATH} && "
        f"module load {module} && env -0"
    )
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True).stdout
    env: dict[str, str] = {}
    for item in out.split("\0"):
        if "=" in item:
            key, _, value = item.partition("=")
            env[key] = value
    return env


def test_modules_on_node(cluster: Cluster) -> None:
    """D3: modules + modules_path load dummy_tool on the node; the job sees what it provides."""
    spec = JobSpec(SHOW_MODULES, name="mods", modules=["dummy_tool"], modules_path=[MODULES_PATH])
    lines = _run(cluster, spec)
    assert "tool=1.0" in lines
    assert "lib=unset" in lines
    assert any(line.startswith("loaded=") and "dummy_tool/1.0" in line for line in lines), lines


def test_regardless_of_submitter(cluster: Cluster, monkeypatch: pytest.MonkeyPatch) -> None:
    """D3: a submit shell with dummy_lib loaded does not leak it; the job sees dummy_tool only."""
    shell_env = _module_shell_env("dummy_lib")
    assert shell_env.get("DUMMY_LIB_VERSION") == "2.5", "submit shell did not load dummy_lib"
    for key, value in shell_env.items():
        monkeypatch.setenv(key, value)

    spec = JobSpec(SHOW_MODULES, name="purge", modules=["dummy_tool"], modules_path=[MODULES_PATH])
    lines = _run(cluster, spec)
    assert "tool=1.0" in lines
    assert "lib=unset" in lines
    loaded = next(line for line in lines if line.startswith("loaded="))
    assert "dummy_lib" not in loaded and "dummy_tool/1.0" in loaded


def test_hpc_cpus(cluster: Cluster) -> None:
    """D5: ${hpc.cpus} in env_vars becomes the slot count on the node."""
    spec = JobSpec('echo "$MAKEFLAGS"', name="cpus", cpu=2, env_vars={"MAKEFLAGS": "-j${hpc.cpus}"})
    assert _run(cluster, spec) == ["-j2"]


def test_env_order(cluster: Cluster, monkeypatch: pytest.MonkeyPatch) -> None:
    """B4: unset, prepend, append, set apply in that order; quoting-hostile values survive."""
    monkeypatch.setenv("HPC_T_BASE", "base")
    monkeypatch.setenv("HPC_T_GONE", "still here")
    spec = JobSpec(
        'printf "%s\\n" "$HPC_T_BASE" "$HPC_T_SET" "${HPC_T_GONE-unset}" "${HPC_T_NEW-unset}"',
        name="envorder",
        env_unset=["HPC_T_GONE"],
        env_prepend={"HPC_T_BASE": f"pre {HOSTILE_SPEC}", "HPC_T_NEW": "first"},
        env_append={"HPC_T_BASE": f"post {HOSTILE_SPEC}"},
        env_vars={"HPC_T_SET": HOSTILE_SPEC},
    )
    assert _run(cluster, spec) == [
        f"pre {HOSTILE}:base:post {HOSTILE}",
        HOSTILE,
        "unset",
        "first",
    ]


def test_venv(cluster: Cluster, monkeypatch: pytest.MonkeyPatch) -> None:
    """A VIRTUAL_ENV on the submit side is the job's python; venv=False leaves the system python."""
    monkeypatch.setenv("VIRTUAL_ENV", VENV_311)
    assert _run(cluster, JobSpec(PRINT_PREFIX, name="venv")) == [VENV_311]
    assert _run(cluster, JobSpec(PRINT_PREFIX, name="novenv", venv=False)) == ["/usr"]


def test_inherit_env_false(cluster: Cluster, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without -V only env_passthrough names reach the job."""
    monkeypatch.setenv("DISPLAY", ":9")
    monkeypatch.setenv("HPC_T_MARKER_NOT_PASSED", "1")
    spec = JobSpec(
        'printf "%s\\n" "${DISPLAY-unset}" "${HPC_T_MARKER_NOT_PASSED-unset}"',
        name="nov",
        inherit_env=False,
        env_passthrough=["DISPLAY"],
    )
    job = resolve(spec, cluster.config(), cluster.host())
    preview = cluster.scheduler().preview(job)
    assert "#$ -V" not in preview.splitlines()
    assert "#$ -v DISPLAY=:9" in preview.splitlines()
    assert _run(cluster, spec) == [":9", "unset"]


def _check_cwd(cluster: Cluster, name: str) -> None:
    """A job with ``workdir=<name>`` under the test dir runs there and writes its output there."""
    sub = cluster.workdir / name
    sub.mkdir()
    spec = JobSpec("pwd", name="cwd", workdir=name)
    job = resolve(spec, cluster.config(), cluster.host())
    assert job.workdir == str(sub)
    assert "#$ -cwd" in cluster.scheduler().preview(job).splitlines()
    result = cluster.scheduler().submit(job)
    assert result.job_id is not None
    assert cluster.wait_for(result.job_id, JobStatus.COMPLETED) is JobStatus.COMPLETED
    assert result.stdout_path is not None and result.stdout_path.parent == sub
    stdout = result.read_stdout()
    assert stdout is not None and stdout.strip() == str(sub)


def test_cwd(cluster: Cluster) -> None:
    """-cwd with a workdir under /work: the job runs there and its output lands there."""
    _check_cwd(cluster, "subdir")


@pytest.mark.xfail(
    strict=True,
    reason="BUG-T1: #$ -o path with a space is split by the directive parser (TESTBED_REVIEW.md)",
)
def test_cwd_with_space(cluster: Cluster) -> None:
    """BUG-T1: a workdir containing a space is submitted and used as-is."""
    _check_cwd(cluster, "sub dir")
