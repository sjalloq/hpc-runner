# CLAUDE.md - hpc-tools Implementation Guide

## Project Overview

`hpc-tools` is a Python package for abstracting HPC job submission across multiple schedulers (Slurm, SGE, PBS, local). It provides:

- A unified Python API for job submission
- A CLI tool (`hpc`) for command-line job submission
- Configuration-driven defaults with tool/job-type matching
- Environment modules integration
- Job dependency/workflow management
- Job array support

## Key Design Principles

1. **Scheduler Agnostic**: Users write code once, run on any scheduler
2. **Sensible Defaults**: Works out of the box, customizable via config
3. **Descriptor Pattern**: Clean, declarative scheduler argument definitions
4. **Config Hierarchy**: Local > Git root > ~/.config > Package defaults
5. **Extensible**: Easy to add new scheduler support

## Package Name and CLI

- **Package**: `hpc-tools` (PyPI), import as `hpc_tools`
- **CLI Command**: `hpc`
- **Entry Point**: `hpc = hpc_tools.cli.main:app`

---

## Project Structure

```
hpc-tools/
├── src/
│   └── hpc_tools/
│       ├── __init__.py                 # Public API exports
│       ├── py.typed
│       │
│       ├── cli/
│       │   ├── __init__.py
│       │   ├── main.py                 # Typer app entry point
│       │   ├── run.py                  # `hpc run <command>`
│       │   ├── status.py               # `hpc status [job_id]`
│       │   ├── cancel.py               # `hpc cancel <job_id>`
│       │   └── config.py               # `hpc config show/init`
│       │
│       ├── core/
│       │   ├── __init__.py
│       │   ├── descriptors.py          # Base descriptor pattern
│       │   ├── job.py                  # Job model
│       │   ├── job_array.py            # Job array support
│       │   ├── result.py               # JobResult, JobStatus
│       │   ├── resources.py            # Resource abstraction
│       │   ├── config.py               # Config loading (TOML)
│       │   ├── exceptions.py
│       │   └── types.py
│       │
│       ├── schedulers/
│       │   ├── __init__.py             # get_scheduler(), registry
│       │   ├── base.py                 # BaseScheduler ABC
│       │   ├── detection.py            # Auto-detect scheduler
│       │   │
│       │   ├── slurm/
│       │   │   ├── __init__.py
│       │   │   ├── scheduler.py
│       │   │   ├── args.py             # Slurm descriptors
│       │   │   ├── parser.py           # Parse squeue/sacct
│       │   │   └── templates/
│       │   │       └── job.sh.j2
│       │   │
│       │   ├── sge/
│       │   │   ├── __init__.py
│       │   │   ├── scheduler.py
│       │   │   ├── args.py
│       │   │   ├── parser.py
│       │   │   └── templates/
│       │   │       └── job.sh.j2
│       │   │
│       │   └── local/
│       │       ├── __init__.py
│       │       └── scheduler.py
│       │
│       ├── modules/
│       │   ├── __init__.py
│       │   └── loader.py               # Environment modules
│       │
│       ├── workflow/
│       │   ├── __init__.py
│       │   ├── dependency.py           # Dependency types
│       │   └── pipeline.py             # Pipeline API
│       │
│       └── templates/
│           ├── __init__.py
│           └── engine.py               # Jinja2 handling
│
├── tests/
│   ├── conftest.py
│   ├── test_core/
│   ├── test_schedulers/
│   ├── test_cli/
│   └── test_workflow/
│
├── docs/
│   └── ...
│
├── pyproject.toml
├── README.md
├── CLAUDE.md
└── defaults/
    └── config.toml                     # Built-in defaults
```

---

## Core Abstractions

### 1. Descriptor Pattern (`core/descriptors.py`)

The descriptor pattern provides type-safe, declarative scheduler argument definitions.

```python
from abc import ABC, abstractmethod
from typing import TypeVar, Generic, Callable, Any

T = TypeVar('T')

class SchedulerArg(ABC, Generic[T]):
    """Base descriptor for scheduler arguments.
    
    Attributes:
        flag: The scheduler's command-line flag name
        converter: Function to convert Python value to string
        validator: Optional validation function
        doc: Documentation string
        env_var: Optional environment variable override
    """
    
    def __init__(
        self,
        flag: str,
        *,
        converter: Callable[[T], str] = str,
        validator: Callable[[T], bool] | None = None,
        doc: str = "",
        env_var: str | None = None,
    ):
        self.flag = flag
        self.converter = converter
        self.validator = validator
        self.doc = doc
        self.env_var = env_var
        self._name: str | None = None
    
    def __set_name__(self, owner: type, name: str) -> None:
        self._name = name
    
    def __get__(self, obj: Any, objtype: type | None = None) -> T | None:
        if obj is None:
            return self  # type: ignore
        return obj.__dict__.get(self._name)
    
    def __set__(self, obj: Any, value: T | None) -> None:
        if value is not None and self.validator:
            if not self.validator(value):
                raise ValueError(f"Invalid value for {self._name}: {value}")
        obj.__dict__[self._name] = value
    
    @abstractmethod
    def to_args(self, value: T | None) -> list[str]:
        """Convert value to command-line arguments."""
        pass
    
    @abstractmethod  
    def to_directive(self, value: T | None) -> str | None:
        """Convert value to script directive (e.g., #SBATCH, #$)."""
        pass
```

### 2. Resource Abstraction (`core/resources.py`)

Unified resource specification that maps to scheduler-specific syntax.

```python
from dataclasses import dataclass, field
from typing import Any

@dataclass
class Resource:
    """A scheduler resource request.
    
    Examples:
        Resource("gpu", 2)              # 2 GPUs
        Resource("xilinx", 1)           # 1 Xilinx license
        Resource("mem", "16G")          # Memory (alternative to Job.mem)
    """
    name: str
    value: int | str
    
    # Scheduler-specific mappings (populated by scheduler)
    _slurm_gres: str | None = field(default=None, repr=False)
    _sge_resource: str | None = field(default=None, repr=False)


@dataclass  
class ResourceSet:
    """Collection of resources for a job."""
    resources: list[Resource] = field(default_factory=list)
    
    def add(self, name: str, value: int | str) -> "ResourceSet":
        self.resources.append(Resource(name, value))
        return self
    
    def __iter__(self):
        return iter(self.resources)
```

### 3. Job Model (`core/job.py`)

The central job representation.

