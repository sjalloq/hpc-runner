"""The waited run on Slurm: ``hpc run --batch --wait`` and ``submit(job, wait=True)``
submit with ``sbatch --wait`` and exit with what it returns.

That is the command's own exit code, as a shell would have had it (137
stays 137, a segfault is 139), which ``sacct`` cannot give: it prints a code
above 128 as 128 less. For a job Slurm ended itself ``sbatch --wait`` returns
1, and the status says why. A job that never ran is not a success: ``hpc
run`` exits 1.
"""

from __future__ import annotations

import subprocess

import pytest

from hpc_runner.core.result import JobStatus
from hpc_runner.core.spec import JobSpec
from tests.integration.cluster import Cluster
from tests.integration.helpers import submitted_id
from tests.integration.slurm.slurm import (
    ALLOC_200M,
    TIME_LIMIT_KILL_TIMEOUT,
    job_id,
    submit,
    submit_waited,
    wait_rows,
)

SEGFAULT = "python3 -c 'import os, signal; os.kill(os.getpid(), signal.SIGSEGV)'"

#: id -> (hpc run options, the command, exit code, status, sacct's ExitCode for the job)
RUNS: dict[str, tuple[list[str], str, int, JobStatus, str]] = {
    "exit 3": ([], "exit 3", 3, JobStatus.FAILED, "FAILED|3:0"),
    "exit 137": ([], "exit 137", 137, JobStatus.FAILED, "FAILED|9:0"),
    "segfault": ([], SEGFAULT, 139, JobStatus.FAILED, "FAILED|11:0"),
    "over its memory": (
        ["--mem", "50M"],
        ALLOC_200M,
        1,
        JobStatus.KILLED,
        "OUT_OF_MEMORY|0:125",
    ),
}


def _hpc_run_waited(cluster: Cluster, *args: str) -> list[str]:
    return [
        str(cluster.hpc_cli),
        "run",
        "--batch",
        "--wait",
        "--directory",
        str(cluster.workdir),
        *args,
    ]


@pytest.mark.parametrize("run", sorted(RUNS))
def test_hpc_run_wait_exits_as_the_job_did(cluster: Cluster, run: str) -> None:
    options, command, code, status, row = RUNS[run]
    argv = _hpc_run_waited(cluster, *options, "sh", "-c", command)
    proc = cluster.run(argv, check=False, timeout=120)
    assert proc.returncode == code, proc.stdout + proc.stderr
    assert f"Job completed with status: {status.name}" in proc.stdout
    job = submitted_id(proc.stdout)
    wait_rows(cluster, job, [f"{job}|{row}", f"{job}.batch|{row}"])


@pytest.mark.parametrize("run", sorted(RUNS))
def test_submit_wait_gives_the_same(cluster: Cluster, run: str) -> None:
    options, command, code, status, row = RUNS[run]
    mem = options[1] if options else None
    result = submit_waited(cluster, JobSpec(command, name="waited", mem=mem))
    assert (result.final_status, result.exit_code) == (status, code)
    assert (result.status, result.returncode) == (status, code)
    job = job_id(result)
    wait_rows(cluster, job, [f"{job}|{row}", f"{job}.batch|{row}"])


def test_a_time_limit_exits_1_with_timeout(cluster: Cluster) -> None:
    """Killed at its time limit: both forms give 1 and TIMEOUT. The two jobs run
    side by side, since each takes over a minute to be killed."""
    argv = _hpc_run_waited(cluster, "--time", "1m", "sleep", "300")
    cli = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        spec = JobSpec("sleep 300", name="waitlimit", time="1m")
        result = submit_waited(cluster, spec, limit=TIME_LIMIT_KILL_TIMEOUT)
        out, err = cli.communicate(timeout=TIME_LIMIT_KILL_TIMEOUT)
    finally:
        cli.kill()
    assert (result.final_status, result.exit_code) == (JobStatus.TIMEOUT, 1)
    assert cli.returncode == 1, out + err
    assert "Job completed with status: TIMEOUT" in out
    for job in (job_id(result), submitted_id(out)):
        wait_rows(cluster, job, [f"{job}|TIMEOUT|0:0", f"{job}.batch|CANCELLED|0:15"])


def test_a_skipped_job_exits_1(cluster: Cluster) -> None:
    """The afterok child of a failed job never runs. ``sbatch --wait`` returns 0
    for it; ``hpc run`` prints SKIPPED and exits 1."""
    parent = submit(cluster, JobSpec("exit 1", name="parent"))
    assert cluster.wait_for(job_id(parent), JobStatus.FAILED) is JobStatus.FAILED
    argv = _hpc_run_waited(cluster, "--depend", f"afterok:{job_id(parent)}", "true")
    proc = cluster.run(argv, check=False, timeout=120)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "Job completed with status: SKIPPED" in proc.stdout
    job = submitted_id(proc.stdout)
    wait_rows(cluster, job, [f"{job}|CANCELLED|0:0"])

    result = submit_waited(cluster, JobSpec("true", name="waitskip").after(parent))
    assert result.final_status is JobStatus.SKIPPED
    assert result.status is JobStatus.SKIPPED
    assert result.exit_code is None
