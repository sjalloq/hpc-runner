"""Status where it depends on how Slurm records a job: ``sacct``, with the
controller's record for the exit code while it has one."""

from __future__ import annotations

import time

from hpc_runner.core.result import JobStatus
from hpc_runner.core.spec import JobSpec
from tests.integration.cluster import Cluster
from tests.integration.helpers import history_entry, hpc_json
from tests.integration.slurm.slurm import (
    TESTER_UID,
    job_id,
    sacct,
    scontrol_job,
    submit,
    wait_rows,
    wait_squeue_state,
)

#: Slurm starts a requeued job again about two minutes after the requeue.
REQUEUE_TIMEOUT = 300.0


def test_cancelled_while_pending_is_cancelled_with_no_exit_code(cluster: Cluster) -> None:
    """A held job cancelled before it ran: ``sacct`` keeps a row for it
    (``CANCELLED by <uid>``), so ``wait()`` gives CANCELLED at once. Nothing
    ran, so there is no exit code and no output file."""
    result = submit(cluster, JobSpec("echo ran", name="cancelpend", scheduler_args=["--hold"]))
    job = job_id(result)
    assert cluster.wait_for(job, JobStatus.PENDING, timeout=5) is JobStatus.PENDING
    assert result.cancel()
    handle = cluster.scheduler().result(job)
    assert handle.wait(poll_interval=0.5, unknown_after=3, timeout=30) is JobStatus.CANCELLED
    wait_rows(cluster, job, [f"{job}|CANCELLED by {TESTER_UID}|0:0"])
    assert handle.returncode is None
    assert cluster.scheduler().get_exit_code(job) is None
    info, extra = cluster.scheduler().get_job_details(job)
    assert (info.status, info.exit_code) == (JobStatus.CANCELLED, None)
    assert extra["state"] == f"CANCELLED by {TESTER_UID}"
    assert not list(cluster.workdir.glob("cancelpend.*.out"))


def test_a_job_just_submitted_has_a_status_at_once(cluster: Cluster) -> None:
    """On the lines after the submission a held job is PENDING, not UNKNOWN,
    though ``sacct`` has its row a moment after ``squeue`` does: the details
    (what ``hpc status <id>`` shows) at once, from the controller's record,
    and ``get_status`` once ``sacct`` has the row."""
    result = submit(cluster, JobSpec("echo ran", name="justsubmitted", scheduler_args=["--hold"]))
    try:
        started = time.monotonic()
        info, _ = cluster.scheduler().get_job_details(job_id(result))
        assert time.monotonic() - started < 1.0
        assert (info.name, info.status) == ("justsubmitted", JobStatus.PENDING)
        assert cluster.scheduler().get_status(job_id(result)) is JobStatus.PENDING
    finally:
        assert result.cancel()


def test_requeued_job_is_waited_through_to_its_second_run(cluster: Cluster) -> None:
    """``scontrol requeue`` of a running job: it is PENDING again, not finished,
    and ``wait()`` goes on to the end of its second run. ``sacct`` keeps the
    first run as a ``REQUEUED`` row that only ``-D`` shows; hpc-runner reports
    the job once, COMPLETED with exit code 0."""
    handle = submit(cluster, JobSpec("sleep 8", name="requeue"))
    job = job_id(handle)
    assert cluster.wait_for(job, JobStatus.RUNNING) is JobStatus.RUNNING
    cluster.run(["scontrol", "requeue", job])
    wait_squeue_state(cluster, job, "PENDING|BeginTime")
    assert scontrol_job(cluster, job)["Restarts"] == "1"
    assert cluster.scheduler().get_status(job) is JobStatus.PENDING
    assert cluster.scheduler().get_exit_code(job) is None

    assert handle.wait(poll_interval=1, timeout=REQUEUE_TIMEOUT) is JobStatus.COMPLETED
    assert handle.returncode == 0
    wait_rows(cluster, job, [f"{job}|COMPLETED|0:0", f"{job}.batch|COMPLETED|0:0"])
    assert sacct(cluster, job, "JobID,State", job_row_only=True) == [f"{job}|COMPLETED"]
    argv = ["sacct", "-D", "-X", "-P", "-n", "-j", job, "--format=JobID,State"]
    assert cluster.run(argv).stdout.splitlines() == [f"{job}|REQUEUED", f"{job}|COMPLETED"]
    history_entry(cluster, job)  # wait until the history has it
    history = [j for j in hpc_json(cluster, "status", "--history") if j["job_id"] == job]
    assert [(j["status"], j["exit_code"]) for j in history] == [("COMPLETED", 0)]