```python
from dataclasses import dataclass, field
from typing import Any
from pathlib import Path

from hpc_tools.core.resources import ResourceSet

@dataclass
class Job:
    """Represents a job to be submitted.
    
    Attributes:
        command: The command to execute (string or list)
        name: Job name (auto-generated if not provided)
        cpu: Number of CPUs/cores/slots
        mem: Memory requirement (e.g., "16G", "4096M")
        time: Wall time limit (e.g., "4:00:00", "1-00:00:00")
        queue: Queue/partition name
        nodes: Number of nodes (for MPI jobs)
        tasks: Number of tasks (for MPI jobs)
        resources: Additional resource requests
        modules: Environment modules to load
        modules_path: Additional module paths
        inherit_env: Inherit current environment
        workdir: Working directory (default: current)
        stdout: Stdout file path (supports templates)
        stderr: Stderr file path (supports templates)
        raw_args: Raw scheduler arguments (passthrough)
        slurm_args: Slurm-specific raw arguments
        sge_args: SGE-specific raw arguments
        pbs_args: PBS-specific raw arguments
    """
    command: str | list[str]
    name: str | None = None
    cpu: int | None = None
    mem: str | None = None
    time: str | None = None
    queue: str | None = None
    nodes: int | None = None
    tasks: int | None = None
    resources: ResourceSet = field(default_factory=ResourceSet)
    modules: list[str] = field(default_factory=list)
    modules_path: list[str] = field(default_factory=list)
    inherit_env: bool = True
    workdir: Path | str | None = None
    stdout: str | None = None
    stderr: str | None = None
    
    # Raw passthrough arguments
    raw_args: list[str] = field(default_factory=list)
    slurm_args: list[str] = field(default_factory=list)
    sge_args: list[str] = field(default_factory=list)
    pbs_args: list[str] = field(default_factory=list)
    
    # Dependency management
    dependencies: list["JobResult"] = field(default_factory=list)
    dependency_type: str = "afterok"  # afterok, afterany, after, afternotok
    
    def __post_init__(self):
        if self.name is None:
            self.name = self._generate_name()
        if isinstance(self.command, list):
            self.command = " ".join(self.command)
    
    def _generate_name(self) -> str:
        """Generate job name from command."""
        import os
        import re
        user = os.environ.get("USER", "user")
        # Extract first word of command, strip path
        cmd = self.command.split()[0] if isinstance(self.command, str) else self.command[0]
        cmd = Path(cmd).name
        cmd = re.sub(r'[^a-zA-Z0-9_-]', '_', cmd)
        return f"{user}_{cmd}"
    
    def submit(self, scheduler: "BaseScheduler | None" = None) -> "JobResult":
        """Submit the job.
        
        Args:
            scheduler: Scheduler to use. Auto-detects if None.
            
        Returns:
            JobResult with job ID and status methods
        """
        from hpc_tools.schedulers import get_scheduler
        if scheduler is None:
            scheduler = get_scheduler()
        return scheduler.submit(self)
    
    def after(self, *jobs: "JobResult", type: str = "afterok") -> "Job":
        """Add dependency on other jobs.
        
        Args:
            jobs: Jobs this job depends on
            type: Dependency type (afterok, afterany, after, afternotok)
        """
        self.dependencies.extend(jobs)
        self.dependency_type = type
        return self
    
    @classmethod
    def from_config(
        cls,
        tool_or_type: str,
        command: str | None = None,
        **overrides
    ) -> "Job":
        """Create job from configuration.
        
        Args:
            tool_or_type: Tool name or job type from config
            command: Override command (uses config template if None)
            **overrides: Override any job parameters
        """
        from hpc_tools.core.config import load_config
        config = load_config()
        job_config = config.get_job_config(tool_or_type)
        
        if command:
            job_config["command"] = command
        job_config.update(overrides)
        
        return cls(**job_config)
```

### 4. Job Arrays (`core/job_array.py`)

```python
from dataclasses import dataclass, field
from typing import Iterator

@dataclass
class JobArray:
    """Represents an array job.
    
    Attributes:
        job: Base job specification
        start: Array start index
        end: Array end index  
        step: Array step (default 1)
        max_concurrent: Max simultaneous tasks (throttling)
    """
    job: Job
    start: int = 1
    end: int = 1
    step: int = 1
    max_concurrent: int | None = None
    
    @property
    def range_str(self) -> str:
        """Format as scheduler range string."""
        s = f"{self.start}-{self.end}"
        if self.step != 1:
            s += f":{self.step}"
        if self.max_concurrent:
            s += f"%{self.max_concurrent}"
        return s
    
    @property
    def indices(self) -> Iterator[int]:
        """Iterate over array indices."""
        return iter(range(self.start, self.end + 1, self.step))
    
    def submit(self, scheduler: "BaseScheduler | None" = None) -> "ArrayJobResult":
        """Submit the array job."""
        from hpc_tools.schedulers import get_scheduler
        if scheduler is None:
            scheduler = get_scheduler()
        return scheduler.submit_array(self)
```

### 5. Job Result (`core/result.py`)

```python
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING
from pathlib import Path

if TYPE_CHECKING:
    from hpc_tools.schedulers.base import BaseScheduler

class JobStatus(Enum):
    """Unified job status across schedulers."""
    PENDING = auto()      # Waiting in queue
    RUNNING = auto()      # Currently executing
    COMPLETED = auto()    # Finished successfully
    FAILED = auto()       # Finished with error
    CANCELLED = auto()    # User cancelled
    TIMEOUT = auto()      # Hit time limit
    UNKNOWN = auto()      # Cannot determine


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
    
    @property
    def status(self) -> JobStatus:
        """Get current job status (queries scheduler)."""
        return self.scheduler.get_status(self.job_id)
    
    @property
    def is_complete(self) -> bool:
        """Check if job has finished (success or failure)."""
        return self.status in (
            JobStatus.COMPLETED, 
            JobStatus.FAILED, 
            JobStatus.CANCELLED,
            JobStatus.TIMEOUT
        )
    
    @property
    def returncode(self) -> int | None:
        """Get exit code (None if not complete)."""
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
            if timeout and (time.time() - start) > timeout:
                raise TimeoutError(f"Job {self.job_id} did not complete within {timeout}s")
            time.sleep(poll_interval)
        return self.status
    
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
        if tail:
            lines = content.splitlines()
            content = "\n".join(lines[-tail:])
        return content


@dataclass
class ArrayJobResult:
    """Result of a submitted array job."""
    base_job_id: str
    scheduler: "BaseScheduler"
    array: "JobArray"
    
    def task_id(self, index: int) -> str:
        """Get job ID for specific array task."""
        return f"{self.base_job_id}_{index}"
    
    def task_status(self, index: int) -> JobStatus:
        """Get status of specific array task."""
        return self.scheduler.get_status(self.task_id(index))
    
    def wait(self, poll_interval: float = 5.0) -> dict[int, JobStatus]:
        """Wait for all array tasks to complete."""
        import time
        results = {}
        pending = set(self.array.indices)
        
        while pending:
            for idx in list(pending):
                status = self.task_status(idx)
                if status in (JobStatus.COMPLETED, JobStatus.FAILED, 
                             JobStatus.CANCELLED, JobStatus.TIMEOUT):
                    results[idx] = status
                    pending.remove(idx)
            if pending:
                time.sleep(poll_interval)
        
        return results
```

