"""Infrastructure failures: qmaster stopped, an exec node killed.

See docs/dev/testbed/TESTBED_PLAN.md section 5.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest

from hpc_runner.core.exceptions import SchedulerError
from hpc_runner.core.resolve import resolve
from hpc_runner.core.result import JobResult, JobStatus
from hpc_runner.core.spec import JobSpec
from tests.integration.cluster import Cluster

#: BaseScheduler.COMMAND_TIMEOUT is 60 s; a failure must surface within that plus slack.
FAIL_WITHIN = 90.0
RECOVER_TIMEOUT = 90.0
#: Statuses that say "did not finish normally" (docs/dev/testbed/TESTBED_REVIEW.md D-T24).
FAILED_FAMILY = {JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.TIMEOUT}


@pytest.fixture(autouse=True)
def _needs_docker(cluster: Cluster) -> Iterator[None]:
    if not cluster.docker_available:
        pytest.skip("Docker socket not mounted in login; node control unavailable (D-T9)")
    yield


def _submit(cluster: Cluster, spec: JobSpec) -> JobResult:
    return cluster.scheduler().submit(resolve(spec, cluster.config(), cluster.host()))


def _job_id(result: JobResult) -> str:
    assert result.job_id is not None
    return result.job_id


@pytest.mark.xfail(
    # Not strict: against a stopped master qstat sometimes fails at once
    # (UNKNOWN, the bug) and sometimes hangs into the 60 s command timeout
    # (SchedulerTimeoutError, which passes). Make it strict once BUG-T6 is fixed.
    strict=False,
    reason="BUG-T6: qstat 'unable to contact qmaster' is treated as 'no active jobs'; "
    "status falls through qacct to UNKNOWN instead of raising SchedulerError",
)
def test_master_stopped(cluster: Cluster) -> None:
    """qmaster down: get_status raises SchedulerError within the command timeout; recovers after."""
    scheduler = cluster.scheduler()
    job_id = _job_id(_submit(cluster, JobSpec("sleep 300", name="orphan")))
    assert cluster.wait_for(job_id, JobStatus.RUNNING) is JobStatus.RUNNING

    cluster.stop_master()
    try:
        started = time.monotonic()
        with pytest.raises(SchedulerError) as excinfo:
            scheduler.get_status(job_id)
        elapsed = time.monotonic() - started
        assert elapsed < FAIL_WITHIN, elapsed
        assert type(excinfo.value) is not SchedulerError or str(excinfo.value)
    finally:
        cluster.start_master()
        cluster.wait_ready(RECOVER_TIMEOUT)

    assert scheduler.get_status(job_id) in {JobStatus.RUNNING, JobStatus.PENDING}
    assert scheduler.cancel(job_id)


@pytest.mark.xfail(strict=True, reason="BUG-T3: failed 19 with exit_status 0 reported COMPLETED")
def test_node_killed(cluster: Cluster) -> None:
    """D-T24: a job on a killed node ends FAILED-family, no exception, qacct ``failed 19``."""
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
