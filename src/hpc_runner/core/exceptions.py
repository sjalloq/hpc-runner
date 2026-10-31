"""Exception hierarchy for hpc-runner.

Every error the package raises on purpose is an ``HPCToolsError``; the CLI
catches that at the top, prints one line and exits 1.  Anything else
escaping is a bug.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hpc_runner.core.result import JobStatus


def _first_line(text: str) -> str:
    """Return the first non-empty line of *text*, stripped, or ``""``."""
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


class HPCToolsError(Exception):
    """Base of every hpc-runner error; caught by the CLI top level."""


class ConfigError(HPCToolsError):
    """Bad or unknown configuration; caught by callers that can fix their config.

    ``errors`` lists the individual problems: ``[self]`` for a single one,
    or every problem found when the loader reports several together (see
    :meth:`collect`), in which case the message is one problem per line.
    """

    def __init__(self, message: str, *, path: Path | None = None, line: int | None = None):
        super().__init__(message)
        self.message = message
        self.path = path
        self.line = line
        self.errors: list[ConfigError] = [self]

    @classmethod
    def collect(cls, errors: Sequence[ConfigError]) -> ConfigError:
        """One error reporting every problem in *errors* (which must be non-empty)."""
        if len(errors) == 1:
            return errors[0]
        combined = cls("\n".join(str(e) for e in errors))
        combined.errors = list(errors)
        return combined

    def __str__(self) -> str:
        if self.path is None:
            return self.message
        if self.line is None:
            return f"{self.path}: {self.message}"
        return f"{self.path}:{self.line}: {self.message}"


class SchedulerError(HPCToolsError):
    """Base of everything a scheduler raises; caught by callers that retry or degrade."""


class SchedulerUnavailableError(SchedulerError):
    """Scheduler binary missing or its daemon unreachable; callers fall back or retry later."""


class SchedulerCommandError(SchedulerError):
    """A scheduler command exited non-zero; caught by callers that inspect argv/stderr."""

    def __init__(
        self,
        argv: Sequence[str],
        returncode: int,
        stdout: str = "",
        stderr: str = "",
    ):
        self.argv = list(argv)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        prog = self.argv[0] if self.argv else "command"
        message = _first_line(stderr) or f"{prog} exited with status {returncode}"
        super().__init__(message)


class SchedulerTimeoutError(SchedulerError):
    """A scheduler command did not finish in time; caught by callers that retry later."""

    def __init__(self, argv: Sequence[str], timeout: float | None):
        self.argv = list(argv)
        self.timeout = timeout
        prog = self.argv[0] if self.argv else "command"
        super().__init__(f"{prog} timed out after {timeout}s")


class SubmissionError(SchedulerError):
    """The scheduler rejected a submission; caught by callers that report or resubmit."""

    def __init__(
        self,
        message: str,
        *,
        script: str | None = None,
        argv: Sequence[str] | None = None,
        stderr: str | None = None,
    ):
        # Normally *message* is the scheduler's stderr verbatim; the headline
        # is its first non-empty line and the full text stays on ``stderr``.
        self.stderr = message if stderr is None else stderr
        self.script = script
        self.argv = list(argv) if argv is not None else None
        super().__init__(_first_line(message) or "submission failed")


class JobNotFoundError(SchedulerError):
    """The scheduler does not know this job id; caught by status/details callers."""


class NotSupportedError(SchedulerError):
    """This scheduler cannot honour the requested feature; caught by portable callers."""


class JobFailedError(SchedulerError):
    """Raised by ``JobResult.result()`` when a job ends not COMPLETED; caught by waiters."""

    def __init__(self, status: JobStatus, job_id: str | None, exit_code: int | None):
        self.status = status
        self.job_id = job_id
        self.exit_code = exit_code
        detail = f" (exit code {exit_code})" if exit_code is not None else ""
        what = f"job {job_id}" if job_id is not None else "interactive job"
        super().__init__(f"{what} finished {status.name}{detail}")


class AccountingNotAvailable(SchedulerError):
    """Job accounting/history is not enabled on this cluster.

    Raised when attempting to query historical job data (e.g., via qacct
    for SGE or sacct for Slurm) but the scheduler's accounting system
    is not configured or accessible.  Caught by the TUI and ``hpc status
    --history``.
    """


class PipelineError(HPCToolsError):
    """Misuse of a Pipeline; caught by code that builds pipelines."""


class DuplicateStepError(PipelineError):
    """A step name is already used in the pipeline; caught by pipeline builders."""


class UnknownStepError(PipelineError):
    """A dependency names a step not in the pipeline; caught by pipeline builders."""


class DependencyCycleError(PipelineError):
    """The pipeline's dependency graph has a cycle; caught by pipeline builders."""


class NotSubmittedError(PipelineError):
    """A pipeline operation needs a completed submission; caught by pipeline runners."""
