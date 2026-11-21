"""The ``hpc`` CLI where SGE's own fields and flags show."""

from __future__ import annotations

from hpc_runner.core.result import JobStatus
from tests.integration.cluster import Cluster
from tests.integration.helpers import history_entry, hpc, hpc_json, run_batch, submitted_id


def test_history_reports_failed_0_for_a_job_that_ran(cluster: Cluster) -> None:
    """A job that ran and exited 3: SGE's ``failed`` is 0, and `--history` says so."""
    job_id = run_batch(cluster, "--name", "clifailed", "sh", "-c", "exit 3")
    assert cluster.wait_for(job_id, JobStatus.FAILED) is JobStatus.FAILED
    done = history_entry(cluster, job_id)
    assert (done["status"], done["exit_code"], done["failed_code"]) == ("FAILED", 3, 0)


def test_status_shows_the_schedulers_failure_reason(cluster: Cluster) -> None:
    """A job killed by qdel: exit 137, failed 100 and SGE's words, in `hpc status <id>`."""
    job_id = run_batch(cluster, "--name", "clikill", "sleep", "300")
    assert cluster.wait_for(job_id, JobStatus.RUNNING) is JobStatus.RUNNING
    hpc(cluster, "cancel", "--force", job_id)
    done = history_entry(cluster, job_id)
    assert (done["status"], done["exit_code"], done["failed_code"]) == ("FAILED", 137, 100)
    assert done["failed_reason"] == "assumedly after job"
    table = hpc(cluster, "status", job_id)
    assert "137" in table and "100: assumedly after job" in table


def test_hold_flag_forwards(cluster: Cluster) -> None:
    """D-T43: qsub's user hold reaches the job through ``-S '-h'``: it queues ``hqw``."""
    out = hpc(
        cluster, "run", "--batch", "--directory", str(cluster.workdir), "-S", "-h", "sleep", "30"
    )
    job_id = submitted_id(out)
    assert "Usage:" not in out
    states = {
        line.split()[0]: line.split()[4]
        for line in cluster.run(["qstat", "-u", "tester"]).stdout.splitlines()[2:]
        if line.split()
    }
    assert states.get(job_id) == "hqw", states
    assert hpc_json(cluster, "status")[0]["status"] == "PENDING"
    cluster.run(["qrls", job_id])
    assert cluster.wait_for(job_id, JobStatus.RUNNING) is JobStatus.RUNNING
