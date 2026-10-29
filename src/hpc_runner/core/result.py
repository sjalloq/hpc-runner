"""Job result handles and status types."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING

from hpc_runner.core.exceptions import JobFailedError, NotSupportedError

if TYPE_CHECKING:
    from hpc_runner.core.array import ArrayRange
    from hpc_runner.core.job import Job
    from hpc_runner.schedulers.base import BaseScheduler


class JobStatus(Enum):
    """Unified job status across schedulers."""

    PENDING = auto()  # Waiting in queue
    RUNNING = auto()  # Currently executing
    COMPLETED = auto()  # Finished successfully
    FAILED = auto()  # Finished with error
    CANCELLED = auto()  # User cancelled
    TIMEOUT = auto()  # Hit time limit
    UNKNOWN = auto()  # Cannot determine


_TERMINAL = (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.TIMEOUT)


@dataclass
class JobResult:
    """A handle on a submitted job.

    Built by ``scheduler.submit(job)`` (with ``job`` set) or by
    ``scheduler.result(job_id)`` (``job`` is None). An interactive run has
    already finished when its handle is returned: ``job_id`` is None and
    ``exit_code`` holds its exit status.
    """

    scheduler: BaseScheduler
    job_id: str | None  # None for a completed interactive run
    job: Job | None = None  # None when rebuilt from an id via scheduler.result()
    exit_code: int | None = None  # set for interactive runs (the run already finished)

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
        return self.status in _TERMINAL

    @property
    def returncode(self) -> int | None:
        """Get exit code (None if not complete)."""
        if self.exit_code is not None:
            return self.exit_code
        if self.job_id is None or not self.is_complete:
            return None
        return self.scheduler.get_exit_code(self.job_id)

    def wait(self, poll_interval: float = 5.0, timeout: float | None = None) -> JobStatus:
        """Block until job completes.

        An interactive run has already completed, so this returns at once.

        Args:
            poll_interval: Seconds between status checks
            timeout: Max seconds to wait (None = forever)

        Returns:
            Final job status
        """
        if self.job_id is None:
            return self.status
        start = time.time()
        while not self.is_complete:
            if timeout is not None and (time.time() - start) >= timeout:
                raise TimeoutError(f"Job {self.job_id} did not complete within {timeout}s")
            time.sleep(poll_interval)
        return self.status

    def result(self, poll_interval: float = 5.0, timeout: float | None = None) -> int | None:
        """Wait for the job, then return its exit code.

        ``concurrent.futures`` style: a job that did not finish COMPLETED
        raises rather than returning a code the caller must check.

        Raises:
            JobFailedError: The final status is not COMPLETED.
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

    def stdout_path(self) -> Path | None:
        """Get path to stdout file."""
        if self.job_id is None:
            return None
        return self.scheduler.get_output_path(self.job_id, "stdout")

    def stderr_path(self) -> Path | None:
        """Get path to stderr file."""
        if self.job_id is None:
            return None
        return self.scheduler.get_output_path(self.job_id, "stderr")

    def read_stdout(self, tail: int | None = None) -> str:
        """Read stdout content."""
        return _read(self.stdout_path(), tail)

    def read_stderr(self, tail: int | None = None) -> str:
        """Read stderr content."""
        return _read(self.stderr_path(), tail)


def _read(path: Path | None, tail: int | None) -> str:
    if not path or not path.exists():
        return ""
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
        """Get status of specific array task."""
        return self.scheduler.get_status(self.task_id(index))

    # DECISION NEEDED: this returns per-task statuses, so it is not a drop-in
    # for JobResult.wait() (-> JobStatus); should it aggregate instead?
    def wait(  # type: ignore[override]
        self, poll_interval: float = 5.0, timeout: float | None = None
    ) -> dict[int, JobStatus]:
        """Wait for all array tasks to complete; returns each task's status."""
        results: dict[int, JobStatus] = {}
        pending = set(self.array.indices)
        start = time.time()

        while pending:
            for idx in list(pending):
                status = self.task_status(idx)
                if status in _TERMINAL:
                    results[idx] = status
                    pending.remove(idx)
            if pending:
                if timeout is not None and (time.time() - start) >= timeout:
                    raise TimeoutError(
                        f"Array job {self.job_id} did not complete within {timeout}s"
                    )
                time.sleep(poll_interval)

        return results

    def result(self, poll_interval: float = 5.0, timeout: float | None = None) -> int | None:
        """Not defined for array jobs; use :meth:`wait` for per-task statuses."""
        # DECISION NEEDED: what should result() mean for an array job?
        raise NotSupportedError("result() is not defined for array jobs; use wait()")

    def cancel(self) -> bool:
        """Cancel all array tasks."""
        return self.scheduler.cancel(self._base_id())
