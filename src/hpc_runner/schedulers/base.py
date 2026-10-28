"""Base scheduler with rendering protocol."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from types import EllipsisType
from typing import TYPE_CHECKING, Any

from hpc_runner.core.descriptors import SchedulerArg
from hpc_runner.schedulers import _subprocess

if TYPE_CHECKING:
    import subprocess

    from hpc_runner.core.job import Job
    from hpc_runner.core.job_array import JobArray
    from hpc_runner.core.job_info import JobInfo
    from hpc_runner.core.result import ArrayJobResult, JobResult, JobStatus


class BaseScheduler(ABC):
    """Abstract base class for HPC schedulers.

    Subclasses must:
    1. Define `name` class attribute
    2. Populate `ARG_RENDERERS` dict mapping Job attribute names to SchedulerArg instances
    3. Implement abstract methods for job submission and management

    The rendering protocol:
    - `render_directives(job)` - Returns list of script directives
    - `render_args(job)` - Returns list of command-line arguments

    Both methods iterate over job.iter_attributes() and use ARG_RENDERERS
    to convert values to scheduler-specific syntax.
    """

    name: str = ""

    # Subclasses populate this in __init__ with config-driven values
    ARG_RENDERERS: dict[str, SchedulerArg[Any]] = {}

    # Default timeout (seconds) for query and submit commands.
    COMMAND_TIMEOUT: float = 60.0

    # =========================================================================
    # Subprocess seam
    # =========================================================================

    def _run(
        self,
        argv: Sequence[str],
        *,
        timeout: float | EllipsisType | None = ...,
        check: bool = True,
        capture: bool = True,
        cwd: Path | None = None,
        input: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run a scheduler command through the one subprocess seam.

        *timeout* defaults to ``COMMAND_TIMEOUT``; pass ``None`` explicitly
        for commands that block for a job's lifetime (interactive runs).
        See :func:`hpc_runner.schedulers._subprocess.run_command` for the
        exceptions raised.
        """
        effective = self.COMMAND_TIMEOUT if timeout is ... else timeout
        return _subprocess.run_command(
            argv, timeout=effective, check=check, capture=capture, cwd=cwd, input=input
        )

    # =========================================================================
    # Rendering Protocol
    # =========================================================================

    def render_directives(self, job: Job) -> list[str]:
        """Render job attributes as script directives.

        Iterates over job's renderable attributes and uses ARG_RENDERERS
        to convert each to the appropriate directive format.

        Args:
            job: The job to render

        Returns:
            List of directive strings (e.g., ['#$ -N jobname', '#$ -pe smp 4'])
        """
        directives: list[str] = []

        for attr_name, value in job.iter_attributes():
            renderer = self.ARG_RENDERERS.get(attr_name)
            if renderer is None:
                continue

            directive = renderer.to_directive(value)
            if directive is not None:
                directives.append(directive)

        return directives

    def render_args(self, job: Job) -> list[str]:
        """Render job attributes as command-line arguments.

        Iterates over job's renderable attributes and uses ARG_RENDERERS
        to convert each to command-line argument format.

        Args:
            job: The job to render

        Returns:
            List of argument strings (e.g., ['-N', 'jobname', '-pe', 'smp', '4'])
        """
        args: list[str] = []

        for attr_name, value in job.iter_attributes():
            renderer = self.ARG_RENDERERS.get(attr_name)
            if renderer is None:
                continue

            args.extend(renderer.to_args(value))

        return args

    # =========================================================================
    # Abstract Methods - Subclasses must implement
    # =========================================================================

    @abstractmethod
    def submit(self, job: Job, interactive: bool = False, keep_script: bool = False) -> JobResult:
        """Submit a job to the scheduler.

        Args:
            job: Job to submit.
            interactive: If True, run interactively.
            keep_script: If True, don't delete job script after submission.
        """

    @abstractmethod
    def submit_array(self, array: JobArray) -> ArrayJobResult:
        """Submit an array job."""

    @abstractmethod
    def cancel(self, job_id: str) -> bool:
        """Cancel a job."""

    @abstractmethod
    def get_status(self, job_id: str) -> JobStatus:
        """Get job status."""

    @abstractmethod
    def get_exit_code(self, job_id: str) -> int | None:
        """Get job exit code."""

    @abstractmethod
    def generate_script(self, job: Job, array_range: str | None = None) -> str:
        """Generate submission script."""

    @abstractmethod
    def build_submit_command(self, job: Job) -> list[str]:
        """Build submission command line."""

    @abstractmethod
    def build_interactive_command(self, job: Job) -> list[str]:
        """Build interactive execution command."""

    # =========================================================================
    # Optional Methods - Override if scheduler supports these
    # =========================================================================

    def generate_interactive_script(self, job: Job, script_path: str) -> str:
        """Generate wrapper script for interactive jobs.

        By default, falls back to the standard batch script.
        Override in subclasses that need a different template for
        interactive sessions (e.g. SGE uses qrsh with no #$ directives).

        Args:
            job: Job to generate script for.
            script_path: Path where the script will be written.

        Returns:
            Script content as a string.
        """
        return self.generate_script(job)

    def get_output_path(self, job_id: str, stream: str) -> Path | None:
        """Get path to output file.

        Args:
            job_id: Job ID
            stream: "stdout" or "stderr"

        Returns:
            Path to output file, or None if not determinable.
        """
        return None

    def get_scheduler_args(self, job: Job) -> list[str]:
        """Get scheduler-specific raw args from job."""
        return getattr(job, f"{self.name}_args", [])

    def list_active_jobs(
        self,
        user: str | None = None,
        status: set[JobStatus] | None = None,
        queue: str | None = None,
    ) -> list[JobInfo]:
        """List active jobs. Override in subclass."""
        return []

    def list_completed_jobs(
        self,
        user: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        exit_code: int | None = None,
        queue: str | None = None,
        limit: int = 100,
    ) -> list[JobInfo]:
        """List completed jobs from accounting. Override in subclass."""
        return []

    def has_accounting(self) -> bool:
        """Check if job accounting/history is available."""
        return False

    def get_job_details(self, job_id: str) -> tuple[JobInfo, dict[str, object]]:
        """Get detailed information for a single job.

        Args:
            job_id: The job identifier.

        Returns:
            Tuple of (JobInfo, extra_details dict).

        Raises:
            JobNotFoundError: If job doesn't exist.
            NotImplementedError: If not implemented by scheduler.
        """
        raise NotImplementedError(f"{self.name} does not implement get_job_details()")
