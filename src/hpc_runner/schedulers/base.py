"""Base scheduler with rendering protocol."""

from __future__ import annotations

import re
import shlex
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from types import EllipsisType
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from hpc_runner.core.descriptors import SchedulerArg
from hpc_runner.core.exceptions import ConfigError, NotSupportedError
from hpc_runner.core.job import RENDER_ORDER
from hpc_runner.core.result import TASK_ID_PLACEHOLDER
from hpc_runner.schedulers import _subprocess
from hpc_runner.templates import ShellWord

if TYPE_CHECKING:
    import subprocess

    from hpc_runner.core.config import HPCConfig
    from hpc_runner.core.job import Job
    from hpc_runner.core.job_info import JobInfo
    from hpc_runner.core.result import JobResult, JobStatus


# A ``${hpc.<name>}`` reference (names are checked by resolve()).
_HPC_REF = re.compile(r"\$\{hpc\.([A-Za-z_][A-Za-z0-9_]*)\}")
_HOST_REF = "${hpc.host}"
# Shell text that is a bare variable expansion, e.g. ``$NSLOTS``.
_SHELL_VAR = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*")

#: Accepted values of ``[schedulers.<name>] module_system``.
MODULE_SYSTEMS = ("modules", "lmod", "")

RenderContext = Literal["script", "path"]
Stream = Literal["stdout", "stderr"]


