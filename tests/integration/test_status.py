"""Status reporting: qstat and qacct (the exit sentinels are never a status, D19).

See docs/dev/testbed/TESTBED_PLAN.md section 5.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from hpc_runner.core.resolve import resolve
from hpc_runner.core.result import JobResult, JobStatus
from hpc_runner.core.spec import JobSpec
from hpc_runner.schedulers import _subprocess
from tests.fakes import SEAM
from tests.integration.cluster import Cluster

pytestmark = pytest.mark.family("sge")

#: ``qconf -sconf`` line of the slow-accounting stack (``hpc-testbed up --slow-accounting``).
SLOW_FLUSH = "flush_time=00:00:15"
#: D-T6: on the slow stack qacct lags the job end by a variable 10-13 s.
QACCT_WINDOW = 30.0
#: How long after start a slow-accounting stack may still write records at once.
SLOW_START = 90.0


def _submit(cluster: Cluster, spec: JobSpec) -> JobResult:
    return cluster.scheduler().submit(resolve(spec, cluster.config(), cluster.host()))


def _job_id(result: JobResult) -> str:
    assert result.job_id is not None
    return result.job_id


def _held(cluster: Cluster, command: str, name: str) -> str:
    """Submit *command* in user hold (``-h``); the id, PENDING until released."""
    return _job_id(_submit(cluster, JobSpec(command, name=name, scheduler_args={"sge": ["-h"]})))


def _qacct_has(cluster: Cluster, job_id: str) -> bool:
    return cluster.run(["qacct", "-j", job_id], check=False).returncode == 0


def test_queued_running_done(cluster: Cluster) -> None:
    """A held job is PENDING, RUNNING once released, then COMPLETED; get_statuses batches."""
    job_id = _held(cluster, "sleep 5", "lifecycle")
    others = [_held(cluster, "true", "other1"), _held(cluster, "true", "other2")]
    assert cluster.wait_for(job_id, JobStatus.PENDING, timeout=5) is JobStatus.PENDING

    scheduler = cluster.scheduler()
    with patch(SEAM, wraps=_subprocess.run_command) as seam:
        statuses = scheduler.get_statuses([job_id, *others])
    assert statuses == dict.fromkeys([job_id, *others], JobStatus.PENDING)
    qstat_calls = [c for c in seam.call_args_list if list(c.args[0][:2]) == ["qstat", "-xml"]]
    assert len(qstat_calls) == 1, [c.args[0] for c in seam.call_args_list]

    cluster.run(["qalter", "-h", "U", job_id])
    assert cluster.wait_for(job_id, JobStatus.RUNNING) is JobStatus.RUNNING
    assert cluster.wait_for(job_id, JobStatus.COMPLETED) is JobStatus.COMPLETED
    assert scheduler.get_exit_code(job_id) == 0


@pytest.mark.slow_accounting
def test_slow_accounting_window(cluster: Cluster) -> None:
    """D-T6/D-T22/D19: a finished job is UNKNOWN between leaving qstat and
    qacct having its record, then COMPLETED; ``wait()`` rides the gap out.

    Selected by ``--slow-accounting`` only; the stack must have been started
    with ``hpc-testbed up --slow-accounting`` or the test fails.
    """
    sconf = cluster.run(["qconf", "-sconf"]).stdout
    assert SLOW_FLUSH in sconf, (
        f"the master is not the slow-accounting stack ({SLOW_FLUSH} not in qconf -sconf); "
        "start it with `hpc-testbed up --slow-accounting` (D-T22)"
    )

    scheduler = cluster.scheduler()
    # OCS 9.1.7 writes each record at once for the first half minute or so
    # after the qmaster starts, then keeps to flush_time; soge buffers from
    # the start. So run jobs until one leaves qstat before its record is
    # written (the first, on soge).
    window_deadline = time.monotonic() + SLOW_START
    while True:
        job_id = _job_id(_submit(cluster, JobSpec("echo done", name="slowacct")))
        deadline = time.monotonic() + 60
        while cluster.run(["qstat", "-j", job_id], check=False).returncode == 0:
            assert time.monotonic() < deadline, f"job {job_id} still in qstat after 60s"
            time.sleep(0.2)
        left_qstat = time.monotonic()
        if not _qacct_has(cluster, job_id):
            break
        assert left_qstat < window_deadline, (
            f"qacct had every record as soon as the job left qstat for {SLOW_START}s; "
            "the stack is not slow"
        )

    # Its sentinel is written by now; it is not a status.
    assert (cluster.exit_dir / f"{job_id}.rc").exists()
    # (unless a flush landed in the last few milliseconds)
    early = (scheduler.get_status(job_id), scheduler.get_exit_code(job_id))
    assert early == (JobStatus.UNKNOWN, None) or _qacct_has(cluster, job_id), early
    status = scheduler.result(job_id).wait(poll_interval=0.5, unknown_after=QACCT_WINDOW)
    assert status is JobStatus.COMPLETED
    assert time.monotonic() - left_qstat < QACCT_WINDOW
    assert scheduler.get_exit_code(job_id) == 0


def test_aged_out(cluster: Cluster) -> None:
    """A job gone from qstat and qacct is UNKNOWN after unknown_after, not forever."""
    scheduler = cluster.scheduler()
    job_id = _job_id(_submit(cluster, JobSpec("true", name="aged")))
    assert cluster.wait_for(job_id, JobStatus.COMPLETED) is JobStatus.COMPLETED

    # COMPLETED came from qacct, so the record exists; "aged out" means it
    # was rotated away. The job's sentinel stays: it is not a status (D19).
    assert _qacct_has(cluster, job_id)
    assert (cluster.exit_dir / f"{job_id}.rc").exists()
    cluster.accounting_truncate()
    assert not _qacct_has(cluster, job_id)

    started = time.monotonic()
    status = scheduler.result(job_id).wait(poll_interval=0.5, unknown_after=2, timeout=20)
    elapsed = time.monotonic() - started
    assert status is JobStatus.UNKNOWN
    assert 2 <= elapsed < 10, elapsed
    assert scheduler.get_exit_code(job_id) is None


def _qstat_state(cluster: Cluster, job_id: str) -> str | None:
    """The job's state code from plain ``qstat``, or None once it has left."""
    for line in cluster.run(["qstat"]).stdout.splitlines()[2:]:
        parts = line.split()
        if len(parts) >= 5 and parts[0] == job_id:
            return parts[4]
    return None


