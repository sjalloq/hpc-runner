"""End-to-end check for ``hpc-testbed pytest``: one batch job, start to finish."""

from __future__ import annotations

from hpc_runner.core.resolve import resolve
from hpc_runner.core.result import JobStatus
from hpc_runner.core.spec import JobSpec
from tests.integration.cluster import Cluster


def test_batch_echo_hello(cluster: Cluster) -> None:
    job = resolve(JobSpec("echo hello", name="smoke"), cluster.config(), cluster.host())
    result = cluster.scheduler().submit(job)

    assert result.job_id is not None and result.job_id.isdigit()
    assert cluster.wait_for(result.job_id, JobStatus.COMPLETED) is JobStatus.COMPLETED

    stdout = result.read_stdout()
    assert stdout is not None
    assert stdout.strip() == "hello"
