"""Job result handles and status types."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING

from hpc_runner.core.exceptions import JobFailedError, NotSupportedError

if TYPE_CHECKING:
    from hpc_runner.core.array import ArrayRange
    from hpc_runner.core.job import Job
    from hpc_runner.schedulers.base import BaseScheduler


HPC_EXIT_DEP_FAILED = 113
"""Exit code of a job whose dependency guard found a dependency unsatisfied.

On SGE, afterok and afternotok are emulated by a guard at the top of the
dependent's script; when a parent's exit sentinel fails the test the guard
exits with this code, and ``get_status`` reports :attr:`JobStatus.SKIPPED`
("did not run because a dependency was not satisfied"). The code is
non-zero, so the dependent's own dependents are skipped in turn.

The code is reserved: a job command that exits 113 itself is also reported
as SKIPPED, on every scheduler (the local scheduler included, for
symmetry, although it rejects dependencies).

Why 113: exit codes are 0-255; 1-2 are generic failures, 64-78 are
``sysexits.h``, 126-127 are not-executable / not-found, 128+N is death by
signal N, and 255 is the out-of-range catch-all. SGE gives two codes in the
remaining band a meaning (verified against the SGE 8.1.9 man pages
sge_shepherd.8 and submit.1): a job script exiting **99** is re-queued
unless ``FORBID_RESCHEDULE`` is set, and one exiting **100** is put in
*error state* unless ``FORBID_APPERROR`` is set; a ``-hold_jid`` dependent
of a job that exited 100 stays held forever. 113 lies in the free band
(about 79-125 less 99-100) and avoids all of that.
"""


class JobStatus(Enum):
    """Unified job status across schedulers."""

    PENDING = auto()  # Waiting in queue
    RUNNING = auto()  # Currently executing
    COMPLETED = auto()  # Finished successfully
    FAILED = auto()  # Finished with error
    CANCELLED = auto()  # User cancelled
    TIMEOUT = auto()  # Hit time limit
    SKIPPED = auto()  # Did not run because a dependency was not satisfied
    UNKNOWN = auto()  # Cannot determine

    @property
    def is_terminal(self) -> bool:
        """Whether the job has finished and the status will not change."""
        return self in _TERMINAL


_TERMINAL = frozenset(
    {
        JobStatus.COMPLETED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
        JobStatus.TIMEOUT,
        JobStatus.SKIPPED,
    }
)


def status_from_exit_code(exit_code: int) -> JobStatus:
    """The status of a finished job from its exit code.

    0 is COMPLETED, :data:`HPC_EXIT_DEP_FAILED` is SKIPPED, anything else
    FAILED.
    """
    if exit_code == 0:
        return JobStatus.COMPLETED
    if exit_code == HPC_EXIT_DEP_FAILED:
        return JobStatus.SKIPPED
    return JobStatus.FAILED


def wait_for_statuses(
    poll: Callable[[list[str]], Mapping[str, JobStatus]],
    job_ids: Sequence[str],
    *,
    poll_interval: float,
    timeout: float | None,
    unknown_after: float,
    label: str,
) -> dict[str, JobStatus]:
    """Poll until every id in *job_ids* is terminal; return their final statuses.

    *poll* is called once per tick with the ids still outstanding (e.g. a
    scheduler's ``get_statuses``). An id whose status has been UNKNOWN
    continuously for *unknown_after* seconds is returned as UNKNOWN rather
    than polled forever; a status that returns to PENDING or RUNNING resets
    that clock.

    Raises:
        TimeoutError: *timeout* seconds elapsed with ids still outstanding.
    """
    final: dict[str, JobStatus] = {}
    pending = list(dict.fromkeys(job_ids))
    unknown_since: dict[str, float] = {}
    start = time.monotonic()
    while pending:
        statuses = poll(pending)
        now = time.monotonic()
        for job_id in pending:
            status = statuses.get(job_id, JobStatus.UNKNOWN)
            if status.is_terminal:
                final[job_id] = status
            elif status is JobStatus.UNKNOWN:
                since = unknown_since.setdefault(job_id, now)
                if now - since >= unknown_after:
                    final[job_id] = status
            else:
                unknown_since.pop(job_id, None)
        pending = [job_id for job_id in pending if job_id not in final]
        if not pending:
            break
        if timeout is not None and now - start >= timeout:
            raise TimeoutError(f"{label} did not complete within {timeout}s")
        time.sleep(poll_interval)
    return final


#: The placeholder an array job's :attr:`JobResult.stdout_path` keeps for
#: the task id; :meth:`ArrayJobResult.task_stdout_path` substitutes it.
TASK_ID_PLACEHOLDER = "${hpc.task_id}"

_HOST_REF = "${hpc.host}"


@dataclass
class JobResult:
    """A handle on a submitted job.

    Built by ``scheduler.submit(job)`` (with ``job`` set) or by
    ``scheduler.result(job_id)`` (``job`` is None). An interactive run has
    already finished when its handle is returned: ``job_id`` is None and
    ``exit_code`` holds its exit status.

    ``stdout_path`` and ``stderr_path`` are recorded at submit time from
    the job's output patterns (D9). ``stderr_path`` is None when stderr is
    merged into stdout. Both are None for an interactive run (its output
    went to the terminal), when the pattern uses ``${hpc.host}`` (not known at
    submit time), and on a handle from ``scheduler.result(job_id)``, which
    does not know the job's patterns. For an array job they contain the
    literal :data:`TASK_ID_PLACEHOLDER`; see
    :meth:`ArrayJobResult.task_stdout_path`.
    """

    scheduler: BaseScheduler
    job_id: str | None  # None for a completed interactive run
    job: Job | None = None  # None when rebuilt from an id via scheduler.result()
    exit_code: int | None = None  # set for interactive runs (the run already finished)
    stdout_path: Path | None = None
    stderr_path: Path | None = None

    @property
    def status(self) -> JobStatus:
        """Get current job status (queries scheduler)."""
        if self.exit_code is not None:
            return JobStatus.COMPLETED if self.exit_code == 0 else JobStatus.FAILED
        if self.job_id is None:
            return JobStatus.UNKNOWN
        return self.scheduler.get_status(self.job_id)

    @property
    def is_complete(self) -> bool:
        """Check if job has finished (success or failure)."""
        return self.status.is_terminal

    @property
    def returncode(self) -> int | None:
        """Get exit code (None if not complete)."""
        if self.exit_code is not None:
            return self.exit_code
        if self.job_id is None or not self.is_complete:
            return None
        return self.scheduler.get_exit_code(self.job_id)

    def wait(
        self,
        poll_interval: float = 5.0,
        timeout: float | None = None,
        unknown_after: float = 60.0,
    ) -> JobStatus:
        """Block until the job reaches a terminal status.

        An interactive run has already completed, so this returns at once.

        Args:
            poll_interval: Seconds between status checks.
            timeout: Max seconds to wait (None = forever).
            unknown_after: Return UNKNOWN once the status has been UNKNOWN
                continuously for this many seconds, instead of polling a job
                the scheduler can no longer report on.

        Returns:
            The final status: terminal, or UNKNOWN (see *unknown_after*).

        Raises:
            TimeoutError: *timeout* elapsed first.
        """
        if self.job_id is None:
            return self.status
        job_id = self.job_id
        return wait_for_statuses(
            lambda ids: {job_id: self.status},
            [job_id],
            poll_interval=poll_interval,
            timeout=timeout,
            unknown_after=unknown_after,
            label=f"Job {job_id}",
        )[job_id]

    def result(self, poll_interval: float = 5.0, timeout: float | None = None) -> int | None:
        """Wait for the job, then return its exit code.

        ``concurrent.futures`` style: a job that did not finish COMPLETED
        (FAILED, SKIPPED, CANCELLED, TIMEOUT, or UNKNOWN) raises rather than
        returning a code the caller must check.

        Raises:
            JobFailedError: The final status is not COMPLETED; the status is
                carried on the exception.
            TimeoutError: *timeout* elapsed first.
        """
        status = self.wait(poll_interval=poll_interval, timeout=timeout)
        exit_code = self.returncode
        if status != JobStatus.COMPLETED:
            raise JobFailedError(status, self.job_id, exit_code)
        return exit_code

    def cancel(self) -> bool:
        """Cancel the job. A completed interactive run cannot be cancelled."""
        if self.job_id is None:
            return False
        return self.scheduler.cancel(self.job_id)

    def read_stdout(self, tail: int | None = None) -> str | None:
        """The job's stdout (the last *tail* lines if given).

        Returns:
            The file's content, or None when there is no file: an
            interactive run, a handle without a recorded path, or a batch
            job whose output file does not exist (yet).

        Raises:
            ValueError: The output path depends on ``${hpc.host}``.
        """
        self._check_known(self.stdout_path, "stdout")
        return _read(self.stdout_path, tail)

    def read_stderr(self, tail: int | None = None) -> str | None:
        """The job's stderr; None when stderr is merged into stdout (see :meth:`read_stdout`).

        Raises:
            ValueError: The output path depends on ``${hpc.host}``.
        """
        self._check_known(self.stderr_path, "stderr")
        return _read(self.stderr_path, tail)

    def _check_known(self, path: Path | None, stream: str) -> None:
        if path is not None or self.job is None or self.job.interactive:
            return
        pattern = getattr(self.job, stream)
        if pattern is not None and _HOST_REF in pattern:
            raise ValueError("output path depends on ${hpc.host}, not known at submit time")


def _read(path: Path | None, tail: int | None) -> str | None:
    if path is None or not path.exists():
        return None
    content = path.read_text()
    if tail is not None:
        lines = content.splitlines()
        content = "\n".join(lines[max(len(lines) - tail, 0) :])
    return content


@dataclass(kw_only=True)
class ArrayJobResult(JobResult):
    """A handle on a submitted array job; ``job_id`` is the base id."""

    array: ArrayRange

    def _base_id(self) -> str:
        if self.job_id is None:
            raise ValueError("array job result has no job id")
        return self.job_id

    def task_id(self, index: int) -> str:
        """Get job ID for specific array task."""
        return f"{self._base_id()}.{index}"

    def task_status(self, index: int) -> JobStatus:
        """The status of task *index* itself (not of the array as a whole)."""
        return self.scheduler.get_status(self.task_id(index))

    def task_stdout_path(self, index: int) -> Path | None:
        """Task *index*'s stdout file (see :attr:`JobResult.stdout_path`)."""
        return _task_path(self.stdout_path, index)

    def task_stderr_path(self, index: int) -> Path | None:
        """Task *index*'s stderr file; None when stderr is merged into stdout."""
        return _task_path(self.stderr_path, index)

    # An array job has one output file per task, so the whole-job readers
    # are refused; read a task's file with read_task_stdout(n).
    def read_stdout(self, tail: int | None = None) -> str | None:
        """Not defined for array jobs; use :meth:`read_task_stdout`."""
        raise NotSupportedError("array job: use read_task_stdout(n)")

    def read_stderr(self, tail: int | None = None) -> str | None:
        """Not defined for array jobs; use :meth:`read_task_stderr`."""
        raise NotSupportedError("array job: use read_task_stderr(n)")

    def read_task_stdout(self, index: int, tail: int | None = None) -> str | None:
        """Task *index*'s stdout, as :meth:`JobResult.read_stdout`.

        Raises:
            ValueError: The output path depends on ``${hpc.host}``.
        """
        path = self.task_stdout_path(index)
        self._check_known(path, "stdout")
        return _read(path, tail)

    def read_task_stderr(self, index: int, tail: int | None = None) -> str | None:
        """Task *index*'s stderr; None when stderr is merged into stdout.

        Raises:
            ValueError: The output path depends on ``${hpc.host}``.
        """
        path = self.task_stderr_path(index)
        self._check_known(path, "stderr")
        return _read(path, tail)

    def task_ids(self) -> list[str]:
        """The job id of every task, in index order."""
        return [self.task_id(index) for index in self.array.indices]

    # Returns per-task statuses rather than one aggregate, so it is not a
    # drop-in for JobResult.wait(); the pipeline lifecycle work (D16) may
    # revisit this.
    def wait(  # type: ignore[override]
        self,
        poll_interval: float = 5.0,
        timeout: float | None = None,
        unknown_after: float = 60.0,
    ) -> dict[int, JobStatus]:
        """Wait for every task; returns each task's final status by index.

        One ``get_statuses`` call per tick covers all outstanding tasks.
        *timeout* and *unknown_after* are as for :meth:`JobResult.wait`.

        Raises:
            TimeoutError: *timeout* elapsed first.
        """
        by_id = dict(zip(self.task_ids(), self.array.indices))
        final = wait_for_statuses(
            self.scheduler.get_statuses,
            list(by_id),
            poll_interval=poll_interval,
            timeout=timeout,
            unknown_after=unknown_after,
            label=f"Array job {self.job_id}",
        )
        return {index: final[task_id] for task_id, index in by_id.items()}

    def result(self, poll_interval: float = 5.0, timeout: float | None = None) -> int | None:
        """Not defined for array jobs; use :meth:`wait` for per-task statuses."""
        raise NotSupportedError("result() is not defined for array jobs; use wait()")

    def cancel(self) -> bool:
        """Cancel all array tasks."""
        return self.scheduler.cancel(self._base_id())


def _task_path(path: Path | None, index: int) -> Path | None:
    if path is None:
        return None
    return Path(str(path).replace(TASK_ID_PLACEHOLDER, str(index)))
