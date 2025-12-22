"""Abstract base class for scheduler implementations."""

from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hpc_runner.core.job import Job
    from hpc_runner.core.job_array import JobArray
    from hpc_runner.core.job_info import JobInfo
    from hpc_runner.core.result import ArrayJobResult, JobResult, JobStatus


class BaseScheduler(ABC):
    """Abstract base class for scheduler implementations.

    Each scheduler must implement:
    - submit(): Submit a job
    - submit_array(): Submit an array job
    - cancel(): Cancel a job
    - get_status(): Query job status
    - get_exit_code(): Get job exit code
    - get_output_path(): Get output file path
    - generate_script(): Generate job script
    - build_submit_command(): Build submission command

    TUI Monitor API (for hpc monitor):
    - list_active_jobs(): List running/pending jobs
    - list_completed_jobs(): List historical jobs
    - has_accounting(): Check if history is available
    - get_job_details(): Get full details for one job
    """

    name: str  # e.g., "sge", "slurm", "local"

    @abstractmethod
    def submit(self, job: "Job", interactive: bool = False) -> "JobResult":
        """Submit a job to the scheduler.

        Args:
            job: Job specification
            interactive: Run interactively (blocking)

        Returns:
            JobResult with job ID and methods
        """

    @abstractmethod
    def submit_array(self, array: "JobArray") -> "ArrayJobResult":
        """Submit an array job."""

    @abstractmethod
    def cancel(self, job_id: str) -> bool:
        """Cancel a job by ID."""

    @abstractmethod
    def get_status(self, job_id: str) -> "JobStatus":
        """Get current status of a job."""

    @abstractmethod
    def get_exit_code(self, job_id: str) -> int | None:
        """Get exit code of completed job."""

    @abstractmethod
    def get_output_path(self, job_id: str, stream: str) -> Path | None:
        """Get path to output file.

        Args:
            job_id: Job ID
            stream: "stdout" or "stderr"
        """

    @abstractmethod
    def generate_script(self, job: "Job") -> str:
        """Generate job script content."""

    @abstractmethod
    def build_submit_command(self, job: "Job") -> list[str]:
        """Build the submission command (e.g., qsub args)."""

    def get_scheduler_args(self, job: "Job") -> list[str]:
        """Get scheduler-specific raw args from job."""
        return getattr(job, f"{self.name}_args", [])

    # -------------------------------------------------------------------------
    # TUI Monitor API
    # -------------------------------------------------------------------------

    @abstractmethod
    def list_active_jobs(
        self,
        user: str | None = None,
        status: set["JobStatus"] | None = None,
        queue: str | None = None,
    ) -> list["JobInfo"]:
        """List currently active (running/pending/held) jobs.

        Args:
            user: Filter by username. None = all users.
            status: Filter by status set. None = all active statuses.
            queue: Filter by queue name. None = all queues.

        Returns:
            List of JobInfo for matching active jobs.
        """

    @abstractmethod
    def list_completed_jobs(
        self,
        user: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        exit_code: int | None = None,
        queue: str | None = None,
        limit: int = 100,
    ) -> list["JobInfo"]:
        """List completed jobs from scheduler accounting.

        Args:
            user: Filter by username. None = all users.
            since: Jobs completed after this time.
            until: Jobs completed before this time.
            exit_code: Filter by exit code. None = all.
            queue: Filter by queue name. None = all queues.
            limit: Maximum number of jobs to return.

        Returns:
            List of JobInfo for matching completed jobs.

        Raises:
            AccountingNotAvailable: If scheduler accounting is not enabled.
        """

    @abstractmethod
    def has_accounting(self) -> bool:
        """Check if job accounting/history is available.

        Returns:
            True if list_completed_jobs() will work, False otherwise.
        """

    @abstractmethod
    def get_job_details(self, job_id: str) -> "JobInfo":
        """Get detailed information for a single job.

        Args:
            job_id: The job identifier.

        Returns:
            JobInfo with all available details.

        Raises:
            JobNotFoundError: If job doesn't exist.
        """
