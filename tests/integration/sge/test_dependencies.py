"""How SGE skips a job: ``-hold_jid`` plus the exit-sentinel guard, which exits 113 (D8).

The generic tests (``tests/integration/test_dependencies.py``) check that
each of these children ends SKIPPED without running; this checks the exit
code the guard gives each one, which is how the SGE backend tells a skipped
job from a failed one.
"""

from __future__ import annotations

from click.testing import CliRunner

from hpc_runner.cli.main import cli
from hpc_runner.core.dependency import DependencyType
from hpc_runner.core.resolve import resolve
from hpc_runner.core.result import HPC_EXIT_DEP_FAILED, JobResult, JobStatus
from hpc_runner.core.spec import JobSpec
from tests.integration.cluster import Cluster
from tests.integration.helpers import submitted_id


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


def test_every_skipped_job_exits_113(cluster: Cluster) -> None:
    """An afterok child and grandchild of a failed job, an afternotok child of a
    passed one, an afterok child of an array with a failed task, and a child
    given with ``hpc run --depend``: each is SKIPPED with exit code 113."""
    assert HPC_EXIT_DEP_FAILED == 113
    failing = _submit(cluster, JobSpec("exit 1", name="failing"))
    passing = _submit(cluster, JobSpec("true", name="passing"))
    mixed = _submit(cluster, JobSpec('[ "$SGE_TASK_ID" != 2 ]', name="mixed", array="1-3"))
    child = _child(cluster, "child", failing)
    skipped = {
        "child": _job_id(child),
        "grandchild": _job_id(_child(cluster, "grandchild", child)),
        "afternotok": _job_id(
            _child(cluster, "afternotok", passing, type=DependencyType.AFTERNOTOK)
        ),
        "arraychild": _job_id(_child(cluster, "arraychild", mixed)),
    }
    argv = ["run", "--batch", "--depend", f"afterok:{_job_id(failing)}"]
    argv += ["--directory", str(cluster.workdir), "--name", "clichild", "echo", "clichild"]
    outcome = CliRunner().invoke(cli, argv, catch_exceptions=False)
    assert outcome.exit_code == 0, outcome.output
    skipped["clichild"] = submitted_id(outcome.output)

    for name, job_id in skipped.items():
        assert cluster.wait_for(job_id, JobStatus.SKIPPED) is JobStatus.SKIPPED, name
        assert cluster.scheduler().get_exit_code(job_id) == HPC_EXIT_DEP_FAILED, name