---

## Scheduler Implementation

### Base Scheduler (`schedulers/base.py`)

```python
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING
from pathlib import Path

if TYPE_CHECKING:
    from hpc_tools.core.job import Job
    from hpc_tools.core.job_array import JobArray
    from hpc_tools.core.result import JobResult, ArrayJobResult, JobStatus

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
    """
    
    name: str  # e.g., "slurm", "sge", "local"
    
    @abstractmethod
    def submit(self, job: "Job", interactive: bool = False) -> "JobResult":
        """Submit a job to the scheduler.
        
        Args:
            job: Job specification
            interactive: Run interactively (blocking)
            
        Returns:
            JobResult with job ID and methods
        """
        pass
    
    @abstractmethod
    def submit_array(self, array: "JobArray") -> "ArrayJobResult":
        """Submit an array job."""
        pass
    
    @abstractmethod
    def cancel(self, job_id: str) -> bool:
        """Cancel a job by ID."""
        pass
    
    @abstractmethod
    def get_status(self, job_id: str) -> "JobStatus":
        """Get current status of a job."""
        pass
    
    @abstractmethod
    def get_exit_code(self, job_id: str) -> int | None:
        """Get exit code of completed job."""
        pass
    
    @abstractmethod
    def get_output_path(self, job_id: str, stream: str) -> Path | None:
        """Get path to output file.
        
        Args:
            job_id: Job ID
            stream: "stdout" or "stderr"
        """
        pass
    
    @abstractmethod
    def generate_script(self, job: "Job") -> str:
        """Generate job script content."""
        pass
    
    @abstractmethod
    def build_submit_command(self, job: "Job") -> list[str]:
        """Build the submission command (e.g., sbatch args)."""
        pass
    
    def get_scheduler_args(self, job: "Job") -> list[str]:
        """Get scheduler-specific raw args from job."""
        return getattr(job, f"{self.name}_args", [])
```

### Slurm Implementation (`schedulers/slurm/scheduler.py`)

```python
import subprocess
import re
from pathlib import Path

from hpc_tools.schedulers.base import BaseScheduler
from hpc_tools.core.job import Job
from hpc_tools.core.result import JobResult, JobStatus, ArrayJobResult
from hpc_tools.core.job_array import JobArray
from hpc_tools.templates import render_template

from .args import (
    SlurmCpuArg, SlurmMemArg, SlurmTimeArg, SlurmQueueArg,
    SlurmNodesArg, SlurmTasksArg, SlurmJobNameArg,
    SlurmOutputArg, SlurmErrorArg, SlurmArrayArg
)


class SlurmScheduler(BaseScheduler):
    """Slurm scheduler implementation."""
    
    name = "slurm"
    
    # Descriptor-based argument definitions
    cpu = SlurmCpuArg("cpus-per-task")
    mem = SlurmMemArg("mem")
    time = SlurmTimeArg("time")
    queue = SlurmQueueArg("partition")
    nodes = SlurmNodesArg("nodes")
    tasks = SlurmTasksArg("ntasks")
    job_name = SlurmJobNameArg("job-name")
    stdout = SlurmOutputArg("output")
    stderr = SlurmErrorArg("error")
    array = SlurmArrayArg("array")
    
    # Map unified status to Slurm codes
    STATUS_MAP = {
        "PENDING": JobStatus.PENDING,
        "RUNNING": JobStatus.RUNNING,
        "COMPLETED": JobStatus.COMPLETED,
        "FAILED": JobStatus.FAILED,
        "CANCELLED": JobStatus.CANCELLED,
        "TIMEOUT": JobStatus.TIMEOUT,
        "NODE_FAIL": JobStatus.FAILED,
        "PREEMPTED": JobStatus.CANCELLED,
    }
    
    def submit(self, job: Job, interactive: bool = False) -> JobResult:
        if interactive:
            return self._submit_interactive(job)
        return self._submit_batch(job)
    
    def _submit_batch(self, job: Job) -> JobResult:
        """Submit via sbatch."""
        import tempfile
        
        script = self.generate_script(job)
        
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.sh', delete=False
        ) as f:
            f.write(script)
            script_path = f.name
        
        try:
            cmd = ["sbatch", "--parsable", script_path]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            job_id = result.stdout.strip().split(";")[0]  # Handle array job output
            return JobResult(job_id=job_id, scheduler=self, job=job)
        finally:
            Path(script_path).unlink(missing_ok=True)
    
    def _submit_interactive(self, job: Job) -> JobResult:
        """Submit via srun for interactive execution."""
        cmd = self.build_interactive_command(job)
        result = subprocess.run(cmd, check=False)
        # For interactive jobs, we don't have a job ID
        return JobResult(job_id="interactive", scheduler=self, job=job)
    
    def submit_array(self, array: JobArray) -> ArrayJobResult:
        """Submit array job."""
        job = array.job
        # Set array attribute for script generation
        self._array_spec = array.range_str
        result = self._submit_batch(job)
        self._array_spec = None
        return ArrayJobResult(base_job_id=result.job_id, scheduler=self, array=array)
    
    def cancel(self, job_id: str) -> bool:
        try:
            subprocess.run(["scancel", job_id], check=True)
            return True
        except subprocess.CalledProcessError:
            return False
    
    def get_status(self, job_id: str) -> JobStatus:
        """Query job status via squeue/sacct."""
        # Try squeue first (running/pending jobs)
        try:
            result = subprocess.run(
                ["squeue", "-j", job_id, "-h", "-o", "%T"],
                capture_output=True, text=True, check=True
            )
            state = result.stdout.strip()
            if state:
                return self.STATUS_MAP.get(state, JobStatus.UNKNOWN)
        except subprocess.CalledProcessError:
            pass
        
        # Fall back to sacct (completed jobs)
        try:
            result = subprocess.run(
                ["sacct", "-j", job_id, "-n", "-o", "State", "-X"],
                capture_output=True, text=True, check=True
            )
            state = result.stdout.strip().split()[0] if result.stdout.strip() else ""
            return self.STATUS_MAP.get(state, JobStatus.UNKNOWN)
        except subprocess.CalledProcessError:
            return JobStatus.UNKNOWN
    
    def get_exit_code(self, job_id: str) -> int | None:
        try:
            result = subprocess.run(
                ["sacct", "-j", job_id, "-n", "-o", "ExitCode", "-X"],
                capture_output=True, text=True, check=True
            )
            # Format is "exit:signal"
            code_str = result.stdout.strip().split(":")[0]
            return int(code_str) if code_str else None
        except (subprocess.CalledProcessError, ValueError):
            return None
    
    def get_output_path(self, job_id: str, stream: str) -> Path | None:
        """Determine output path from job info."""
        try:
            flag = "StdOut" if stream == "stdout" else "StdErr"
            result = subprocess.run(
                ["scontrol", "show", "job", job_id],
                capture_output=True, text=True, check=True
            )
            for line in result.stdout.split():
                if line.startswith(f"{flag}="):
                    return Path(line.split("=", 1)[1])
        except subprocess.CalledProcessError:
            pass
        return None
    
    def generate_script(self, job: Job) -> str:
        """Generate sbatch script using template."""
        return render_template(
            "slurm/job.sh.j2",
            job=job,
            scheduler=self,
            directives=self._build_directives(job),
        )
    
    def _build_directives(self, job: Job) -> list[str]:
        """Build #SBATCH directives."""
        directives = []
        
        # Use descriptors to generate directives
        for name in ['cpu', 'mem', 'time', 'queue', 'nodes', 'tasks', 
                     'job_name', 'stdout', 'stderr']:
            descriptor = getattr(type(self), name)
            value = getattr(job, name, None)
            if directive := descriptor.to_directive(value):
                directives.append(directive)
        
        # Handle array jobs
        if hasattr(self, '_array_spec') and self._array_spec:
            directives.append(f"#SBATCH --array={self._array_spec}")
        
        # Handle resources (GRES)
        for resource in job.resources:
            directives.append(f"#SBATCH --gres={resource.name}:{resource.value}")
        
        # Handle dependencies
        if job.dependencies:
            dep_ids = ":".join(dep.job_id for dep in job.dependencies)
            directives.append(f"#SBATCH --dependency={job.dependency_type}:{dep_ids}")
        
        # Raw args
        for arg in job.raw_args + job.slurm_args:
            directives.append(f"#SBATCH {arg}")
        
        return directives
    
    def build_submit_command(self, job: Job) -> list[str]:
        """Build sbatch command line."""
        cmd = ["sbatch"]
        
        for name in ['cpu', 'mem', 'time', 'queue', 'nodes', 'tasks', 'job_name']:
            descriptor = getattr(type(self), name)
            value = getattr(job, name, None)
            cmd.extend(descriptor.to_args(value))
        
        cmd.extend(job.raw_args)
        cmd.extend(job.slurm_args)
        
        return cmd
    
    def build_interactive_command(self, job: Job) -> list[str]:
        """Build srun command for interactive jobs."""
        cmd = ["srun", "--pty"]
        
        for name in ['cpu', 'mem', 'time', 'queue', 'nodes', 'tasks']:
            descriptor = getattr(type(self), name)
            value = getattr(job, name, None)
            cmd.extend(descriptor.to_args(value))
        
        cmd.extend(job.raw_args)
        cmd.extend(job.slurm_args)
        
        # Add the command
        cmd.extend(["bash", "-c", job.command])
        
        return cmd
```

