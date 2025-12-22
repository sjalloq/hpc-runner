"""Custom exceptions for hpc-tools."""


class HPCToolsError(Exception):
    """Base exception for hpc-tools."""


class SchedulerError(HPCToolsError):
    """Error related to scheduler operations."""


class SubmissionError(SchedulerError):
    """Error during job submission."""


class JobNotFoundError(SchedulerError):
    """Job ID not found."""


class ConfigError(HPCToolsError):
    """Error in configuration."""


class ConfigNotFoundError(ConfigError):
    """Configuration file not found."""


class ValidationError(HPCToolsError):
    """Validation error for job parameters."""


class AccountingNotAvailable(SchedulerError):
    """Job accounting/history is not enabled on this cluster.

    Raised when attempting to query historical job data (e.g., via qacct
    for SGE or sacct for Slurm) but the scheduler's accounting system
    is not configured or accessible.
    """
