"""Listing other users' jobs: list_active_jobs, list_completed_jobs, get_job_details."""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest

from hpc_runner.core.job_info import JobInfo
from hpc_runner.core.result import JobStatus
from tests.integration.cluster import Cluster

HPC_KEYS = ("HPC_SCHEDULER", "HPC_RUNNER_CONFIG", "HPC_SCRIPT_DIR", "HPC_EXIT_DIR")
#: qmaster flushes accounting within 1 s on the default stack; allow more.
QACCT_TIMEOUT = 30.0


def _submit_as(cluster: Cluster, user: str, name: str, command: list[str]) -> str:
    """Submit a batch job as *user* through the CLI; return its id.

    D-T31: the API submits as the test process (tester), so other users go
    through ``sudo -u <user> hpc run --batch ...`` with the testbed's HPC_*
    exported. The jobs are not held: ``-h`` is the CLI's help flag (BUG-T4),
    so qsub's ``-h`` cannot be passed through.
    """
    env = cluster.host().env
    argv = [
        "env",
        *(f"{k}={env[k]}" for k in HPC_KEYS),
        f"HOME=/home/{user}",
        str(cluster.hpc_cli),
        "run",
        "--batch",
        "--name",
        name,
        "--directory",
        str(cluster.workdir),
        *command,
    ]
    out = cluster.run(argv, user=user).stdout
    match = re.search(r"Submitted job (\d+)", out)
    assert match is not None, out
    return match.group(1)


def _completed(cluster: Cluster, user: str, job_id: str) -> JobInfo:
    """Poll list_completed_jobs(user=...) until it has *job_id*; return its JobInfo."""
    deadline = time.monotonic() + QACCT_TIMEOUT
    while True:
        jobs = {j.job_id: j for j in cluster.scheduler().list_completed_jobs(user=user)}
        if job_id in jobs:
            return jobs[job_id]
        assert time.monotonic() < deadline, f"{job_id} not in list_completed_jobs({user!r})"
        time.sleep(0.5)


def test_two_users(cluster: Cluster) -> None:
    """alice and bob submit; listings filter by user and get_job_details reports the fields."""
    alice_id = _submit_as(cluster, "alice", "alice-job", ["sleep", "30"])
    bob_id = _submit_as(cluster, "bob", "bob-job", ["sleep", "8"])
    assert cluster.wait_for(alice_id, JobStatus.RUNNING) is JobStatus.RUNNING
    assert cluster.wait_for(bob_id, JobStatus.RUNNING) is JobStatus.RUNNING

    scheduler = cluster.scheduler()
    alice_jobs = scheduler.list_active_jobs(user="alice")
    assert [j.job_id for j in alice_jobs] == [alice_id]
    assert alice_jobs[0].user == "alice" and alice_jobs[0].name == "alice-job"
    assert alice_jobs[0].status is JobStatus.RUNNING
    everyone = {j.job_id: j.user for j in scheduler.list_active_jobs()}
    assert everyone == {alice_id: "alice", bob_id: "bob"}
    assert scheduler.list_active_jobs(user="tester") == []

    info, details = scheduler.get_job_details(alice_id)
    assert info.job_id == alice_id and info.user == "alice" and info.name == "alice-job"
    assert info.status is JobStatus.RUNNING
    assert info.submit_time is not None and info.start_time is not None
    assert info.node in {"exec1", "exec2"} and info.queue is not None
    assert info.working_dir == cluster.workdir
    assert info.stdout_path is not None and Path(info.stdout_path).parent == cluster.workdir
    assert details.get("cwd") == str(cluster.workdir)
    assert details.get("script_file") is not None

    assert cluster.wait_for(bob_id, JobStatus.COMPLETED) is JobStatus.COMPLETED
    done = _completed(cluster, "bob", bob_id)
    assert done.user == "bob" and done.name == "bob-job"
    assert done.status is JobStatus.COMPLETED and done.exit_code == 0
    assert done.queue == "all.q" and done.node in {"exec1", "exec2"}
    assert done.start_time is not None and done.end_time is not None
    assert alice_id not in {j.job_id for j in scheduler.list_completed_jobs(user="bob")}

    assert scheduler.list_active_jobs(user="bob") == []
    assert [j.job_id for j in scheduler.list_active_jobs(user="alice")] == [alice_id]


@pytest.mark.xfail(strict=True, reason="BUG-T2: qacct -o summary block parsed as a job")
def test_completed_listing_by_user(cluster: Cluster) -> None:
    """BUG-T2: list_completed_jobs(user=bob) holds only bob's real records, no phantom entry."""
    bob_id = _submit_as(cluster, "bob", "bob-only", ["true"])
    assert cluster.wait_for(bob_id, JobStatus.COMPLETED) is JobStatus.COMPLETED
    _completed(cluster, "bob", bob_id)
    jobs = cluster.scheduler().list_completed_jobs(user="bob")
    assert all(j.user == "bob" and j.job_id.isdigit() for j in jobs), jobs
    assert [j.job_id for j in jobs if j.job_id == bob_id] == [bob_id]