### Slurm Descriptors (`schedulers/slurm/args.py`)

```python
from hpc_tools.core.descriptors import SchedulerArg

class SlurmArg(SchedulerArg):
    """Base Slurm argument."""
    
    def to_args(self, value) -> list[str]:
        if value is None:
            return []
        return [f"--{self.flag}={self.converter(value)}"]
    
    def to_directive(self, value) -> str | None:
        if value is None:
            return None
        return f"#SBATCH --{self.flag}={self.converter(value)}"


class SlurmCpuArg(SlurmArg):
    """CPU/cores argument."""
    def __init__(self, flag: str = "cpus-per-task"):
        super().__init__(flag, converter=str, doc="Number of CPUs per task")


class SlurmMemArg(SlurmArg):
    """Memory argument with unit handling."""
    def __init__(self, flag: str = "mem"):
        super().__init__(flag, converter=self._convert_mem, doc="Memory requirement")
    
    @staticmethod
    def _convert_mem(value: str | int) -> str:
        if isinstance(value, int):
            return f"{value}G"
        return value


class SlurmTimeArg(SlurmArg):
    """Time limit argument."""
    def __init__(self, flag: str = "time"):
        super().__init__(flag, doc="Wall time limit (HH:MM:SS or D-HH:MM:SS)")


class SlurmQueueArg(SlurmArg):
    """Partition/queue argument."""
    def __init__(self, flag: str = "partition"):
        super().__init__(flag, doc="Partition name")


class SlurmNodesArg(SlurmArg):
    """Number of nodes."""
    def __init__(self, flag: str = "nodes"):
        super().__init__(flag, converter=str, doc="Number of nodes")


class SlurmTasksArg(SlurmArg):
    """Number of tasks (MPI)."""
    def __init__(self, flag: str = "ntasks"):
        super().__init__(flag, converter=str, doc="Number of tasks")


class SlurmJobNameArg(SlurmArg):
    """Job name."""
    def __init__(self, flag: str = "job-name"):
        super().__init__(flag, doc="Job name")


class SlurmOutputArg(SlurmArg):
    """Stdout path."""
    def __init__(self, flag: str = "output"):
        super().__init__(flag, doc="Stdout file path")
    
    def to_directive(self, value) -> str | None:
        if value is None:
            # Default pattern
            return "#SBATCH --output=%x.%j.out"
        return super().to_directive(value)


class SlurmErrorArg(SlurmArg):
    """Stderr path."""
    def __init__(self, flag: str = "error"):
        super().__init__(flag, doc="Stderr file path")
    
    def to_directive(self, value) -> str | None:
        if value is None:
            return "#SBATCH --error=%x.%j.err"
        return super().to_directive(value)


class SlurmArrayArg(SlurmArg):
    """Array job specification."""
    def __init__(self, flag: str = "array"):
        super().__init__(flag, doc="Array job range (e.g., 1-100, 1-100:10, 1-100%10)")
```

### SGE Implementation (`schedulers/sge/scheduler.py`)

Similar structure to Slurm, but with SGE-specific:
- Uses `qsub`, `qrsh`, `qdel`, `qstat`
- Different directive format (`#$ -flag value`)
- Parallel environments for CPU allocation (`-pe smp N`)
- Different resource specification (`-l resource=value`)

