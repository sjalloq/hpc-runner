"""Resource requests every scheduler has: a named queue."""

from __future__ import annotations

from hpc_runner.core.resolve import resolve
from hpc_runner.core.result import JobResult, JobStatus
from hpc_runner.core.spec import JobSpec
from tests.integration.cluster import Cluster


def _submit(cluster: Cluster, spec: JobSpec) -> JobResult:
    return cluster.scheduler().submit(resolve(spec, cluster.config(), cluster.host()))


def _job_id(result: JobResult) -> str:
    assert result.job_id is not None
    return result.job_id


def test_queue_selection(cluster: Cluster) -> None:
    """A job given the second queue runs in it on an exec node; list_active_jobs
    reports queue and node."""
    queue = cluster.other_queue
    job_id = _job_id(_submit(cluster, JobSpec("sleep 30", name="shortq", queue=queue)))
    assert cluster.wait_for(job_id, JobStatus.RUNNING) is JobStatus.RUNNING
    jobs = {j.job_id: j for j in cluster.scheduler().list_active_jobs()}
    assert job_id in jobs, jobs
    info = jobs[job_id]
    # JobInfo holds the queue and the node apart, however the scheduler joins them.
    assert info.queue == queue, info
    assert info.node is not None and info.node.startswith("exec"), info
    assert info.status is JobStatus.RUNNING
    assert info.user == "tester"
