"""How Slurm skips a job: its own ``--dependency`` with ``--kill-on-invalid-dep=yes``.

The generic tests (``tests/integration/test_dependencies.py``) check that
each of these children ends SKIPPED without running. This checks how Slurm
records one, which is what the backend reads: plain ``CANCELLED`` (nobody's
uid after it), no exit code, no output file. And that without
``kill_invalid_dependents`` Slurm keeps such a job pending for ever.
"""

from __future__ import annotations

import dataclasses

from click.testing import CliRunner

from hpc_runner.cli.main import cli
from hpc_runner.core.dependency import DependencyType
from hpc_runner.core.resolve import resolve
from hpc_runner.core.result import JobResult, JobStatus
from hpc_runner.core.spec import JobSpec
from hpc_runner.schedulers import get_scheduler
from tests.integration.cluster import Cluster
from tests.integration.helpers import submitted_id
from tests.integration.slurm.slurm import (
    job_id,
    scontrol_job,
    submit,
    wait_rows,
    wait_squeue_state,
)


def _child(
    cluster: Cluster,
    name: str,
    *parents: JobResult,
    type: DependencyType = DependencyType.AFTEROK,
) -> JobResult:
    return submit(cluster, JobSpec(f"echo {name}", name=name).after(*parents, type=type))


def test_every_skipped_job_is_cancelled_by_slurm_with_no_exit_code(cluster: Cluster) -> None:
    """An afterok child and grandchild of a failed job, an afternotok child of a
    passed one, an afterok child of an array with a failed task, and a child
    given with ``hpc run --depend``: each is SKIPPED, ``CANCELLED`` in ``sacct``
    with no uid, has no exit code and wrote no output file."""
    failing = submit(cluster, JobSpec("exit 1", name="failing"))
    passing = submit(cluster, JobSpec("true", name="passing"))
    mixed = submit(cluster, JobSpec('[ "$SLURM_ARRAY_TASK_ID" != 2 ]', name="mixed", array="1-3"))
    child = _child(cluster, "child", failing)
    script = cluster.scheduler().preview(child.job).splitlines()
    assert f"#SBATCH --dependency=afterok:{job_id(failing)} --kill-on-invalid-dep=yes" in script
    skipped = {
        "child": job_id(child),
        "grandchild": job_id(_child(cluster, "grandchild", child)),
        "afternotok": job_id(
            _child(cluster, "afternotok", passing, type=DependencyType.AFTERNOTOK)
        ),
        "arraychild": job_id(_child(cluster, "arraychild", mixed)),
    }
    argv = ["run", "--batch", "--depend", f"afterok:{job_id(failing)}"]
    argv += ["--directory", str(cluster.workdir), "--name", "clichild", "echo", "clichild"]
    outcome = CliRunner().invoke(cli, argv, catch_exceptions=False)
    assert outcome.exit_code == 0, outcome.output
    skipped["clichild"] = submitted_id(outcome.output)

    for name, skipped_id in skipped.items():
        assert cluster.wait_for(skipped_id, JobStatus.SKIPPED) is JobStatus.SKIPPED, name
        wait_rows(cluster, skipped_id, [f"{skipped_id}|CANCELLED|0:0"])
        assert cluster.scheduler().get_exit_code(skipped_id) is None, name
        info, extra = cluster.scheduler().get_job_details(skipped_id)
        assert (info.status, info.exit_code, extra["state"]) == (
            JobStatus.SKIPPED,
            None,
            "CANCELLED",
        ), name
        assert not list(cluster.workdir.glob(f"{name}.*.out")), name


def test_without_kill_invalid_dependents_the_child_stays_pending(cluster: Cluster) -> None:
    """``kill_invalid_dependents = false`` sends no ``--kill-on-invalid-dep``: the
    afterok child of a failed job is PENDING, and stays so. ``squeue`` knows why
    (``DependencyNeverSatisfied``); hpc-runner reads ``sacct``, which does not."""
    slurm = {**cluster.config().schedulers.get("slurm", {}), "kill_invalid_dependents": False}
    config = dataclasses.replace(
        cluster.config(), schedulers={**cluster.config().schedulers, "slurm": slurm}
    )
    scheduler = get_scheduler("slurm", config=config)
    parent = submit(cluster, JobSpec("exit 1", name="parent"))
    spec = JobSpec("echo child", name="child").after(parent)
    resolved = resolve(spec, config, cluster.host())
    script = scheduler.preview(resolved).splitlines()
    assert f"#SBATCH --dependency=afterok:{job_id(parent)}" in script
    assert not [line for line in script if "kill-on-invalid-dep" in line]
    child = job_id(scheduler.submit(resolved))

    assert cluster.wait_for(job_id(parent), JobStatus.FAILED) is JobStatus.FAILED
    wait_squeue_state(cluster, child, "PENDING|DependencyNeverSatisfied")
    assert scontrol_job(cluster, child)["Dependency"] == f"afterok:{job_id(parent)}(failed)"
    assert cluster.scheduler().get_status(child) is JobStatus.PENDING
    assert cluster.scheduler().get_exit_code(child) is None
    wait_rows(cluster, child, [f"{child}|PENDING|0:0"])
    assert not list(cluster.workdir.glob("child.*.out"))
