"""The ``hpc`` CLI where Slurm's own states and flags show."""

from __future__ import annotations

from hpc_runner.core.result import JobStatus
from tests.integration.cluster import Cluster
from tests.integration.helpers import history_entry, hpc, hpc_json, run_batch, submitted_id
from tests.integration.slurm.slurm import (
    ALLOC_200M,
    TESTER_UID,
    wait_rows,
    wait_squeue_state,
)


def test_history_has_sacct_exit_code_and_details_the_controllers(cluster: Cluster) -> None:
    """A job that exits 137: ``sacct`` prints ``9:0``, so `--history`, which reads
    ``sacct`` alone, says 9; `hpc status <id>` asks the controller and says 137.
    Slurm has no failure code beside the exit code."""
    job_id = run_batch(cluster, "--name", "cli137", "sh", "-c", "exit 137")
    assert cluster.wait_for(job_id, JobStatus.FAILED) is JobStatus.FAILED
    wait_rows(cluster, job_id, [f"{job_id}|FAILED|9:0", f"{job_id}.batch|FAILED|9:0"])
    done = history_entry(cluster, job_id)
    assert (done["status"], done["exit_code"]) == ("FAILED", 9)
    assert (done["failed_code"], done["failed_reason"]) == (None, None)
    details = hpc_json(cluster, "status", job_id)
    assert (details["status"], details["exit_code"]) == ("FAILED", 137)


def test_status_shows_slurms_own_state(cluster: Cluster) -> None:
    """`hpc status <id>` carries Slurm's state in the details: who cancelled a job,
    and that a KILLED job was out of memory. The table shows it with --verbose."""
    cancelled = run_batch(cluster, "--name", "clicancel", "sleep", "300")
    assert cluster.wait_for(cancelled, JobStatus.RUNNING) is JobStatus.RUNNING
    hpc(cluster, "cancel", "--force", cancelled)
    done = history_entry(cluster, cancelled)
    assert done["status"] == "CANCELLED"
    assert cluster.scheduler().get_exit_code(cancelled) == 143
    details = hpc_json(cluster, "status", cancelled)
    assert (details["status"], details["exit_code"]) == ("CANCELLED", 143)
    assert details["details"]["state"] == f"CANCELLED by {TESTER_UID}"
    table = hpc(cluster, "status", "--verbose", cancelled)
    assert "143" in table and f"CANCELLED by {TESTER_UID}" in table

    killed = run_batch(cluster, "--name", "clioom", "--mem", "50M", "sh", "-c", ALLOC_200M)
    assert cluster.wait_for(killed, JobStatus.KILLED) is JobStatus.KILLED
    details = hpc_json(cluster, "status", killed)
    assert (details["status"], details["details"]["state"]) == ("KILLED", "OUT_OF_MEMORY")
    table = hpc(cluster, "status", "--verbose", killed)
    assert "KILLED" in table and "OUT_OF_MEMORY" in table


def test_hold_flag_forwards(cluster: Cluster) -> None:
    """sbatch's ``--hold`` reaches the job through ``-S '--hold'``: it pends with
    reason ``JobHeldUser`` until released."""
    run = ("run", "--batch", "--directory", str(cluster.workdir))
    out = hpc(cluster, *run, "-S", "--hold", "sleep", "30")
    job_id = submitted_id(out)
    assert "Usage:" not in out
    wait_squeue_state(cluster, job_id, "PENDING|JobHeldUser")
    active = {j["job_id"]: j["status"] for j in hpc_json(cluster, "status")}
    assert active == {job_id: "PENDING"}
    cluster.run(["scontrol", "release", job_id])
    assert cluster.wait_for(job_id, JobStatus.RUNNING) is JobStatus.RUNNING
