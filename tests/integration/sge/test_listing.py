"""What ``get_job_details`` adds for SGE: the keys only ``qstat -j`` has."""

from __future__ import annotations

from hpc_runner.core.result import JobStatus
from tests.integration.cluster import Cluster
from tests.integration.helpers import submit_as


def test_details_of_another_users_job_have_cwd_and_script_file(cluster: Cluster) -> None:
    """alice's running job, read by tester: ``cwd`` is its directory and ``script_file`` is set."""
    alice_id = submit_as(cluster, "alice", "alice-job", ["sleep", "30"])
    assert cluster.wait_for(alice_id, JobStatus.RUNNING) is JobStatus.RUNNING
    _, details = cluster.scheduler().get_job_details(alice_id)
    assert details.get("cwd") == str(cluster.workdir)
    assert details.get("script_file") is not None