### Local Scheduler (`schedulers/local/scheduler.py`)

```python
import subprocess
import os
from pathlib import Path
from datetime import datetime

from hpc_tools.schedulers.base import BaseScheduler
from hpc_tools.core.job import Job
from hpc_tools.core.result import JobResult, JobStatus

class LocalScheduler(BaseScheduler):
    """Execute jobs locally (for development/testing)."""
    
    name = "local"
    
    _job_counter = 0
    _processes: dict[str, subprocess.Popen] = {}
    
    def submit(self, job: Job, interactive: bool = False) -> JobResult:
        """Run job as local subprocess."""
        self._job_counter += 1
        job_id = f"local_{self._job_counter}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        # Set up environment with modules
        env = os.environ.copy() if job.inherit_env else {}
        
        # Generate and run script
        script = self.generate_script(job)
        script_path = Path(f".hpc_local_{job_id}.sh")
        script_path.write_text(script)
        script_path.chmod(0o755)
        
        workdir = Path(job.workdir) if job.workdir else Path.cwd()
        stdout_path = workdir / f"{job.name}.{job_id}.out"
        stderr_path = workdir / f"{job.name}.{job_id}.err"
        
        with open(stdout_path, 'w') as stdout_f, open(stderr_path, 'w') as stderr_f:
            if interactive:
                # Blocking execution
                result = subprocess.run(
                    [str(script_path)],
                    cwd=workdir,
                    env=env,
                    stdout=stdout_f,
                    stderr=stderr_f,
                )
                script_path.unlink()
            else:
                # Background execution
                proc = subprocess.Popen(
                    [str(script_path)],
                    cwd=workdir,
                    env=env,
                    stdout=stdout_f,
                    stderr=stderr_f,
                )
                self._processes[job_id] = proc
        
        return JobResult(job_id=job_id, scheduler=self, job=job)
    
    def submit_array(self, array):
        """Simulate array job by submitting multiple jobs."""
        results = []
        for idx in array.indices:
            job = array.job
            # Set array index environment variable
            env_backup = os.environ.get("HPC_ARRAY_TASK_ID")
            os.environ["HPC_ARRAY_TASK_ID"] = str(idx)
            result = self.submit(job)
            results.append(result)
            if env_backup:
                os.environ["HPC_ARRAY_TASK_ID"] = env_backup
        # Return first result with modified job_id format
        return results[0]  # Simplified
    
    def cancel(self, job_id: str) -> bool:
        if job_id in self._processes:
            self._processes[job_id].terminate()
            return True
        return False
    
    def get_status(self, job_id: str) -> JobStatus:
        if job_id not in self._processes:
            return JobStatus.UNKNOWN
        proc = self._processes[job_id]
        if proc.poll() is None:
            return JobStatus.RUNNING
        return JobStatus.COMPLETED if proc.returncode == 0 else JobStatus.FAILED
    
    def get_exit_code(self, job_id: str) -> int | None:
        if job_id in self._processes:
            return self._processes[job_id].returncode
        return None
    
    def get_output_path(self, job_id: str, stream: str) -> Path | None:
        # Would need to track this from submit()
        return None
    
    def generate_script(self, job: Job) -> str:
        """Generate local execution script."""
        from hpc_tools.templates import render_template
        return render_template("local/job.sh.j2", job=job, scheduler=self)
    
    def build_submit_command(self, job: Job) -> list[str]:
        return ["bash", "-c", job.command]
```

### Scheduler Detection (`schedulers/detection.py`)

```python
import shutil
import os

def detect_scheduler() -> str:
    """Auto-detect available scheduler.
    
    Order of precedence:
    1. HPC_SCHEDULER environment variable
    2. Slurm (check for sbatch)
    3. SGE (check for qsub with SGE_ROOT)
    4. PBS (check for qsub with PBS_CONF_FILE)
    5. Local fallback
    """
    # Environment override
    if scheduler := os.environ.get("HPC_SCHEDULER"):
        return scheduler.lower()
    
    # Check for Slurm
    if shutil.which("sbatch") and shutil.which("squeue"):
        return "slurm"
    
    # Check for SGE (also uses qsub but has SGE_ROOT)
    if shutil.which("qsub") and os.environ.get("SGE_ROOT"):
        return "sge"
    
    # Check for PBS/Torque
    if shutil.which("qsub") and os.environ.get("PBS_CONF_FILE"):
        return "pbs"
    
    # Fallback to local
    return "local"
```

### Scheduler Registry (`schedulers/__init__.py`)

```python
from typing import TYPE_CHECKING

from .detection import detect_scheduler

if TYPE_CHECKING:
    from .base import BaseScheduler

_SCHEDULERS = {
    "slurm": "hpc_tools.schedulers.slurm:SlurmScheduler",
    "sge": "hpc_tools.schedulers.sge:SGEScheduler", 
    "pbs": "hpc_tools.schedulers.pbs:PBSScheduler",
    "local": "hpc_tools.schedulers.local:LocalScheduler",
}

def get_scheduler(name: str | None = None) -> "BaseScheduler":
    """Get scheduler instance.
    
    Args:
        name: Scheduler name or None to auto-detect
        
    Returns:
        Scheduler instance
    """
    if name is None:
        name = detect_scheduler()
    
    if name not in _SCHEDULERS:
        raise ValueError(f"Unknown scheduler: {name}. Available: {list(_SCHEDULERS.keys())}")
    
    # Lazy import
    module_path, class_name = _SCHEDULERS[name].rsplit(":", 1)
    import importlib
    module = importlib.import_module(module_path)
    scheduler_class = getattr(module, class_name)
    
    return scheduler_class()


def register_scheduler(name: str, import_path: str) -> None:
    """Register a custom scheduler.
    
    Args:
        name: Scheduler name
        import_path: Import path like "mypackage.scheduler:MyScheduler"
    """
    _SCHEDULERS[name] = import_path
```

---

## Configuration System

### Config Loader (`core/config.py`)

