"""Job model - pure data container with no scheduler knowledge."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from hpc_runner.core.descriptors import JobAttribute
from hpc_runner.core.exceptions import ConfigError
from hpc_runner.core.resources import ResourceSet

if TYPE_CHECKING:
    from hpc_runner.core.result import JobResult
    from hpc_runner.schedulers.base import BaseScheduler


# Matches a ``$VAR`` or ``${VAR}`` reference left behind after expansion.
_UNRESOLVED_VAR = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")


def _expand_module_list(entries: list[str] | None, key: str) -> list[str]:
    """Expand ``$VAR`` references in a ``modules``/``modules_path`` list.

    Module names are expanded on the submit host, before the job script's
    ``module purge`` wipes the environment. Unlike ``env_vars``, an entry that
    cannot be resolved is an error: a bare ``module load`` (empty name) or a
    literal ``module load $XCELIUM_MODULE`` in the script is a silent no-op
    that leaves the job running with whatever tools it inherited, which is
    exactly the "works for me" failure this package exists to prevent.

    Args:
        entries: Raw list from the merged config (may be ``None``).
        key: Config key name, for the error message.

    Returns:
        The expanded list.

    Raises:
        ConfigError: If an entry references an undefined environment variable
            or expands to an empty string.
    """
    from hpc_runner.core.config import _expand_env_vars

    result: list[str] = []
    for raw in entries or []:
        expanded = _expand_env_vars(raw)
        unresolved = _UNRESOLVED_VAR.findall(expanded)
        if unresolved:
            names = ", ".join(f"${v}" for v in dict.fromkeys(unresolved))
            raise ConfigError(
                f"{key} entry {raw!r} references undefined environment variable(s) "
                f"{names}; set them in the shell that submits the job"
            )
        if not expanded.strip():
            raise ConfigError(
                f"{key} entry {raw!r} expands to an empty string; the referenced "
                f"environment variable is set but empty in the submitting shell"
            )
        result.append(expanded)
    return result


class Job:
    """HPC job specification.

    This is a pure data container. It has no knowledge of any specific
    scheduler's syntax. Rendering to scheduler-specific formats is handled
    by the scheduler classes.

    Attributes are defined using JobAttribute descriptors, which enables:
    - Clean attribute access: job.name, job.cpu, etc.
    - Iteration over set attributes via iter_attributes()
    - Class-level introspection: Job.name returns the descriptor

    Example:
        job = Job(
            command="python train.py",
            name="training_run",
            cpu=4,
            mem="16G",
            time="4:00:00",
        )

        # Direct access
        print(job.name)  # "training_run"
        print(job.cpu)   # 4

        # Iterate over set attributes
        for attr, value in job.iter_attributes():
            print(f"{attr}={value}")
    """

    # =========================================================================
    # Attribute Descriptors
    # =========================================================================

    # Job identification
    name = JobAttribute[str]("name")

    # Resource requests
    cpu = JobAttribute[int]("cpu")
    mem = JobAttribute[str]("mem")
    time = JobAttribute[str]("time")

    # Scheduling
    queue = JobAttribute[str]("queue")
    priority = JobAttribute[int]("priority")

    # MPI/Multi-node jobs (primarily Slurm, but kept for compatibility)
    nodes = JobAttribute[int]("nodes")
    tasks = JobAttribute[int]("tasks")

    # Output handling
    stdout = JobAttribute[str]("stdout")
    stderr = JobAttribute[str]("stderr")

    # Environment
    inherit_env = JobAttribute[bool]("inherit_env", default=True)
    workdir = JobAttribute[str]("workdir")
    shell = JobAttribute[str]("shell", default="/bin/bash")
    venv = JobAttribute[str]("venv")  # Virtual environment path

    # Working directory behavior
    use_cwd = JobAttribute[bool]("use_cwd", default=True)

    # Note: 'dependency' is NOT a descriptor - it's handled specially by schedulers
    # because it involves both string form (CLI) and programmatic form (Job.after())

    # =========================================================================
    # Attribute Registry - Order matters for directive generation
    # =========================================================================

    RENDERABLE_ATTRIBUTES: list[str] = [
        "shell",
        "use_cwd",
        "inherit_env",
        "name",
        "cpu",
        "mem",
        "time",
        "queue",
        "priority",
        "nodes",
        "tasks",
        "stdout",
        "stderr",
    ]

    # =========================================================================
    # Initialization
    # =========================================================================

    def __init__(
        self,
        command: str | list[str],
        *,
        job_type: str | None = None,
        name: str | None = None,
        cpu: int | None = None,
        mem: str | None = None,
        time: str | None = None,
        queue: str | None = None,
        priority: int | None = None,
        nodes: int | None = None,
        tasks: int | None = None,
        stdout: str | None = None,
        stderr: str | None = None,
        inherit_env: bool | None = None,
        workdir: str | None = None,
        shell: str | None = None,
        use_cwd: bool | None = None,
        venv: str | None = None,
        env_vars: dict[str, str] | None = None,
        env_prepend: dict[str, str] | None = None,
        env_append: dict[str, str] | None = None,
        modules: list[str] | None = None,
        modules_path: list[str] | None = None,
        extra_modules: list[str] | None = None,
        extra_modules_path: list[str] | None = None,
        resources: ResourceSet | None = None,
        raw_args: list[str] | None = None,
        sge_args: list[str] | None = None,
        slurm_args: list[str] | None = None,
        pbs_args: list[str] | None = None,
        dependency: str | None = None,
    ):
        # Command handling
        if isinstance(command, list):
            self.command = " ".join(command)
        else:
            self.command = command

        # -----------------------------------------------------------------
        # Config merge: [defaults] → tool/type config → explicit kwargs
        # -----------------------------------------------------------------

        from hpc_runner.core.config import get_config

        config = get_config()

        if job_type is not None:
            job_config = config.get_type_config(job_type)
        else:
            job_config = config.get_tool_config(self.command)

        # Command always comes from the caller, never from config
        job_config.pop("command", None)

        # Build config_overrides from non-None kwargs (descriptor + template
        # attrs only — per-invocation attrs like raw_args and dependency are
        # handled separately below and never come from config).
        config_overrides: dict[str, Any] = {}
        for key, val in {
            "name": name,
            "cpu": cpu,
            "mem": mem,
            "time": time,
            "queue": queue,
            "priority": priority,
            "nodes": nodes,
            "tasks": tasks,
            "stdout": stdout,
            "stderr": stderr,
            "inherit_env": inherit_env,
            "workdir": workdir,
            "shell": shell,
            "use_cwd": use_cwd,
            "venv": venv,
            "env_vars": env_vars,
            "env_prepend": env_prepend,
            "env_append": env_append,
            "modules": modules,
            "modules_path": modules_path,
        }.items():
            if val is not None:
                config_overrides[key] = val

        job_config.update(config_overrides)

        # Append extra_modules / extra_modules_path with deduplication
        for key, extras in (
            ("modules", extra_modules),
            ("modules_path", extra_modules_path),
        ):
            if extras:
                base = job_config.get(key) or []
                seen: set[str] = set()
                merged: list[str] = []
                for item in base + extras:
                    if item not in seen:
                        seen.add(item)
                        merged.append(item)
                job_config[key] = merged

        # -----------------------------------------------------------------
        # Attribute assignment from merged config
        # -----------------------------------------------------------------

        # Name: auto-generate if not in config or kwargs
        self.name = job_config.get("name") or self._generate_name()

        # Descriptor-based attributes: only set if present in merged config,
        # otherwise the descriptor default applies (e.g. inherit_env=True)
        for attr in (
            "cpu",
            "mem",
            "time",
            "queue",
            "priority",
            "nodes",
            "tasks",
            "stdout",
            "stderr",
            "inherit_env",
            "workdir",
            "shell",
            "use_cwd",
        ):
            if attr in job_config:
                setattr(self, attr, job_config[attr])

        # Virtual environment - auto-capture from VIRTUAL_ENV if not specified
        venv_val = job_config.get("venv")
        if venv_val is None:
            venv_val = os.environ.get("VIRTUAL_ENV")
        self.venv = venv_val

        # Non-descriptor attributes — expand $VAR references so that
        # values are captured *before* module purge wipes the environment.
        from hpc_runner.core.config import _expand_env_vars

        def _expand_dict_values(d: dict[str, str] | None) -> dict[str, str]:
            if not d:
                return {}
            return {k: _expand_env_vars(v) for k, v in d.items()}

        self.env_vars: dict[str, str] = _expand_dict_values(job_config.get("env_vars"))
        self.env_prepend: dict[str, str] = _expand_dict_values(job_config.get("env_prepend"))
        self.env_append: dict[str, str] = _expand_dict_values(job_config.get("env_append"))
        # Module names are expanded here too, and unlike env_vars an
        # unresolved or empty entry is fatal (see _expand_module_list).
        self.modules: list[str] = _expand_module_list(job_config.get("modules"), "modules")
        self.modules_path: list[str] = _expand_module_list(
            job_config.get("modules_path"), "modules_path"
        )

        # Handle resources list-of-dicts -> ResourceSet conversion from config.
        if resources is None and "resources" in job_config:
            raw_resources = job_config.pop("resources")
            if isinstance(raw_resources, list):
                resources = ResourceSet()
                for r in raw_resources:
                    resources.add(r["name"], r["value"])
            elif isinstance(raw_resources, ResourceSet):
                resources = raw_resources
        else:
            job_config.pop("resources", None)

        self.resources: ResourceSet = resources or ResourceSet()

        # Per-invocation attrs — straight from kwargs, never from config.
        self.raw_args: list[str] = raw_args or []
        self.sge_args: list[str] = sge_args or []
        self.slurm_args: list[str] = slurm_args or []
        self.pbs_args: list[str] = pbs_args or []
        self.dependency: str | None = dependency

        # Programmatic dependencies (from .after() method)
        self.dependencies: list[JobResult] = []
        self.dependency_type: str = "afterok"

    # =========================================================================
    # Submission API
    # =========================================================================

    def submit(
        self,
        scheduler: BaseScheduler | None = None,
        keep_script: bool = False,
        interactive: bool = False,
    ) -> JobResult:
        """Submit the job to a scheduler.

        This is the primary programmatic API for job submission.

        Args:
            scheduler: Scheduler to use. If None, auto-detects based on
                       environment (checks HPC_SCHEDULER env var, then
                       probes for SGE_ROOT, sbatch, etc.)
            keep_script: If True, don't delete the generated job script
                         after submission. Useful for debugging.
            interactive: If True, run the job interactively (e.g. qrsh
                         for SGE) instead of submitting to the batch queue.

        Returns:
            JobResult with job ID and methods to check status, get output, etc.

        Example:
            job = Job("python train.py", cpu=4, mem="16G")
            result = job.submit()
            print(f"Submitted: {result.job_id}")

            # Wait for completion
            result.wait()
            print(f"Exit code: {result.exit_code}")
        """
        from hpc_runner.schedulers import get_scheduler

        if scheduler is None:
            scheduler = get_scheduler()
        return scheduler.submit(self, interactive=interactive, keep_script=keep_script)

    # =========================================================================
    # Attribute Iteration
    # =========================================================================

    def iter_attributes(self) -> Iterator[tuple[str, Any]]:
        """Iterate over renderable attributes that have been set.

        Yields:
            Tuples of (attribute_name, value) for attributes that are not None
            and not equal to their default "skip" values.

        Note:
            The iteration order follows RENDERABLE_ATTRIBUTES, which is
            designed to produce sensible directive ordering.
        """
        for attr_name in self.RENDERABLE_ATTRIBUTES:
            value = getattr(self, attr_name)

            # Skip None values
            if value is None:
                continue

            # Skip False for boolean attributes (they're opt-in)
            # Exception: use_cwd and inherit_env default True, so False means explicit opt-out
            descriptor = getattr(self.__class__, attr_name)
            if isinstance(value, bool) and value is False and descriptor.default is not True:
                continue

            yield attr_name, value

    # =========================================================================
    # Properties
    # =========================================================================

    @property
    def merge_output(self) -> bool:
        """Whether to merge stderr into stdout."""
        return self.stderr is None

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _generate_name(self) -> str:
        """Generate a job name from username and command."""
        user = os.environ.get("USER", "user")
        # Extract first meaningful word from command
        cmd_parts = self.command.split()
        for part in cmd_parts:
            if "=" not in part:
                cmd_name = part.split("/")[-1]  # Handle paths
                return f"{user}_{cmd_name}"
        return f"{user}_job"

    def after(
        self,
        *jobs: JobResult,
        type: str = "afterok",
    ) -> Job:
        """Add job dependencies.

        Args:
            *jobs: JobResult objects this job depends on
            type: Dependency type (afterok, afterany, afternotok)

        Returns:
            Self for method chaining
        """
        self.dependencies.extend(jobs)
        self.dependency_type = type
        return self

    def __repr__(self) -> str:
        attrs = []
        for attr, value in self.iter_attributes():
            attrs.append(f"{attr}={value!r}")
        return f"Job(command={self.command!r}, {', '.join(attrs)})"
