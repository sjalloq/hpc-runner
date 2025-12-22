"""Core models and abstractions for hpc-tools."""

from .exceptions import (
    AccountingNotAvailable,
    ConfigError,
    ConfigNotFoundError,
    HPCToolsError,
    JobNotFoundError,
    SchedulerError,
    SubmissionError,
    ValidationError,
)
from .job_info import JobInfo
from .result import ArrayJobResult, JobResult, JobStatus

__all__ = [
    # Exceptions
    "AccountingNotAvailable",
    "ConfigError",
    "ConfigNotFoundError",
    "HPCToolsError",
    "JobNotFoundError",
    "SchedulerError",
    "SubmissionError",
    "ValidationError",
    # Types
    "JobInfo",
    "JobResult",
    "ArrayJobResult",
    "JobStatus",
]