```python
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any
import tomllib

@dataclass
class HPCConfig:
    """Loaded configuration."""
    defaults: dict[str, Any] = field(default_factory=dict)
    tools: dict[str, dict[str, Any]] = field(default_factory=dict)
    types: dict[str, dict[str, Any]] = field(default_factory=dict)
    schedulers: dict[str, dict[str, Any]] = field(default_factory=dict)
    
    _source_path: Path | None = None
    
    def get_job_config(self, tool_or_type: str) -> dict[str, Any]:
        """Get merged configuration for a tool or type.
        
        Lookup order:
        1. Check types[tool_or_type]
        2. Check tools[tool_or_type]
        3. Fall back to defaults
        """
        config = self.defaults.copy()
        
        if tool_or_type in self.types:
            config = self._merge(config, self.types[tool_or_type])
        elif tool_or_type in self.tools:
            config = self._merge(config, self.tools[tool_or_type])
        
        return config
    
    def get_tool_config(self, command: str) -> dict[str, Any]:
        """Get configuration matching a command.
        
        Extracts tool name from command and looks up config.
        """
        # Extract tool name (first word, strip path)
        tool = command.split()[0]
        tool = Path(tool).name
        
        return self.get_job_config(tool)
    
    @staticmethod
    def _merge(base: dict, override: dict) -> dict:
        """Deep merge with override taking precedence."""
        result = base.copy()
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = HPCConfig._merge(result[key], value)
            elif key in result and isinstance(result[key], list) and isinstance(value, list):
                # Check for list reset marker
                if value and value[0] == "-":
                    result[key] = value[1:]
                else:
                    result[key] = list(set(result[key] + value))
            else:
                result[key] = value
        return result


def find_config_file() -> Path | None:
    """Find configuration file in priority order.
    
    Search order:
    1. ./hpc-tools.toml (current directory)
    2. ./pyproject.toml [tool.hpc-tools] section
    3. Git repository root hpc-tools.toml
    4. ~/.config/hpc-tools/config.toml
    5. Package defaults
    """
    # Current directory
    cwd = Path.cwd()
    if (cwd / "hpc-tools.toml").exists():
        return cwd / "hpc-tools.toml"
    
    if (cwd / "pyproject.toml").exists():
        try:
            with open(cwd / "pyproject.toml", "rb") as f:
                pyproject = tomllib.load(f)
            if "tool" in pyproject and "hpc-tools" in pyproject["tool"]:
                return cwd / "pyproject.toml"
        except Exception:
            pass
    
    # Git root
    git_root = _find_git_root(cwd)
    if git_root and (git_root / "hpc-tools.toml").exists():
        return git_root / "hpc-tools.toml"
    
    # User config
    user_config = Path.home() / ".config" / "hpc-tools" / "config.toml"
    if user_config.exists():
        return user_config
    
    # Package defaults
    package_defaults = Path(__file__).parent.parent / "defaults" / "config.toml"
    if package_defaults.exists():
        return package_defaults
    
    return None


def _find_git_root(start: Path) -> Path | None:
    """Find git repository root."""
    current = start.resolve()
    while current != current.parent:
        if (current / ".git").exists():
            return current
        current = current.parent
    return None


def load_config(path: Path | str | None = None) -> HPCConfig:
    """Load configuration from file.
    
    Args:
        path: Explicit config path or None to auto-discover
    """
    if path is None:
        path = find_config_file()
    
    if path is None:
        return HPCConfig()  # Empty config, use defaults
    
    path = Path(path)
    
    with open(path, "rb") as f:
        data = tomllib.load(f)
    
    # Handle pyproject.toml
    if path.name == "pyproject.toml":
        data = data.get("tool", {}).get("hpc-tools", {})
    
    config = HPCConfig(
        defaults=data.get("defaults", {}),
        tools=data.get("tools", {}),
        types=data.get("types", {}),
        schedulers=data.get("schedulers", {}),
    )
    config._source_path = path
    
    return config
```

### Default Configuration (`defaults/config.toml`)

```toml
# hpc-tools default configuration

[defaults]
scheduler = "auto"
queue = "batch"
cpu = 1
mem = "4G"
time = "1:00:00"
inherit_env = true
modules_path = []
modules = []

# Default output patterns (scheduler-specific patterns applied)
stdout = "{name}.{job_id}.out"
stderr = "{name}.{job_id}.err"

# Tool configurations
[tools.xterm]
queue = "interactive"
time = "4:00:00"
interactive = true

[tools.make]
# Inherit defaults

[tools.python]
modules = ["python/3.11"]

# Named job types
[types.interactive]
queue = "interactive"
time = "8:00:00"
inherit_env = true

[types.gpu]
queue = "gpu"
resources = [{name = "gpu", value = 1}]

[types.mpi]
nodes = 2
tasks = 32
```

---

## Templates

### Template Engine (`templates/engine.py`)

```python
from pathlib import Path
import jinja2

_TEMPLATE_DIR = Path(__file__).parent.parent / "schedulers"

_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader([_TEMPLATE_DIR, Path(__file__).parent]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_template(name: str, **context) -> str:
    """Render a template.
    
    Args:
        name: Template name (e.g., "slurm/job.sh.j2")
        **context: Template context variables
    """
    template = _env.get_template(name)
    return template.render(**context)
```

### Slurm Template (`schedulers/slurm/templates/job.sh.j2`)

```jinja2
#!/bin/bash
# Generated by hpc-tools

{% for directive in directives %}
{{ directive }}
{% endfor %}

# Exit on error
set -e

# Source module system
if [ -f /etc/profile.d/modules.sh ]; then
    . /etc/profile.d/modules.sh
elif [ -f /usr/share/Modules/init/bash ]; then
    . /usr/share/Modules/init/bash
fi

{% if job.modules_path %}
# Additional module paths
{% for path in job.modules_path %}
module use {{ path }}
{% endfor %}
{% endif %}

{% if job.modules %}
# Load modules
module purge
{% for mod in job.modules %}
module load {{ mod }}
{% endfor %}
{% endif %}

{% if job.workdir %}
# Change to working directory
cd {{ job.workdir }}
{% endif %}

# Execute command
{{ job.command }}
exit $?
```

---

## CLI Implementation

### Main Entry Point (`cli/main.py`)

```python
import typer
from typing import Optional
from pathlib import Path

app = typer.Typer(
    name="hpc",
    help="HPC job submission and management tool",
    no_args_is_help=True,
)


@app.callback()
def main(
    ctx: typer.Context,
    config: Optional[Path] = typer.Option(
        None, "--config", "-c",
        help="Path to configuration file"
    ),
    scheduler: Optional[str] = typer.Option(
        None, "--scheduler", "-s",
        help="Force scheduler (slurm, sge, pbs, local)"
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v",
        help="Enable verbose output"
    ),
):
    """HPC job submission tool."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config
    ctx.obj["scheduler"] = scheduler
    ctx.obj["verbose"] = verbose


# Import and register subcommands
from hpc_tools.cli import run, status, cancel, config as config_cmd

app.add_typer(run.app, name="run")
app.command("status")(status.status)
app.command("cancel")(cancel.cancel)
app.add_typer(config_cmd.app, name="config")


def cli():
    """Entry point for console script."""
    app()


if __name__ == "__main__":
    cli()
```

