"""Job result and status types."""

from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING

from hpc_runner.core.exceptions import JobFailedError

if TYPE_CHECKING:
    from hpc_runner.core.job import Job
    from hpc_runner.core.job_array import JobArray
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


@dataclass
class JobResult:
    """Result of a submitted job.

    Provides methods to query status, wait for completion,
    and access output.
    """

    job_id: str
    scheduler: "BaseScheduler"
    job: "Job"

    _cached_status: JobStatus | None = field(default=None, repr=False)
    _exit_code: int | None = field(default=None, repr=False)  # For interactive jobs

    @property
    def status(self) -> JobStatus:
        """Get current job status (queries scheduler)."""
        if self._exit_code is not None:
            return JobStatus.COMPLETED if self._exit_code == 0 else JobStatus.FAILED
        return self.scheduler.get_status(self.job_id)

    @property
    def is_complete(self) -> bool:
        """Check if job has finished (success or failure)."""
        return self.status in (
            JobStatus.COMPLETED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.TIMEOUT,
        )

    @property
    def returncode(self) -> int | None:
        """Get exit code (None if not complete)."""
        # For interactive jobs, use cached exit code
        if self._exit_code is not None:
            return self._exit_code
        if not self.is_complete:
            return None
        return self.scheduler.get_exit_code(self.job_id)

    def wait(self, poll_interval: float = 5.0, timeout: float | None = None) -> JobStatus:
        """Block until job completes.

        Args:
            poll_interval: Seconds between status checks
            timeout: Max seconds to wait (None = forever)

        Returns:
            Final job status
        """
        import time

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
        """Cancel the job."""
        return self.scheduler.cancel(self.job_id)

    def stdout_path(self) -> Path | None:
        """Get path to stdout file."""
        return self.scheduler.get_output_path(self.job_id, "stdout")

    def stderr_path(self) -> Path | None:
        """Get path to stderr file."""
        return self.scheduler.get_output_path(self.job_id, "stderr")

    def read_stdout(self, tail: int | None = None) -> str:
        """Read stdout content."""
        path = self.stdout_path()
        if not path or not path.exists():
            return ""
        content = path.read_text()
        if tail is not None:
            lines = content.splitlines()
            content = "\n".join(lines[max(len(lines) - tail, 0) :])
        return content

    def read_stderr(self, tail: int | None = None) -> str:
        """Read stderr content."""
        path = self.stderr_path()
        if not path or not path.exists():
            return ""
        content = path.read_text()
        if tail is not None:
            lines = content.splitlines()
            content = "\n".join(lines[max(len(lines) - tail, 0) :])
        return content


@dataclass
class ArrayJobResult:
    """Result of a submitted array job."""

    base_job_id: str
    scheduler: "BaseScheduler"
    array: "JobArray"

    def task_id(self, index: int) -> str:
        """Get job ID for specific array task."""
        return f"{self.base_job_id}.{index}"

    def task_status(self, index: int) -> JobStatus:
        """Get status of specific array task."""
        return self.scheduler.get_status(self.task_id(index))

    def wait(self, poll_interval: float = 5.0) -> dict[int, JobStatus]:
        """Wait for all array tasks to complete."""
        import time

        results: dict[int, JobStatus] = {}
        pending = set(self.array.indices)

        while pending:
            for idx in list(pending):
                status = self.task_status(idx)
                if status in (
                    JobStatus.COMPLETED,
                    JobStatus.FAILED,
                    JobStatus.CANCELLED,
                    JobStatus.TIMEOUT,
                ):
                    results[idx] = status
                    pending.remove(idx)
            if pending:
                time.sleep(poll_interval)

        return results

    def cancel(self) -> bool:
        """Cancel all array tasks."""
        return self.scheduler.cancel(self.base_job_id)
