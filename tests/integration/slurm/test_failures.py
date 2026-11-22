"""Infrastructure failures: an exec node killed mid-job."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from hpc_runner.core.result import JobStatus
from hpc_runner.core.spec import JobSpec
from tests.integration.cluster import Cluster
from tests.integration.helpers import history_entry
from tests.integration.slurm.slurm import job_id, sacct, scontrol_job, submit

#: ``SlurmdTimeout=20`` (testbed slurm.conf): the controller gives the node up
#: about half a minute after it is killed.
NODE_FAIL_TIMEOUT = 120.0
RECOVER_TIMEOUT = 180.0


@pytest.fixture(autouse=True)
def _needs_docker(cluster: Cluster) -> Iterator[None]:
    """Node control is part of the testbed; without it the testbed is broken."""
    if not cluster.docker_available:
        pytest.fail(
            "Docker socket not mounted in login: node control unavailable, the testbed is "
            "broken (testbed/README.md)",
            pytrace=False,
        )
    yield


def test_node_killed(cluster: Cluster) -> None:
    """A job on a killed node, submitted so that Slurm does not requeue it, ends
    FAILED with Slurm's ``NODE_FAIL`` in the details, and never with exit code 0."""
    scheduler = cluster.scheduler()
    spec = JobSpec("sleep 300", name="doomed", scheduler_args=["--no-requeue", "-w", "exec1"])
    job = job_id(submit(cluster, spec))
    assert cluster.wait_for(job, JobStatus.RUNNING) is JobStatus.RUNNING
    fields = scontrol_job(cluster, job)
    assert (fields["NodeList"], fields["Requeue"]) == ("exec1", "0")

    cluster.kill_node("exec1")
    try:
        status = cluster.wait_for(job, JobStatus.FAILED, timeout=NODE_FAIL_TIMEOUT)
    finally:
        cluster.start_node("exec1")
        cluster.wait_ready(RECOVER_TIMEOUT)

    assert status is JobStatus.FAILED
    assert sacct(cluster, job, "JobID,State", job_row_only=True) == [f"{job}|NODE_FAIL"]
    info, details = scheduler.get_job_details(job)
    assert (info.status, info.node, details["state"]) == (JobStatus.FAILED, "exec1", "NODE_FAIL")
    assert scheduler.get_exit_code(job) != 0
    done = history_entry(cluster, job)
    assert done["status"] == "FAILED" and done["exit_code"] != 0
