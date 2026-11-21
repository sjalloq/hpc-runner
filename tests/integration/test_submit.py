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

#: Fillers run ``sleep 15``; a queued interactive job cannot start for most of that.
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


def test_rejected_queue(cluster: Cluster) -> None:
    """D13: an unknown queue raises SubmissionError naming it, with the scheduler's stderr."""
    with pytest.raises(SubmissionError) as excinfo:
        _submit(cluster, JobSpec("true", name="badq", queue="nope.q"))
    assert "nope.q" in str(excinfo.value)
    assert "nope.q" in excinfo.value.stderr


def test_interactive(cluster: Cluster, capfd: pytest.CaptureFixture[str]) -> None:
    """interactive=True: no job id, the command's exit code, stdout streamed."""
    result = _submit(cluster, JobSpec("echo hi; exit 3", name="inter", interactive=True))
    assert result.job_id is None
    assert result.exit_code == 3
    assert result.status is JobStatus.FAILED
    assert result.read_stdout() is None
    assert "hi" in capfd.readouterr().out


def test_interactive_no_slot(cluster: Cluster, capfd: pytest.CaptureFixture[str]) -> None:
    """With every slot busy, an interactive job queues, then completes."""
    fillers = [
        _job_id(_submit(cluster, JobSpec("sleep 15", name=f"fill{i}", queue=queue)))
        for queue, slots in cluster.queue_slots.items()
        for i in range(slots)
    ]
    for job_id in fillers:
        cluster.wait_for(job_id, JobStatus.RUNNING)

    started = time.monotonic()
    result = _submit(cluster, JobSpec("echo late", name="inter-late", interactive=True))
    waited = time.monotonic() - started
    assert result.job_id is None
    assert result.exit_code == 0
    assert "late" in capfd.readouterr().out
    # It needs one slot: it ran only after a filler finished, not at once.
    assert waited > MIN_QUEUED_SECONDS, waited
    # A filler has left the active list; it is UNKNOWN until its accounting record is
    # written (D19), then COMPLETED.
    statuses = cluster.scheduler().get_statuses(fillers)
    done = [job_id for job_id, status in statuses.items() if status is not JobStatus.RUNNING]
    assert done, statuses
    assert cluster.wait_for(done[0], JobStatus.COMPLETED) is JobStatus.COMPLETED
