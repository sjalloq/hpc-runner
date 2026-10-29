"""Base scheduler with rendering protocol."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from types import EllipsisType
from typing import TYPE_CHECKING, Any, ClassVar

from hpc_runner.core.descriptors import SchedulerArg
from hpc_runner.core.job import RENDER_ORDER
from hpc_runner.schedulers import _subprocess

if TYPE_CHECKING:
    import subprocess

    from hpc_runner.core.config import HPCConfig
    from hpc_runner.core.job import Job
    from hpc_runner.core.job_info import JobInfo
    from hpc_runner.core.result import JobResult, JobStatus


class BaseScheduler(ABC):
    """Abstract base class for HPC schedulers.

    Subclasses must:
    1. Define `name` class attribute
    2. Populate `ARG_RENDERERS` mapping Job attribute names to SchedulerArg instances
    3. Implement the abstract methods below

    The contract is one shape for every scheduler: ``submit(job)`` reads
    ``job.interactive`` and ``job.array`` itself; ``preview(job)`` returns the
    script ``submit`` would write. ``render_directives(job)`` walks
    ``RENDER_ORDER`` and uses ``ARG_RENDERERS`` to produce script directives.
    """

    name: str = ""

    # The keys this backend reads from ``[schedulers.<name>]``, key -> the
    # Python type its value must have. Config validation rejects any other.
    CONFIG_KEYS: ClassVar[Mapping[str, type]] = {}

    # Subclasses populate this in __init__ with config-driven values
    ARG_RENDERERS: dict[str, SchedulerArg[Any]] = {}

    # Default timeout (seconds) for query and submit commands.
    COMMAND_TIMEOUT: float = 60.0

    def __init__(self, config: HPCConfig) -> None:
        self.config = config

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

        Walks ``RENDER_ORDER``, skipping unset (None) attributes, and uses
        ARG_RENDERERS to convert each to the scheduler's directive format.
        A renderer returns None for a value it does not render (e.g. False
        for ``use_cwd``/``inherit_env``, an explicit opt-out).

        Returns:
            List of directive strings (e.g., ['#$ -N jobname', '#$ -pe smp 4'])
        """
        directives: list[str] = []
        for attr_name in RENDER_ORDER:
            value = getattr(job, attr_name)
            if value is None:
                continue
            renderer = self.ARG_RENDERERS.get(attr_name)
            if renderer is None:
                continue
            directive = renderer.to_directive(value)
            if directive is not None:
                directives.append(directive)
        return directives

    def _render_interactive_args(self, job: Job) -> list[str]:
        """Render job attributes as interactive command-line arguments.

        Like :meth:`render_directives` but produces argv for the scheduler's
        interactive command, skipping renderers declared
        ``interactive_ok=False``.
        """
        args: list[str] = []
        for attr_name in RENDER_ORDER:
            value = getattr(job, attr_name)
            if value is None:
                continue
            renderer = self.ARG_RENDERERS.get(attr_name)
            if renderer is None or not renderer.interactive_ok:
                continue
            args.extend(renderer.to_args(value))
        return args

    def _passthrough_args(self, job: Job) -> tuple[str, ...]:
        """The job's raw scheduler arguments for this scheduler."""
        return job.scheduler_args.get(self.name, ())

    # =========================================================================
    # Abstract Methods - Subclasses must implement
    # =========================================================================

    @abstractmethod
    def submit(self, job: Job, *, keep_script: bool = False) -> JobResult:
        """Submit a job.

        Runs it attached (and returns once it has finished) when
        ``job.interactive``; returns an ``ArrayJobResult`` when ``job.array``.

        Args:
            job: Resolved job to submit.
            keep_script: If True, don't delete the job script after submission.
        """

    @abstractmethod
    def preview(self, job: Job) -> str:
        """Return the script that :meth:`submit` would write for *job*."""

    @abstractmethod
    def cancel(self, job_id: str) -> bool:
        """Cancel a job."""

    @abstractmethod
    def get_status(self, job_id: str) -> JobStatus:
        """Get job status."""

    @abstractmethod
    def get_exit_code(self, job_id: str) -> int | None:
        """Get job exit code."""

    def result(self, job_id: str) -> JobResult:
        """A handle on an existing job, without the in-memory ``Job``."""
        from hpc_runner.core.result import JobResult

        return JobResult(scheduler=self, job_id=job_id)

    # =========================================================================
    # Optional Methods - Override if scheduler supports these
    # =========================================================================

    def get_output_path(self, job_id: str, stream: str) -> Path | None:
        """Get path to output file.

        Args:
            job_id: Job ID
            stream: "stdout" or "stderr"

        Returns:
            Path to output file, or None if not determinable.
        """
        return None

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
