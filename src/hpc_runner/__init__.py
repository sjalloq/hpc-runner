"""hpc-runner: HPC job submission across multiple schedulers."""

try:
    from hpc_runner._version import __version__
except ImportError:
    __version__ = "0.0.0.dev0"

from hpc_runner.core.array import ArrayRange
from hpc_runner.core.config import HPCConfig, load_config
from hpc_runner.core.dependency import Dependency, DependencyType
from hpc_runner.core.exceptions import (
    AccountingNotAvailable,
    ConfigError,
    DependencyCycleError,
    DuplicateStepError,
    HPCToolsError,
    JobFailedError,
    JobNotFoundError,
    NotSubmittedError,
    NotSupportedError,
    PipelineError,
    SchedulerCommandError,
    SchedulerError,
    SchedulerTimeoutError,
    SchedulerUnavailableError,
    SubmissionError,
    UnknownStepError,
)
from hpc_runner.core.host import Host
from hpc_runner.core.job import Job
from hpc_runner.core.merge import Edit, Set
from hpc_runner.core.resolve import resolve
from hpc_runner.core.resources import Resource, ResourceSet
from hpc_runner.core.result import ArrayJobResult, JobResult, JobStatus
from hpc_runner.core.spec import JobSpec
from hpc_runner.schedulers import get_scheduler, list_schedulers, register_scheduler
from hpc_runner.workflow import Pipeline, PipelineJob

__all__ = [
    # Version
    "__version__",
    # Core
    "JobSpec",
    "Job",
    "Host",
    "resolve",
    "Set",
    "Edit",
    "ArrayRange",
    "Dependency",
    "DependencyType",
    "JobResult",
    "ArrayJobResult",
    "JobStatus",
    "Resource",
    "ResourceSet",
    # Config
    "load_config",
    "HPCConfig",
    # Schedulers
    "get_scheduler",
    "register_scheduler",
    "list_schedulers",
    # Workflow
    "Pipeline",
    "PipelineJob",
    # Exceptions
    "AccountingNotAvailable",
    "ConfigError",
    "DependencyCycleError",
    "DuplicateStepError",
    "HPCToolsError",
    "JobFailedError",
    "JobNotFoundError",
    "NotSubmittedError",
    "NotSupportedError",
    "PipelineError",
    "SchedulerCommandError",
    "SchedulerError",
    "SchedulerTimeoutError",
    "SchedulerUnavailableError",
    "SubmissionError",
    "UnknownStepError",
]