### Run Command (`cli/run.py`)

```python
import typer
from typing import Optional, List
from pathlib import Path

app = typer.Typer(help="Submit a job")


@app.callback(invoke_without_command=True)
def run(
    ctx: typer.Context,
    command: List[str] = typer.Argument(..., help="Command to execute"),
    
    # Job parameters
    name: Optional[str] = typer.Option(None, "--name", "-N", help="Job name"),
    cpu: Optional[int] = typer.Option(None, "--cpu", "-c", help="Number of CPUs"),
    mem: Optional[str] = typer.Option(None, "--mem", "-m", help="Memory (e.g., 16G)"),
    time: Optional[str] = typer.Option(None, "--time", "-t", help="Time limit"),
    queue: Optional[str] = typer.Option(None, "--queue", "-q", help="Queue/partition"),
    
    # Execution mode
    interactive: bool = typer.Option(False, "--interactive", "-I", help="Run interactively"),
    local: bool = typer.Option(False, "--local", "-L", help="Run locally"),
    
    # Configuration
    job_type: Optional[str] = typer.Option(None, "--type", "-T", help="Job type from config"),
    
    # Modules
    module: Optional[List[str]] = typer.Option(None, "--module", "-M", help="Modules to load"),
    
    # Raw passthrough
    raw: Optional[List[str]] = typer.Option(None, "--raw", "-R", help="Raw scheduler args"),
    
    # Output
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show what would be submitted"),
):
    """Submit a job to the scheduler."""
    from hpc_tools import Job, get_scheduler
    from hpc_tools.core.config import load_config
    
    # Get scheduler
    scheduler_name = "local" if local else ctx.obj.get("scheduler")
    scheduler = get_scheduler(scheduler_name)
    
    # Load config
    config = load_config(ctx.obj.get("config_path"))
    
    # Build command string
    cmd_str = " ".join(command)
    
    # Create job from config or parameters
    if job_type:
        job = Job.from_config(job_type, command=cmd_str)
    else:
        # Try to match tool
        job_config = config.get_tool_config(cmd_str)
        job = Job(command=cmd_str, **job_config)
    
    # Override with CLI arguments
    if name:
        job.name = name
    if cpu:
        job.cpu = cpu
    if mem:
        job.mem = mem
    if time:
        job.time = time
    if queue:
        job.queue = queue
    if module:
        job.modules = list(module)
    if raw:
        job.raw_args = list(raw)
    
    if dry_run:
        _show_dry_run(job, scheduler)
        return
    
    # Submit
    result = scheduler.submit(job, interactive=interactive)
    
    if interactive:
        typer.echo(f"Job completed with exit code: {result.returncode}")
    else:
        typer.echo(f"Submitted job {result.job_id}")


def _show_dry_run(job, scheduler):
    """Display what would be submitted."""
    typer.echo("=== Dry Run ===")
    typer.echo(f"Scheduler: {scheduler.name}")
    typer.echo(f"Job Name: {job.name}")
    typer.echo(f"Command: {job.command}")
    typer.echo(f"CPU: {job.cpu}")
    typer.echo(f"Memory: {job.mem}")
    typer.echo(f"Time: {job.time}")
    typer.echo(f"Queue: {job.queue}")
    typer.echo(f"Modules: {job.modules}")
    typer.echo("\n=== Generated Script ===")
    typer.echo(scheduler.generate_script(job))
```

### Status Command (`cli/status.py`)

```python
import typer
from typing import Optional

def status(
    ctx: typer.Context,
    job_id: Optional[str] = typer.Argument(None, help="Job ID to check"),
    all_users: bool = typer.Option(False, "--all", "-a", help="Show all users"),
    watch: bool = typer.Option(False, "--watch", "-w", help="Watch mode"),
):
    """Check job status."""
    from hpc_tools import get_scheduler
    
    scheduler = get_scheduler(ctx.obj.get("scheduler"))
    
    if job_id:
        status = scheduler.get_status(job_id)
        typer.echo(f"Job {job_id}: {status.name}")
    else:
        # List all jobs (would need scheduler.list_jobs() method)
        typer.echo("Listing jobs not yet implemented")
```

---

## Workflow Support

### Pipeline API (`workflow/pipeline.py`)

```python
from dataclasses import dataclass, field
from typing import Any
from hpc_tools.core.job import Job
from hpc_tools.core.result import JobResult

@dataclass
class PipelineJob:
    """A job within a pipeline."""
    job: Job
    name: str
    depends_on: list["PipelineJob"] = field(default_factory=list)
    result: JobResult | None = None


class Pipeline:
    """Workflow pipeline with job dependencies."""
    
    def __init__(self, name: str = "pipeline"):
        self.name = name
        self.jobs: list[PipelineJob] = []
        self._name_map: dict[str, PipelineJob] = {}
    
    def add(
        self,
        command: str,
        name: str | None = None,
        depends_on: list[str | PipelineJob] | None = None,
        **job_kwargs
    ) -> PipelineJob:
        """Add a job to the pipeline.
        
        Args:
            command: Command to execute
            name: Job name (auto-generated if None)
            depends_on: List of job names or PipelineJob objects
            **job_kwargs: Additional Job parameters
        """
        if name is None:
            name = f"step_{len(self.jobs) + 1}"
        
        job = Job(command=command, name=name, **job_kwargs)
        
        dependencies = []
        if depends_on:
            for dep in depends_on:
                if isinstance(dep, str):
                    if dep not in self._name_map:
                        raise ValueError(f"Unknown dependency: {dep}")
                    dependencies.append(self._name_map[dep])
                else:
                    dependencies.append(dep)
        
        pipeline_job = PipelineJob(job=job, name=name, depends_on=dependencies)
        self.jobs.append(pipeline_job)
        self._name_map[name] = pipeline_job
        
        return pipeline_job
    
    def submit(self, scheduler=None) -> dict[str, JobResult]:
        """Submit all jobs respecting dependencies.
        
        Returns:
            Dict mapping job names to results
        """
        from hpc_tools import get_scheduler
        
        if scheduler is None:
            scheduler = get_scheduler()
        
        results = {}
        
        for pjob in self._topological_sort():
            # Set up dependencies
            if pjob.depends_on:
                dep_results = [results[d.name] for d in pjob.depends_on]
                pjob.job.dependencies = dep_results
            
            # Submit
            result = scheduler.submit(pjob.job)
            pjob.result = result
            results[pjob.name] = result
        
        return results
    
    def _topological_sort(self) -> list[PipelineJob]:
        """Sort jobs by dependency order."""
        visited = set()
        result = []
        
        def visit(job: PipelineJob):
            if job.name in visited:
                return
            visited.add(job.name)
            for dep in job.depends_on:
                visit(dep)
            result.append(job)
        
        for job in self.jobs:
            visit(job)
        
        return result
    
    def wait(self, poll_interval: float = 5.0) -> dict[str, JobResult]:
        """Wait for all jobs to complete."""
        for pjob in self.jobs:
            if pjob.result:
                pjob.result.wait(poll_interval=poll_interval)
        return {pj.name: pj.result for pj in self.jobs if pj.result}
    
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        pass
```

