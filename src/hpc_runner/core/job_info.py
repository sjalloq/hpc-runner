"""Job information types for TUI display."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .result import JobStatus


@dataclass
class JobInfo:
    """Unified job information for TUI display.

    This dataclass provides a scheduler-agnostic view of job information
    suitable for display in the monitor TUI. All fields except job_id,
    name, user, and status are optional to handle varying levels of
    information availability across schedulers.
    """

    job_id: str
    name: str
    user: str
    status: JobStatus

    # Queue/partition info
    queue: str | None = None

    # Timing information
    submit_time: datetime | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    runtime: timedelta | None = None

    # Resource requests/usage
    cpu: int | None = None
    memory: str | None = None  # e.g., "16G", "4096M"
    gpu: int | None = None

    # Completion info (None for active jobs)
    exit_code: int | None = None

    # Output file paths
    stdout_path: Path | None = None
    stderr_path: Path | None = None

    # Extended info
    node: str | None = None
    dependencies: list[str] | None = None
    array_task_id: int | None = None

    @property
    def is_active(self) -> bool:
        """Check if job is still active (not yet completed)."""
        return self.status in (
            JobStatus.PENDING,
            JobStatus.RUNNING,
            JobStatus.UNKNOWN,
        )

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
    def runtime_display(self) -> str:
        """Format runtime for display (e.g., '2h 15m')."""
        if self.runtime is None:
            return "—"

        total_seconds = int(self.runtime.total_seconds())
        if total_seconds < 60:
            return f"{total_seconds}s"

        minutes = total_seconds // 60
        if minutes < 60:
            return f"{minutes}m"

        hours = minutes // 60
        remaining_minutes = minutes % 60
        if hours < 24:
            return f"{hours}h {remaining_minutes}m"

        days = hours // 24
        remaining_hours = hours % 24
        return f"{days}d {remaining_hours}h"

    @property
    def resources_display(self) -> str:
        """Format resources for display (e.g., '4/16G')."""
        parts = []
        if self.cpu is not None:
            parts.append(str(self.cpu))
        if self.memory is not None:
            parts.append(self.memory)
        if self.gpu is not None:
            parts.append(f"{self.gpu}GPU")

        return "/".join(parts) if parts else "—"
