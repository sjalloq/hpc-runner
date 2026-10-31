"""Infrastructure failures: qmaster stopped, an exec node killed.

See docs/dev/testbed/TESTBED_PLAN.md section 5.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest

from hpc_runner.core.exceptions import SchedulerUnavailableError
from hpc_runner.core.resolve import resolve
from hpc_runner.core.result import JobResult, JobStatus
from hpc_runner.core.spec import JobSpec
from hpc_runner.schedulers.base import BaseScheduler
from tests.integration.cluster import Cluster

#: Against a stopped master, qstat's commlib gives up after about 45 s (observed 43.5 s
#: on gridengine 8.1.9). The error must come from qstat's own stderr, so it must
#: land before BaseScheduler.COMMAND_TIMEOUT (60 s) turns it into a timeout.
FAIL_WITHIN = BaseScheduler.COMMAND_TIMEOUT
RECOVER_TIMEOUT = 90.0
#: Statuses that say "did not finish normally" (docs/dev/testbed/TESTBED_REVIEW.md D-T24).
FAILED_FAMILY = {JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.TIMEOUT}


@pytest.fixture(autouse=True)
def _needs_docker(cluster: Cluster) -> Iterator[None]:
    """Node control is part of the testbed (D-T9); without it the testbed is broken."""
    if not cluster.docker_available:
        pytest.fail(
            "Docker socket not mounted in login: node control unavailable, the testbed is "
            "broken (testbed/README.md, D-T9)",
            pytrace=False,
        )
    yield


def _submit(cluster: Cluster, spec: JobSpec) -> JobResult:
    return cluster.scheduler().submit(resolve(spec, cluster.config(), cluster.host()))


def _job_id(result: JobResult) -> str:
    assert result.job_id is not None
    return result.job_id


def test_master_stopped(cluster: Cluster) -> None:
    """qmaster down: get_status raises SchedulerUnavailableError; recovers after.

    The oracle is the specific error built from qstat's stderr (BUG-T6): the
    command-timeout path raises SchedulerTimeoutError and fails this test.
    """
    scheduler = cluster.scheduler()
    job_id = _job_id(_submit(cluster, JobSpec("sleep 300", name="orphan")))
    assert cluster.wait_for(job_id, JobStatus.RUNNING) is JobStatus.RUNNING

    cluster.stop_master()
    try:
        started = time.monotonic()
        with pytest.raises(SchedulerUnavailableError, match="qmaster") as excinfo:
            scheduler.get_status(job_id)
        elapsed = time.monotonic() - started
        assert elapsed < FAIL_WITHIN, elapsed
        assert "unable to contact qmaster" in str(excinfo.value), excinfo.value
    finally:
        cluster.start_master()
        cluster.wait_ready(RECOVER_TIMEOUT)

    assert scheduler.get_status(job_id) in {JobStatus.RUNNING, JobStatus.PENDING}
    assert scheduler.cancel(job_id)


def test_node_killed(cluster: Cluster) -> None:
    """D-T24: a job on a killed node ends FAILED, no exception, qacct ``failed 19`` (BUG-T3)."""
    scheduler = cluster.scheduler()
    spec = JobSpec("sleep 30", name="doomed", resources=[{"name": "hostname", "value": "exec1"}])
    job_id = _job_id(_submit(cluster, spec))
    assert cluster.wait_for(job_id, JobStatus.RUNNING) is JobStatus.RUNNING
    _, details = scheduler.get_job_details(job_id)
    assert details.get("resources") == {"hostname": "exec1"}

    cluster.kill_node("exec1")
    try:
        time.sleep(2)
        cluster.start_node("exec1")
        deadline = time.monotonic() + RECOVER_TIMEOUT
        while cluster.run(["qstat", "-j", job_id], check=False).returncode == 0:
            assert time.monotonic() < deadline, f"job {job_id} still in qstat after node restart"
            time.sleep(1)
    finally:
        cluster.start_node("exec1")
        cluster.wait_ready(RECOVER_TIMEOUT)

    status = scheduler.get_status(job_id)
    assert status.is_terminal, status
    assert status in FAILED_FAMILY, status
    record = cluster.run(["qacct", "-j", job_id]).stdout
    failed = [line.split() for line in record.splitlines() if line.startswith("failed")]
    assert failed and failed[0][1] == "19", record
    # The scheduler's own words reach the listing (hpc status --history shows them).
    info = next(j for j in scheduler.list_completed_jobs(user="tester") if j.job_id == job_id)
    assert (info.status, info.failed_code) == (JobStatus.FAILED, 19), info
    assert info.failed_reason == "before writing exit_status"