---

## Public API (`__init__.py`)

```python
"""hpc-tools: HPC job submission across multiple schedulers."""

from hpc_tools.core.job import Job
from hpc_tools.core.job_array import JobArray
from hpc_tools.core.result import JobResult, ArrayJobResult, JobStatus
from hpc_tools.core.resources import Resource, ResourceSet
from hpc_tools.core.config import load_config, HPCConfig
from hpc_tools.schedulers import get_scheduler, register_scheduler
from hpc_tools.workflow.pipeline import Pipeline

__version__ = "0.1.0"

__all__ = [
    # Core
    "Job",
    "JobArray",
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
    
    # Workflow
    "Pipeline",
]
```

---

## Testing Strategy

### Test Structure

```
tests/
├── conftest.py              # Fixtures, mock scheduler
├── test_core/
│   ├── test_job.py
│   ├── test_descriptors.py
│   ├── test_config.py
│   └── test_resources.py
├── test_schedulers/
│   ├── test_detection.py
│   ├── test_slurm.py       # Mock sbatch/squeue
│   ├── test_sge.py         # Mock qsub/qstat
│   └── test_local.py
├── test_cli/
│   └── test_run.py         # Typer testing
├── test_workflow/
│   └── test_pipeline.py
└── integration/             # Optional, requires real scheduler
    └── test_real_slurm.py
```

### Key Test Fixtures (`conftest.py`)

```python
import pytest
from unittest.mock import MagicMock, patch

@pytest.fixture
def mock_slurm():
    """Mock Slurm commands."""
    with patch("subprocess.run") as mock_run:
        def side_effect(cmd, *args, **kwargs):
            result = MagicMock()
            if cmd[0] == "sbatch":
                result.stdout = "12345\n"
                result.returncode = 0
            elif cmd[0] == "squeue":
                result.stdout = "RUNNING\n"
                result.returncode = 0
            return result
        
        mock_run.side_effect = side_effect
        yield mock_run


@pytest.fixture
def sample_config(tmp_path):
    """Create sample config file."""
    config_content = '''
[defaults]
cpu = 2
mem = "8G"

[tools.myapp]
cpu = 4
modules = ["myapp/1.0"]
'''
    config_file = tmp_path / "hpc-tools.toml"
    config_file.write_text(config_content)
    return config_file
```

---

## Implementation Order

1. **Phase 1: Core (Week 1)**
   - `core/descriptors.py`
   - `core/job.py`
   - `core/result.py`
   - `core/resources.py`
   - `core/exceptions.py`
   - Basic tests

2. **Phase 2: Schedulers (Week 2)**
   - `schedulers/base.py`
   - `schedulers/detection.py`
   - `schedulers/local/` (full implementation)
   - `schedulers/slurm/` (full implementation)
   - `templates/engine.py`
   - Scheduler tests

3. **Phase 3: Configuration (Week 3)**
   - `core/config.py`
   - `defaults/config.toml`
   - Config tests
   - Integration with Job and schedulers

4. **Phase 4: CLI (Week 4)**
   - `cli/main.py`
   - `cli/run.py`
   - `cli/status.py`
   - `cli/cancel.py`
   - CLI tests

5. **Phase 5: SGE & Advanced (Week 5)**
   - `schedulers/sge/`
   - `core/job_array.py`
   - `workflow/pipeline.py`
   - `modules/loader.py`

6. **Phase 6: Polish (Week 6)**
   - Documentation
   - Additional tests
   - PBS scheduler (if needed)
   - PyPI packaging

---

## pyproject.toml

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "hpc-tools"
version = "0.1.0"
description = "Unified HPC job submission across multiple schedulers"
readme = "README.md"
license = "MIT"
requires-python = ">=3.10"
authors = [
    {name = "Your Name", email = "your@email.com"}
]
keywords = ["hpc", "slurm", "sge", "pbs", "cluster", "job-submission"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Environment :: Console",
    "Intended Audience :: Science/Research",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: MIT License",
    "Operating System :: POSIX :: Linux",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Topic :: System :: Clustering",
    "Topic :: System :: Distributed Computing",
]
dependencies = [
    "typer>=0.9.0",
    "jinja2>=3.0",
    "rich>=13.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0",
    "pytest-cov",
    "mypy",
    "ruff",
]

[project.scripts]
hpc = "hpc_tools.cli.main:cli"

[project.urls]
Homepage = "https://github.com/yourname/hpc-tools"
Documentation = "https://github.com/yourname/hpc-tools#readme"
Repository = "https://github.com/yourname/hpc-tools"

[tool.hatch.build.targets.wheel]
packages = ["src/hpc_tools"]

[tool.ruff]
line-length = 100
target-version = "py310"

[tool.mypy]
python_version = "3.10"
strict = true

[tool.pytest.ini_options]
testpaths = ["tests"]
```

---

## Usage Examples

### CLI Usage

```bash
# Basic submission
hpc run "make -j8 all" --cpu 8 --mem 16G

# Match tool configuration automatically
hpc run vivado -mode batch

# Interactive job
hpc run -I xterm

# Use a job type
hpc run -T fpga "vivado -mode batch"

# Run locally (no scheduler)
hpc run -L "make test"

# Dry run to see what would be submitted
hpc run -n "make all" --cpu 4

# Check job status
hpc status 12345

# Cancel a job
hpc cancel 12345
```

### Python API Usage

```python
from hpc_tools import Job, get_scheduler, Pipeline

# Simple submission
job = Job("make -j8 all", cpu=8, mem="16G")
result = job.submit()
print(f"Submitted: {result.job_id}")

# Wait for completion
result.wait()
print(f"Exit code: {result.returncode}")

# Use configuration
job = Job.from_config("vivado", command="vivado -mode batch -source build.tcl")
result = job.submit()

# Pipeline with dependencies
with Pipeline("build-test") as p:
    build = p.add("make build", name="build", cpu=4)
    test = p.add("make test", name="test", depends_on=["build"])
    package = p.add("make package", name="package", depends_on=["test"])

results = p.submit()
p.wait()
```
