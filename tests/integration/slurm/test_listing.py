"""What ``get_job_details`` adds for Slurm: the keys only the controller has."""

from __future__ import annotations

from hpc_runner.core.result import JobStatus
from tests.integration.cluster import Cluster
from tests.integration.helpers import submit_as
from tests.integration.slurm.slurm import scontrol_job


def test_details_of_another_users_job_have_state_project_and_command(cluster: Cluster) -> None:
    """alice's running job, read by tester: Slurm's state, the account it runs
    under and the script it was submitted as, the same as ``scontrol`` says."""
    alice_id = submit_as(cluster, "alice", "alice-job", ["sleep", "30"])
    assert cluster.wait_for(alice_id, JobStatus.RUNNING) is JobStatus.RUNNING
    info, details = cluster.scheduler().get_job_details(alice_id)
    assert info.user == "alice"
    assert (details["state"], details["project"]) == ("RUNNING", "proj")
    script_dir = cluster.host().env["HPC_SCRIPT_DIR"]
    command = details["command"]
    assert isinstance(command, str) and command.startswith(f"{script_dir}/hpc_batch_")
    assert command == scontrol_job(cluster, alice_id)["Command"]
    assert "reason" not in details and "dependencies" not in details
