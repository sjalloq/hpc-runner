"""afterok/afternotok/afterany on SGE: -hold_jid plus the exit-sentinel guard (D8)."""

from __future__ import annotations

import re

from click.testing import CliRunner

from hpc_runner.cli.main import cli
from hpc_runner.core.dependency import DependencyType
from hpc_runner.core.resolve import resolve
from hpc_runner.core.result import HPC_EXIT_DEP_FAILED, ArrayJobResult, JobResult, JobStatus
from hpc_runner.core.spec import JobSpec
from tests.integration.cluster import Cluster

#: A three-task array whose task 2 fails.
ARRAY_TASK2_FAILS = '[ "$SGE_TASK_ID" != 2 ]'


def _submit(cluster: Cluster, spec: JobSpec) -> JobResult:
    return cluster.scheduler().submit(resolve(spec, cluster.config(), cluster.host()))


def _job_id(result: JobResult) -> str:
    assert result.job_id is not None
    return result.job_id


def _child(
    cluster: Cluster,
    name: str,
    *parents: JobResult,
    type: DependencyType = DependencyType.AFTEROK,
) -> JobResult:
    return _submit(cluster, JobSpec(f"echo {name}", name=name).after(*parents, type=type))


def _assert_skipped(cluster: Cluster, result: JobResult) -> None:
    """*result* ended SKIPPED, exit 113, and did not run its command."""
    job_id = _job_id(result)
    assert cluster.wait_for(job_id, JobStatus.SKIPPED) is JobStatus.SKIPPED
    assert cluster.scheduler().get_exit_code(job_id) == HPC_EXIT_DEP_FAILED
    assert cluster.scheduler().get_status(job_id) is JobStatus.SKIPPED
    stdout = result.read_stdout() or ""
    assert result.job is not None and result.job.name not in stdout.splitlines()


def _assert_ran(cluster: Cluster, result: JobResult) -> None:
    job_id = _job_id(result)
    assert cluster.wait_for(job_id, JobStatus.COMPLETED) is JobStatus.COMPLETED
    assert cluster.scheduler().get_exit_code(job_id) == 0
    stdout = result.read_stdout()
    assert result.job is not None and stdout is not None
    assert stdout.strip() == result.job.name


def test_afterok_cascade(cluster: Cluster) -> None:
    """B6/D8: a failed parent skips its afterok child (exit 113) and the grandchild in turn."""
    parent = _submit(cluster, JobSpec("exit 1", name="parent"))
    child = _child(cluster, "child", parent)
    grandchild = _child(cluster, "grandchild", child)

    assert cluster.wait_for(_job_id(parent), JobStatus.FAILED) is JobStatus.FAILED
    _assert_skipped(cluster, child)
    _assert_skipped(cluster, grandchild)


def test_afternotok(cluster: Cluster) -> None:
    """D8: afternotok runs the child when the parent fails and skips it when the parent succeeds."""
    failing = _submit(cluster, JobSpec("exit 1", name="failing"))
    passing = _submit(cluster, JobSpec("true", name="passing"))
    runs = _child(cluster, "runs", failing, type=DependencyType.AFTERNOTOK)
    skipped = _child(cluster, "skipped", passing, type=DependencyType.AFTERNOTOK)

    _assert_ran(cluster, runs)
    _assert_skipped(cluster, skipped)


def test_afterany(cluster: Cluster) -> None:
    """D8: afterany runs the child whether the parent failed or succeeded."""
    failing = _submit(cluster, JobSpec("exit 1", name="failing"))
    passing = _submit(cluster, JobSpec("true", name="passing"))
    after_failed = _child(cluster, "afterfailed", failing, type=DependencyType.AFTERANY)
    after_passed = _child(cluster, "afterpassed", passing, type=DependencyType.AFTERANY)

    _assert_ran(cluster, after_failed)
    _assert_ran(cluster, after_passed)


def test_array_parent(cluster: Cluster) -> None:
    """D8: an afterok child of an array parent is skipped if any task fails, runs if all succeed."""
    mixed = _submit(cluster, JobSpec(ARRAY_TASK2_FAILS, name="mixed", array="1-3"))
    clean = _submit(cluster, JobSpec("true", name="clean", array="1-3"))
    assert isinstance(mixed, ArrayJobResult) and isinstance(clean, ArrayJobResult)
    skipped = _child(cluster, "skipped", mixed)
    runs = _child(cluster, "runs", clean)

    _assert_skipped(cluster, skipped)
    _assert_ran(cluster, runs)


def test_cli_depend(cluster: Cluster) -> None:
    """B6: ``hpc run --batch --depend afterok:<id>`` takes the same guarded path as the API."""
    parent = _submit(cluster, JobSpec("exit 1", name="parent"))
    argv = [
        "run",
        "--batch",
        "--depend",
        f"afterok:{_job_id(parent)}",
        "--directory",
        str(cluster.workdir),
        "--name",
        "clichild",
        "echo",
        "clichild",
    ]
    outcome = CliRunner().invoke(cli, argv, catch_exceptions=False)
    assert outcome.exit_code == 0, outcome.output
    match = re.search(r"Submitted job (\d+)", outcome.output)
    assert match is not None, outcome.output
    child_id = match.group(1)

    assert cluster.wait_for(child_id, JobStatus.SKIPPED) is JobStatus.SKIPPED
    assert cluster.scheduler().get_exit_code(child_id) == HPC_EXIT_DEP_FAILED
    for out in cluster.workdir.glob("clichild.*.out"):
        assert "clichild" not in out.read_text().splitlines()