def refuse_interactive_dependencies(job: Job) -> None:
    """Refuse an interactive job with dependencies rather than ignore them.

    Raises:
        NotSupportedError: *job* is interactive and has dependencies.
    """
    if job.interactive and job.dependencies:
        raise NotSupportedError("interactive jobs cannot have dependencies; use --batch")


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

    #: The shell text each ``${hpc.<name>}`` becomes inside the job script.
    HPC_VARS: ClassVar[Mapping[str, str]] = {}
    #: Overrides of :attr:`HPC_VARS` where the scheduler itself expands a
    #: path (output file names), which can differ from the job's shell.
    HPC_PATH_VARS: ClassVar[Mapping[str, str]] = {}

    def __init__(self, config: HPCConfig) -> None:
        self.config = config
        settings = config.get_scheduler_config(self.name)
        # Site module setup shared by every backend's environment block.
        self.module_init_script: str = settings.get("module_init_script", "")
        self.module_system: str = settings.get("module_system", "")
        if self.module_system not in MODULE_SYSTEMS:
            raise ConfigError(
                f"[schedulers.{self.name}] module_system: expected one of "
                f"{', '.join(repr(m) for m in MODULE_SYSTEMS)}, got {self.module_system!r}"
            )

    # =========================================================================
    # ${hpc.*} and the shared environment block
    # =========================================================================

    def hpc_vars(self, job: Job, *, job_id: str | None = None) -> Mapping[str, str]:
        """The shell text for each ``${hpc.*}`` name in *job*'s script.

        Override when values are known literally (e.g. the local scheduler,
        which allocates *job_id* itself before rendering).
        """
        return self.HPC_VARS

    def render_hpc_vars(
        self,
        text: str,
        *,
        job: Job,
        job_id: str | None = None,
        context: RenderContext = "script",
    ) -> str:
        """*text* with each ``${hpc.x}`` replaced by this scheduler's rendering.

        *context* ``"path"`` applies :attr:`HPC_PATH_VARS` (a path the
        scheduler expands, such as ``-o``); ``"script"`` is the job shell.
        """
        mapping = dict(self.hpc_vars(job, job_id=job_id))
        if context == "path":
            mapping.update(self.HPC_PATH_VARS)
        return _HPC_REF.sub(lambda m: mapping[m.group(1)], text)

    def shell_word(self, text: str, *, job: Job, job_id: str | None = None) -> ShellWord:
        """*text* as one shell word with its ``${hpc.*}`` references rendered live.

        Literal parts are ``shlex``-quoted; each reference becomes this
        scheduler's shell text (a variable is double-quoted so it neither
        splits nor runs into the following text).
        """
        mapping = self.hpc_vars(job, job_id=job_id)
        parts: list[str] = []
        pos = 0
        for match in _HPC_REF.finditer(text):
            if match.start() > pos:
                parts.append(shlex.quote(text[pos : match.start()]))
            shell = mapping[match.group(1)]
            parts.append(f'"{shell}"' if _SHELL_VAR.fullmatch(shell) else shell)
            pos = match.end()
        if pos < len(text) or not parts:
            parts.append(shlex.quote(text[pos:]))
        return ShellWord("".join(parts))

    def environment_context(
        self, job: Job, *, job_id: str | None = None, verbose: bool = False
    ) -> dict[str, Any]:
        """Template context for ``_environment.sh.j2``, the shared environment block.

        Values that may contain ``${hpc.*}`` are pre-rendered
        :class:`~hpc_runner.templates.ShellWord` objects; the template
        passes every value through the ``sh`` filter.
        """

        def word(text: str) -> ShellWord:
            return self.shell_word(text, job=job, job_id=job_id)

        return {
            "module_init_script": self.module_init_script,
            "module_system": self.module_system,
            "verbose": verbose,
            "modules_path": [word(p) for p in job.modules_path],
            "modules_path_append": [word(p) for p in job.modules_path_append],
            "modules": [word(m) for m in job.modules],
            "venv": word(job.venv) if job.venv else None,
            "env_unset": list(job.env_unset),
            "env_prepend": [(k, word(v)) for k, v in job.env_prepend.items()],
            "env_append": [(k, word(v)) for k, v in job.env_append.items()],
            "env_vars": [(k, word(v)) for k, v in job.env_vars.items()],
        }

    # =========================================================================
    # Output paths (D9)
    # =========================================================================

    def job_name(self, job: Job) -> str:
        """The name this scheduler submits *job* under.

        The name is part of the default output file name on every
        scheduler, so whitespace, ``/`` and non-printable characters become
        ``_``. A scheduler with stricter rules extends this.
        """
        return "".join(
            "_" if c == "/" or c.isspace() or not c.isprintable() else c for c in job.name
        )

    def output_pattern(self, job: Job, stream: Stream) -> str | None:
        """*job*'s output pattern for *stream*, as the scheduler renders it.

        None for ``stderr`` when it is merged into stdout. An array job's
        pattern without ``${hpc.task_id}`` gets ``.${hpc.task_id}``
        appended, so tasks never write the same file.
        """
        pattern = job.stdout if stream == "stdout" else job.stderr
        if pattern is None:
            return None
        if job.array is not None and TASK_ID_PLACEHOLDER not in pattern:
            pattern += f".{TASK_ID_PLACEHOLDER}"
        return pattern

    def output_paths(self, job: Job, job_id: str) -> tuple[Path | None, Path | None]:
        """``(stdout_path, stderr_path)`` recorded on *job*'s result at submit.

        The known values (job name and *job_id*) are substituted in Python;
        an array job's paths keep ``${hpc.task_id}``. A path is None when
        *job* is interactive, when stderr is merged, or when the pattern
        uses ``${hpc.host}``. Relative patterns are relative to
        ``job.workdir``.
        """
        if job.interactive:
            return None, None
        return (
            self._output_path(job, self.output_pattern(job, "stdout"), job_id),
            self._output_path(job, self.output_pattern(job, "stderr"), job_id),
        )

    def _output_path(self, job: Job, pattern: str | None, job_id: str) -> Path | None:
        if pattern is None or _HOST_REF in pattern:
            return None
        text = pattern.replace("${hpc.job_name}", self.job_name(job))
        return Path(job.workdir) / text.replace("${hpc.job_id}", job_id)

    def make_output_dirs(self, job: Job) -> None:
        """Create the directory of each output path on the submit host.

        Called by ``submit()`` before submission: SGE silently discards
        output whose directory does not exist. ``resolve()`` rejects
        ``${hpc.*}`` in the directory part, so it is always known here.
        """
        if job.interactive:
            return
        streams: tuple[Stream, ...] = ("stdout", "stderr")
        for stream in streams:
            pattern = self.output_pattern(job, stream)
            if pattern is not None:
                (Path(job.workdir) / pattern).parent.mkdir(parents=True, exist_ok=True)

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
            value = self.render_value(job, attr_name)
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
            value = self.render_value(job, attr_name)
            if value is None:
                continue
            renderer = self.ARG_RENDERERS.get(attr_name)
            if renderer is None or not renderer.interactive_ok:
                continue
            args.extend(renderer.to_args(value))
        return args

    def render_value(self, job: Job, attr_name: str) -> Any:
        """The value of *attr_name* handed to its ``ARG_RENDERERS`` entry.

        The base passes the job attribute through; a scheduler overrides
        this where its rendering needs more of the job (e.g. sanitising the
        name, or dividing ``mem`` by ``cpu``).
        """
        return getattr(job, attr_name)

    def _passthrough_args(self, job: Job) -> tuple[str, ...]:
        """The job's raw scheduler arguments for this scheduler."""
        return job.scheduler_args.get(self.name, ())

    # =========================================================================
    # Abstract Methods - Subclasses must implement
    # =========================================================================

    @abstractmethod
    def submit(self, job: Job, *, keep_script: bool = False, verbose: bool = False) -> JobResult:
        """Submit a job.

        Runs it attached (and returns once it has finished) when
        ``job.interactive``; returns an ``ArrayJobResult`` when ``job.array``.

        Args:
            job: Resolved job to submit.
            keep_script: If True, don't delete the job script after submission.
            verbose: If True, module commands print their output instead of
                showing it only when one fails.
        """

    @abstractmethod
    def preview(self, job: Job, *, verbose: bool = False) -> str:
        """Return the script that :meth:`submit` would write for *job*."""

    @abstractmethod
    def cancel(self, job_id: str) -> bool:
        """Cancel a job."""

    @abstractmethod
    def get_statuses(self, job_ids: Sequence[str]) -> dict[str, JobStatus]:
        """The status of each job in *job_ids*, from one bulk query.

        Ids may be array task ids (see :meth:`task_job_id`); each reports
        its own task's state. Every id in *job_ids* is a key of the result.
        """

    def get_status(self, job_id: str) -> JobStatus:
        """The status of one job (see :meth:`get_statuses`)."""
        return self.get_statuses([job_id])[job_id]

    def task_job_id(self, base: str, index: int) -> str:
        """The id this scheduler uses for task *index* of array job *base*.

        Each backend that supports arrays defines its own form (SGE and PBS
        ``<base>.<index>``, Slurm ``<base>_<index>``).

        Raises:
            NotSupportedError: This scheduler has no array jobs.
        """
        raise NotSupportedError(f"the {self.name} scheduler does not support array jobs")

    @abstractmethod
    def get_exit_code(self, job_id: str) -> int | None:
        """Get job exit code."""

    def result(self, job_id: str) -> JobResult:
        """A handle on an existing job, without the in-memory ``Job``.

        The job's output patterns are not known from an id, so the handle's
        ``stdout_path`` and ``stderr_path`` are None.
        """
        from hpc_runner.core.result import JobResult

        return JobResult(scheduler=self, job_id=job_id)

    # =========================================================================
    # Optional Methods - Override if scheduler supports these
    # =========================================================================

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
