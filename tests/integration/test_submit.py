"""Submission through ``resolve`` + ``scheduler.submit``.

See docs/dev/testbed/TESTBED_PLAN.md section 5.
"""

from __future__ import annotations

import time

import pytest

from hpc_runner.core.exceptions import SubmissionError
from hpc_runner.core.job import Job
from hpc_runner.core.resolve import resolve
from hpc_runner.core.result import JobResult, JobStatus
from hpc_runner.core.spec import JobSpec
from tests.integration.cluster import Cluster

#: Every queue instance and its slots. ``all.q`` is the only INTERACTIVE
#: queue, but SGE schedules ``qrsh -now no <command>`` like a batch job, so
#: it also runs in ``short.q`` (BATCH only); "no slot" means all eight busy.
QUEUE_SLOTS = {"all.q": 4, "short.q": 4}
#: Fillers run ``sleep 15``; a queued qrsh cannot start for most of that.
MIN_QUEUED_SECONDS = 5.0


def _job(cluster: Cluster, spec: JobSpec) -> Job:
    return resolve(spec, cluster.config(), cluster.host())


def _submit(cluster: Cluster, spec: JobSpec) -> JobResult:
    return cluster.scheduler().submit(_job(cluster, spec))


def _job_id(result: JobResult) -> str:
    assert result.job_id is not None
    return result.job_id


def test_batch_echo(cluster: Cluster) -> None:
    """B7/D9: a batch job's id is numeric and read_stdout() reads the owned output path."""
    result = _submit(cluster, JobSpec("echo hello", name="echo"))
    job_id = _job_id(result)
    assert job_id.isdigit()
    assert cluster.wait_for(job_id, JobStatus.COMPLETED) is JobStatus.COMPLETED
    assert result.stdout_path is not None and result.stdout_path.parent == cluster.workdir
    stdout = result.read_stdout()
    assert stdout is not None and stdout.strip() == "hello"


def test_passthrough(cluster: Cluster) -> None:
    """B1: scheduler_args land as one ``#$`` line and qstat -j shows the hard resource."""
    spec = JobSpec("sleep 10", name="passthrough", scheduler_args={"sge": ["-l", "gpu=1"]})
    job = _job(cluster, spec)
    directives = [line for line in cluster.scheduler().preview(job).splitlines() if "gpu" in line]
    assert directives == ["#$ -l gpu=1"]

    result = cluster.scheduler().submit(job)
    job_id = _job_id(result)
    _, details = cluster.scheduler().get_job_details(job_id)
    assert details.get("resources") == {"gpu": "1"}
    raw = cluster.run(["qstat", "-j", job_id]).stdout
    assert "gpu=1" in raw


def test_rejected_queue(cluster: Cluster) -> None:
    """D13: an unknown queue raises SubmissionError carrying qsub's reason text."""
    with pytest.raises(SubmissionError) as excinfo:
        _submit(cluster, JobSpec("true", name="badq", queue="nope.q"))
    assert 'unknown queue "nope.q"' in str(excinfo.value)
    assert "nope.q" in excinfo.value.stderr


def test_rejected_resource(cluster: Cluster) -> None:
    """D13: an unknown resource is refused by qsub and surfaces as SubmissionError.

    Note: an oversubscribed consumable (``-l gpu=2`` against complex_values
    gpu=1) is *accepted* at submit and queues forever; SGE only verifies
    satisfiability with ``qsub -w e``, which hpc-runner does not pass
    (docs/dev/testbed/TESTBED_REVIEW.md D-T27).
    """
    with pytest.raises(SubmissionError) as excinfo:
        _submit(cluster, JobSpec("true", name="badres", scheduler_args={"sge": ["-l", "nosuch=1"]}))
    assert "nosuch" in excinfo.value.stderr


def test_interactive(cluster: Cluster, capfd: pytest.CaptureFixture[str]) -> None:
    """interactive=True runs qrsh: no job id, the command's exit code, stdout streamed."""
    result = _submit(cluster, JobSpec("echo hi; exit 3", name="qrsh", interactive=True))
    assert result.job_id is None
    assert result.exit_code == 3
    assert result.status is JobStatus.FAILED
    assert result.read_stdout() is None
    assert "hi" in capfd.readouterr().out


def test_interactive_no_slot(cluster: Cluster, capfd: pytest.CaptureFixture[str]) -> None:
    """With every slot busy, qrsh (-now no since 806897f) queues, then completes."""
    fillers = [
        _job_id(_submit(cluster, JobSpec("sleep 15", name=f"fill{i}", queue=queue)))
        for queue, slots in QUEUE_SLOTS.items()
        for i in range(slots)
    ]
    for job_id in fillers:
        cluster.wait_for(job_id, JobStatus.RUNNING)

    started = time.monotonic()
    result = _submit(cluster, JobSpec("echo late", name="qrsh-late", interactive=True))
    waited = time.monotonic() - started
    assert result.job_id is None
    assert result.exit_code == 0
    assert "late" in capfd.readouterr().out
    # qrsh needs one slot: it ran only after a filler finished, not at once.
    assert waited > MIN_QUEUED_SECONDS, waited
    statuses = cluster.scheduler().get_statuses(fillers)
    assert JobStatus.COMPLETED in statuses.values(), statuses
