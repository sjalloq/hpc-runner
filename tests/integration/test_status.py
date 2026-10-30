"""Status reporting: qstat, qacct and the exit sentinel (TESTBED_PLAN.md section 5)."""

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

#: ``qconf -sconf`` line of the slow-accounting stack (``make sge-up SLOW_ACCOUNTING=1``).
SLOW_FLUSH = "flush_time=00:00:15"
#: D-T6: on the slow stack qacct lags the job end by a variable 10-13 s.
QACCT_WINDOW = 30.0


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


def test_slow_accounting_window(cluster: Cluster) -> None:
    """D-T6/D-T22: the exit sentinel resolves a finished job before qacct has its record."""
    sconf = cluster.run(["qconf", "-sconf"]).stdout
    if SLOW_FLUSH not in sconf:
        pytest.skip(f"needs the slow-accounting stack ({SLOW_FLUSH}); see TESTBED_REVIEW.md D-T22")

    scheduler = cluster.scheduler()
    job_id = _job_id(_submit(cluster, JobSpec("echo done", name="slowacct")))
    deadline = time.monotonic() + 60
    while cluster.run(["qstat", "-j", job_id], check=False).returncode == 0:
        assert time.monotonic() < deadline, f"job {job_id} still in qstat after 60s"
        time.sleep(0.2)
    left_qstat = time.monotonic()

    assert not _qacct_has(cluster, job_id), "qacct already had the record; stack is not slow"
    assert scheduler.get_status(job_id) is JobStatus.COMPLETED
    assert scheduler.get_exit_code(job_id) == 0

    while not _qacct_has(cluster, job_id):
        assert time.monotonic() - left_qstat < QACCT_WINDOW, f"qacct silent for {QACCT_WINDOW}s"
        time.sleep(0.5)
    assert scheduler.get_status(job_id) is JobStatus.COMPLETED


def test_aged_out(cluster: Cluster) -> None:
    """A job gone from qstat, qacct and the sentinel is UNKNOWN after unknown_after, not forever."""
    scheduler = cluster.scheduler()
    job_id = _job_id(_submit(cluster, JobSpec("true", name="aged")))
    assert cluster.wait_for(job_id, JobStatus.COMPLETED) is JobStatus.COMPLETED

    # wait_for may return via the sentinel before qmaster flushes the record;
    # "aged out" means the record existed and was rotated, so wait for it.
    deadline = time.monotonic() + 30
    while not _qacct_has(cluster, job_id):
        assert time.monotonic() < deadline, f"qacct never saw job {job_id}"
        time.sleep(0.2)
    sentinel = cluster.exit_dir / f"{job_id}.rc"
    assert sentinel.exists()
    sentinel.unlink()
    cluster.accounting_truncate()
    assert not _qacct_has(cluster, job_id)

    started = time.monotonic()
    status = scheduler.result(job_id).wait(poll_interval=0.5, unknown_after=2, timeout=20)
    elapsed = time.monotonic() - started
    assert status is JobStatus.UNKNOWN
    assert 2 <= elapsed < 10, elapsed
    assert scheduler.get_exit_code(job_id) is None
