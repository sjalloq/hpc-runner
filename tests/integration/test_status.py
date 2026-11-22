"""Status reporting through a job's life: pending, running, suspended, done.

See docs/dev/testbed/TESTBED_PLAN.md section 5.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from hpc_runner.core.resolve import resolve
from hpc_runner.core.result import JobResult, JobStatus
from hpc_runner.core.spec import JobSpec
from hpc_runner.schedulers import _subprocess
from tests.fakes import SEAM
from tests.integration.cluster import Cluster


def _submit(cluster: Cluster, spec: JobSpec) -> JobResult:
    return cluster.scheduler().submit(resolve(spec, cluster.config(), cluster.host()))


def _job_id(result: JobResult) -> str:
    assert result.job_id is not None
    return result.job_id


def _held(cluster: Cluster, command: str, name: str) -> str:
    """Submit *command* held; the id, PENDING until released."""
    spec = JobSpec(command, name=name, scheduler_args=list(cluster.hold_args))
    return _job_id(_submit(cluster, spec))


def test_queued_running_done(cluster: Cluster) -> None:
    """A held job is PENDING, RUNNING once released, then COMPLETED; get_statuses batches."""
    job_id = _held(cluster, "sleep 5", "lifecycle")
    others = [_held(cluster, "true", "other1"), _held(cluster, "true", "other2")]
    assert cluster.wait_for(job_id, JobStatus.PENDING, timeout=5) is JobStatus.PENDING

    scheduler = cluster.scheduler()
    with patch(SEAM, wraps=_subprocess.run_command) as seam:
        statuses = scheduler.get_statuses([job_id, *others])
    assert statuses == dict.fromkeys([job_id, *others], JobStatus.PENDING)
    # One scheduler command answered for all three.
    assert len(seam.call_args_list) == 1, [c.args[0] for c in seam.call_args_list]

    cluster.release_job(job_id)
    assert cluster.wait_for(job_id, JobStatus.RUNNING) is JobStatus.RUNNING
    assert cluster.wait_for(job_id, JobStatus.COMPLETED) is JobStatus.COMPLETED
    assert scheduler.get_exit_code(job_id) == 0


def test_suspended_job_is_waited_through(cluster: Cluster) -> None:
    """Lifecycle matrix row 7, column J: ``wait()`` does not return while suspended."""
    handle = _submit(cluster, JobSpec("sleep 6", name="suspend"))
    job_id = _job_id(handle)
    assert cluster.wait_for(job_id, JobStatus.RUNNING) is JobStatus.RUNNING
    cluster.suspend_job(job_id)
    assert cluster.wait_for(job_id, JobStatus.SUSPENDED) is JobStatus.SUSPENDED
    with pytest.raises(TimeoutError):
        handle.wait(poll_interval=0.5, timeout=8)
    assert cluster.scheduler().get_status(job_id) is JobStatus.SUSPENDED
    cluster.resume_job(job_id)
    assert handle.wait(poll_interval=0.5, timeout=60) is JobStatus.COMPLETED
    assert handle.returncode == 0