def _wait_state(cluster: Cluster, job_id: str, state: str, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while (seen := _qstat_state(cluster, job_id)) != state:
        assert time.monotonic() < deadline, f"job {job_id} is {seen!r}, wanted {state!r}"
        time.sleep(0.5)


def test_suspended_job_is_waited_through(cluster: Cluster) -> None:
    """Lifecycle matrix row 7, column J: ``wait()`` does not return while suspended."""
    handle = _submit(cluster, JobSpec("sleep 6", name="suspend"))
    job_id = _job_id(handle)
    assert cluster.wait_for(job_id, JobStatus.RUNNING) is JobStatus.RUNNING
    cluster.run(["qmod", "-sj", job_id])
    assert cluster.wait_for(job_id, JobStatus.SUSPENDED) is JobStatus.SUSPENDED
    with pytest.raises(TimeoutError):
        handle.wait(poll_interval=0.5, timeout=8)
    assert cluster.scheduler().get_status(job_id) is JobStatus.SUSPENDED
    cluster.run(["qmod", "-usj", job_id])
    assert handle.wait(poll_interval=0.5, timeout=60) is JobStatus.COMPLETED
    assert handle.returncode == 0


def test_requeued_job_ends_with_its_final_record(cluster: Cluster) -> None:
    """Row 19, column J: a ``qmod -rj`` restart is waited through; the end is its second run's.

    qacct holds a ``failed 25`` record for the first run; the reader must take
    the last one (COMPLETED, exit 0), not the reschedule.
    """
    handle = _submit(
        cluster, JobSpec("sleep 8", name="requeue", scheduler_args={"sge": ["-r", "y"]})
    )
    job_id = _job_id(handle)
    assert cluster.wait_for(job_id, JobStatus.RUNNING) is JobStatus.RUNNING
    cluster.run(["qmod", "-rj", job_id], user="root")
    _wait_state(cluster, job_id, "Rr", timeout=90)
    assert cluster.scheduler().get_status(job_id) is JobStatus.RUNNING
    assert handle.wait(poll_interval=0.5, timeout=90) is JobStatus.COMPLETED
    assert handle.returncode == 0
    deadline = time.monotonic() + 30
    while (out := cluster.run(["qacct", "-j", job_id], check=False).stdout).count("jobnumber") < 2:
        assert time.monotonic() < deadline, out
        time.sleep(0.5)
    failed = [ln.split(None, 1)[1] for ln in out.splitlines() if ln.startswith("failed")]
    assert failed[0].startswith("25") and failed[-1].startswith("0"), failed
    assert cluster.scheduler().get_status(job_id) is JobStatus.COMPLETED
    history = [j for j in cluster.scheduler().list_completed_jobs() if j.job_id == job_id]
    assert [(j.status, j.exit_code) for j in history] == [(JobStatus.COMPLETED, 0)]


def test_deleted_while_pending_waits_out_to_unknown(cluster: Cluster) -> None:
    """Row 18, column J: no record is ever written, so ``wait()`` gives UNKNOWN, not a hang."""
    job_id = _held(cluster, "true", "delpend")
    assert cluster.wait_for(job_id, JobStatus.PENDING, timeout=5) is JobStatus.PENDING
    cluster.run(["qdel", job_id])
    status = cluster.scheduler().result(job_id).wait(poll_interval=0.5, unknown_after=3, timeout=30)
    assert status is JobStatus.UNKNOWN
    assert not _qacct_has(cluster, job_id)
