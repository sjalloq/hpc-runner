"""What Slurm's own tests share: submitting, and reading Slurm's commands directly.

The tests check hpc-runner against what ``scontrol``, ``sacct`` and ``squeue``
say themselves, so nothing here goes through hpc-runner's parsers.
"""

from __future__ import annotations

import threading
import time

from hpc_runner.core.resolve import resolve
from hpc_runner.core.result import JobResult
from hpc_runner.core.spec import JobSpec
from tests.integration.cluster import ACCOUNTING_TIMEOUT, Cluster

#: ``tester``'s uid (testbed/common/users.sh): ``sacct`` names it in ``CANCELLED by <uid>``.
TESTER_UID = 1003
#: Allocates 200 MB, then prints how much; for a job given less memory than that.
ALLOC_200M = 'python3 -c "b = bytearray(200 * 1024 * 1024); print(len(b))"'
#: What :data:`ALLOC_200M` prints when it is allowed to finish.
ALLOCATED = str(200 * 1024 * 1024)
#: A ``time = "1m"`` job is killed between one and two minutes after it starts.
TIME_LIMIT_KILL_TIMEOUT = 180.0


def submit(cluster: Cluster, spec: JobSpec) -> JobResult:
    return cluster.scheduler().submit(resolve(spec, cluster.config(), cluster.host()))


def submit_waited(cluster: Cluster, spec: JobSpec, limit: float = 120) -> JobResult:
    """``submit(job, wait=True)`` of *spec*, which must have a name of its own.

    The call has no timeout, so the job is cancelled by name if it is still
    there after *limit* seconds: the call then returns and the test fails
    on the status it did not expect.
    """
    assert spec.name is not None
    job = resolve(spec, cluster.config(), cluster.host())
    watchdog = threading.Timer(
        limit, lambda: cluster.run(["scancel", "-u", "tester", "-n", job.name], check=False)
    )
    watchdog.start()
    try:
        return cluster.scheduler().submit(job, wait=True)
    finally:
        watchdog.cancel()


def job_id(result: JobResult) -> str:
    assert result.job_id is not None
    return result.job_id


def scontrol_job(cluster: Cluster, job_id: str) -> dict[str, str]:
    """The ``Key=Value`` words of ``scontrol show job <job_id>``."""
    out = cluster.run(["scontrol", "show", "job", job_id]).stdout
    return {key: value for key, sep, value in (w.partition("=") for w in out.split()) if sep}


def sacct(cluster: Cluster, job_id: str, fields: str, *, job_row_only: bool = False) -> list[str]:
    """The lines of ``sacct -P -n -j <job_id> --format=<fields>``; with ``-X`` if asked."""
    argv = ["sacct", "-P", "-n", "-j", job_id, f"--format={fields}"]
    if job_row_only:
        argv.insert(1, "-X")
    return cluster.run(argv).stdout.splitlines()


def wait_sacct(
    cluster: Cluster,
    job_id: str,
    fields: str,
    expected: list[str],
    *,
    job_row_only: bool = False,
    timeout: float = ACCOUNTING_TIMEOUT,
) -> None:
    """Wait until :func:`sacct` gives exactly *expected*; fail with what it gave instead.

    A row is not whole at once: the ``.batch`` row of a job Slurm killed says
    ``0:0`` for a moment before it says ``0:15``, and 22.05 can show an
    early row with half its fields.
    """
    deadline = time.monotonic() + timeout
    while (seen := sacct(cluster, job_id, fields, job_row_only=job_row_only)) != expected:
        assert time.monotonic() < deadline, f"sacct has {seen}, not {expected}, after {timeout}s"
        time.sleep(0.5)


def wait_rows(cluster: Cluster, job_id: str, expected: list[str]) -> None:
    """Wait until ``sacct`` has exactly the ``JobID|State|ExitCode`` rows *expected*."""
    wait_sacct(cluster, job_id, "JobID,State,ExitCode", expected)


def squeue_state(cluster: Cluster, job_id: str) -> str:
    """``<state>|<reason>`` of a live job from ``squeue``; ``""`` once it has left."""
    argv = ["squeue", "-h", "-j", job_id, "-o", "%T|%r"]
    return cluster.run(argv, check=False).stdout.strip()


def wait_squeue_state(cluster: Cluster, job_id: str, expected: str, timeout: float = 30) -> None:
    """Wait until :func:`squeue_state` is *expected* (a pending job's reason is
    ``None`` until the scheduler has looked at it)."""
    deadline = time.monotonic() + timeout
    while (seen := squeue_state(cluster, job_id)) != expected:
        assert time.monotonic() < deadline, f"job {job_id} is {seen!r}, not {expected!r}"
        time.sleep(0.5)
