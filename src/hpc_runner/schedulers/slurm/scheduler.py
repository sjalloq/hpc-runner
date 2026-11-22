"""Slurm scheduler implementation: rendering and submission."""

from __future__ import annotations

import logging
import re
import sys
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from hpc_runner.core.exceptions import ConfigError, SchedulerCommandError, SubmissionError
from hpc_runner.core.job import RENDER_ORDER, SCHEDULER_OPTIONS
from hpc_runner.core.result import ArrayJobResult, JobResult, JobStatus
from hpc_runner.schedulers.base import (
    MAPS,
    REJECTS,
    BaseScheduler,
    NotApplicable,
    Support,
    get_script_dir,
    refuse_interactive_dependencies,
)
from hpc_runner.schedulers.slurm.args import (
    MEMORY_PER,
    SlurmAccountArg,
    SlurmArg,
    SlurmArrayArg,
    SlurmChdirArg,
    SlurmCpuArg,
    SlurmDependencyArg,
    SlurmDevicesArg,
    SlurmErrorArg,
    SlurmExportArg,
    SlurmFeaturesArg,
    SlurmJobNameArg,
    SlurmLicensesArg,
    SlurmMemArg,
    SlurmOutputArg,
    SlurmPartitionArg,
    SlurmTimeArg,
    export_value,
)
from hpc_runner.schedulers.slurm.parser import parse_sbatch_output
from hpc_runner.templates import render_template
from hpc_runner.trace import recorder

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from hpc_runner.core.config import HPCConfig
    from hpc_runner.core.job import Job

# Script path shown by preview(); never written or deleted.
_PREVIEW_SCRIPT_PATH = "<script>"

#: What a ``#SBATCH`` line does not carry as written: whitespace splits the
#: line, quotes and backslashes are taken out, ``#`` ends it (run on the
#: testbed, 24.11 and 22.05).
_NOT_IN_A_DIRECTIVE = re.compile(r"""[\s'"\\#]""")

_TO_THE_TERMINAL = NotApplicable("an interactive job's output is the terminal; no file is named")


class SlurmScheduler(BaseScheduler):
    """Slurm scheduler implementation."""

    name = "slurm"

    CONFIG_KEYS = {
        "memory_per": str,
        "kill_invalid_dependents": bool,
    }

    BATCH_OPTIONS: ClassVar[Mapping[str, Support]] = dict.fromkeys(SCHEDULER_OPTIONS, MAPS)
    INTERACTIVE_OPTIONS: ClassVar[Mapping[str, Support]] = {
        **BATCH_OPTIONS,
        # --output and --error are never sent to srun
        # (``interactive_ok=False`` in slurm/args.py).
        "stdout": _TO_THE_TERMINAL,
        "stderr": _TO_THE_TERMINAL,
        "array": REJECTS,  # resolve() already refuses an interactive array
        "dependencies": REJECTS,
    }

    HPC_VARS = {
        "cpus": "$SLURM_CPUS_PER_TASK",
        # The id sbatch printed: an array task has its own SLURM_JOB_ID.
        "job_id": '"${SLURM_ARRAY_JOB_ID:-$SLURM_JOB_ID}"',
        "task_id": "$SLURM_ARRAY_TASK_ID",
        "tmpdir": "$TMPDIR",
        "job_name": "$SLURM_JOB_NAME",
        "host": "$HOSTNAME",
    }
    # Slurm fills --output/--error paths itself, from its own symbols.
    HPC_PATH_VARS = {"job_id": "%j", "task_id": "%a", "job_name": "%x", "host": "%N"}

    def __init__(self, config: HPCConfig) -> None:
        """Initialize Slurm scheduler with config-driven settings."""
        super().__init__(config)
        slurm_config = config.get_scheduler_config("slurm")

        # ``mem`` is always per job. "cpu" is for a site that wants the
        # request made per CPU: it is divided by the job's CPUs.
        self.memory_per: str = slurm_config.get("memory_per", "job")
        if self.memory_per not in MEMORY_PER:
            raise ConfigError(
                "[schedulers.slurm] memory_per: expected one of "
                f"{', '.join(repr(m) for m in MEMORY_PER)}, got {self.memory_per!r}"
            )
        # A dependent whose parent did not end as asked is cancelled by
        # Slurm instead of pending for ever.
        self.kill_invalid_dependents: bool = slurm_config.get("kill_invalid_dependents", True)

        self._mem_arg = SlurmMemArg(memory_per=self.memory_per)
        self.ARG_RENDERERS = {
            "inherit_env": SlurmExportArg(),
            "name": SlurmJobNameArg(),
            "cpu": SlurmCpuArg(),
            "mem": self._mem_arg,
            "time": SlurmTimeArg(),
            "queue": SlurmPartitionArg(),
            "project": SlurmAccountArg(),
            "stdout": SlurmOutputArg(),
            "stderr": SlurmErrorArg(),
        }
        self._chdir_arg = SlurmChdirArg()
        self._array_arg = SlurmArrayArg()
        self._dependency_arg = SlurmDependencyArg(kill_invalid=self.kill_invalid_dependents)
        self._licenses_arg = SlurmLicensesArg()
        self._devices_arg = SlurmDevicesArg()
        self._features_arg = SlurmFeaturesArg()

    # =========================================================================
    # Rendering
    # =========================================================================

    def job_name(self, job: Job) -> str:
        """*job*'s name: the base rules, then ``_`` for what a ``#SBATCH`` line alters."""
        return _NOT_IN_A_DIRECTIVE.sub("_", super().job_name(job))

    def hpc_path_vars(self, job: Job) -> Mapping[str, str]:
        """An array's ``${hpc.job_id}`` is ``%A``, the id every task shares."""
        if job.array is not None:
            return {**self.HPC_PATH_VARS, "job_id": "%A"}
        return self.HPC_PATH_VARS

    def render_value(self, job: Job, attr_name: str) -> Any:
        """Slurm's view of *attr_name*.

        ``inherit_env`` is the whole ``--export`` value, ``cpu`` is 1 when
        unset (it is always sent), ``mem`` is per CPU when the site says so,
        and output paths are absolute (joined onto ``job.workdir``, so they
        match the recorded ``stdout_path``) with Slurm's own symbols.
        """
        if attr_name == "inherit_env":
            return export_value(job.inherit_env, job.env_passthrough)
        if attr_name == "name":
            return self.job_name(job)
        if attr_name == "cpu":
            return job.cpu or 1
        if attr_name == "mem" and job.mem is not None:
            return self._mem_arg.for_job(job.mem, job.cpu or 1)
        if attr_name in ("stdout", "stderr"):
            pattern = self.output_pattern(job, "stdout" if attr_name == "stdout" else "stderr")
            if pattern is None:
                return None
            path = str(Path(job.workdir) / pattern)
            return self.render_hpc_vars(path, job=job, context="path")
        return super().render_value(job, attr_name)

    def render_directives(self, job: Job) -> list[str]:
        """The base directives after ``--chdir``: a job always runs in ``job.workdir``."""
        chdir = self._chdir_arg.to_directive(job.workdir)
        return [*([chdir] if chdir else []), *super().render_directives(job)]

    def _render_interactive_args(self, job: Job) -> list[str]:
        """The base arguments after ``--chdir``."""
        return [*self._chdir_arg.to_args(job.workdir), *super()._render_interactive_args(job)]

    def _requests(self, job: Job) -> list[tuple[SlurmArg[Any], Any]]:
        """The options outside ``RENDER_ORDER``, each renderer with its value."""
        return [
            (self._array_arg, job.array),
            (self._dependency_arg, job.dependencies),
            (self._licenses_arg, job.licenses),
            (self._devices_arg, job.devices),
            (self._features_arg, job.features),
        ]

    def _build_directives(self, job: Job) -> list[str]:
        """Every ``#SBATCH`` line of *job*'s script."""
        self._check_directive_values(job)
        directives = self.render_directives(job)
        for renderer, value in self._requests(job):
            if directive := renderer.to_directive(value):
                directives.append(directive)
        # Raw passthrough arguments, on one line as on a command line.
        passthrough = self._passthrough_args(job)
        if passthrough:
            directives.append("#SBATCH " + " ".join(passthrough))
        return directives

    def _check_directive_values(self, job: Job) -> None:
        """Refuse a value a ``#SBATCH`` line would not carry as written.

        Every option stays in the script, so a value with whitespace is
        refused here, as on SGE, and with it a quote, a backslash or ``#``.

        Raises:
            SubmissionError: The working directory, a rendered attribute, a
                passed-through variable, or the name of a licence, device
                or feature contains one of those characters.
        """
        offending: list[str] = []
        if _NOT_IN_A_DIRECTIVE.search(job.workdir):
            offending.append(f"workdir = {job.workdir!r}")
        for attr_name in RENDER_ORDER:
            if attr_name == "inherit_env":
                continue  # the --export value: its variables are named below
            value = self.render_value(job, attr_name)
            if isinstance(value, str) and _NOT_IN_A_DIRECTIVE.search(value):
                offending.append(f"{attr_name} = {value!r}")
        for name, value in job.env_passthrough.items():
            if _NOT_IN_A_DIRECTIVE.search(value):
                offending.append(f"env_passthrough {name} = {value!r}")
        for field in ("licenses", "devices", "features"):
            for name in getattr(job, field):
                if _NOT_IN_A_DIRECTIVE.search(name):
                    offending.append(f"{field} name {name!r}")
        if offending:
            raise SubmissionError(
                "Slurm cannot take a value with whitespace, a quote, a backslash or '#' "
                "in a job script directive (#SBATCH lines are split on spaces and do not "
                "keep those characters): " + "; ".join(offending),
            )

    def _check(self, job: Job) -> None:
        """Refuse what Slurm, or hpc-runner on Slurm, does not take.

        Raises:
            NotSupportedError: An interactive job with dependencies or an
                array (see ``INTERACTIVE_OPTIONS``).
            SubmissionError: A passed-through variable whose value contains
                ``,``.
        """
        refuse_interactive_dependencies(job)
        self.check_options(job)
        export_value(job.inherit_env, job.env_passthrough)

    def preview(self, job: Job, *, verbose: bool = False) -> str:
        """Return the script :meth:`submit` would write for *job*.

        Rendered as with ``keep_script=True`` (no self-delete line), so it
        is byte-for-byte what ``submit(job, keep_script=True)`` writes.
        """
        self._check(job)
        if job.interactive:
            return self._render_interactive(
                job, _PREVIEW_SCRIPT_PATH, keep_script=True, verbose=verbose
            )
        return self._render_batch(job, verbose=verbose)

    def _render_batch(self, job: Job, verbose: bool = False) -> str:
        """Render the sbatch script: every option is a ``#SBATCH`` directive."""
        return render_template(
            "slurm/templates/batch.sh.j2",
            job=job,
            environment=self.environment_context(job, verbose=verbose),
            directives=self._build_directives(job),
        )

    def _render_interactive(
        self, job: Job, script_path: str, keep_script: bool, verbose: bool = False
    ) -> str:
        """Render the wrapper script srun runs (no directives)."""
        return render_template(
            "slurm/templates/interactive.sh.j2",
            job=job,
            environment=self.environment_context(job, verbose=verbose),
            script_path=script_path,
            keep_script=keep_script,
        )

    # =========================================================================
    # Job Submission
    # =========================================================================

    def submit(self, job: Job, *, keep_script: bool = False, verbose: bool = False) -> JobResult:
        """Submit a job to Slurm: srun when ``job.interactive``, else sbatch.

        Args:
            job: Job to submit.
            keep_script: If True, don't delete the job script after submission.
            verbose: If True, module commands in the script print their
                output rather than only on failure.

        Raises:
            NotSupportedError, SubmissionError: See :meth:`_check`; nothing
                is written or submitted.
        """
        self._check(job)
        if job.interactive:
            return self._submit_interactive(job, keep_script=keep_script, verbose=verbose)
        return self._submit_batch(job, keep_script=keep_script, verbose=verbose)

    def _submit_batch(
        self, job: Job, keep_script: bool = False, verbose: bool = False
    ) -> JobResult:
        """Submit via ``sbatch <script>``; all options are in the script."""
        script = self._render_batch(job, verbose=verbose)
        script_path = get_script_dir() / f"hpc_batch_{uuid.uuid4().hex[:8]}.sh"
        script_path.write_text(script)
        script_path.chmod(0o755)
        recorder.note_script(script_path, script)

        if keep_script:
            logger.debug("Script saved: %s", script_path)

        self.make_output_dirs(job)

        try:
            job_id = self._sbatch(["sbatch", str(script_path)], script=script, job=job)
        finally:
            # sbatch has taken its own copy of the script by the time it returns.
            if not keep_script:
                script_path.unlink(missing_ok=True)

        stdout_path, stderr_path = self.output_paths(job, job_id)
        if job.array is not None:
            return ArrayJobResult(
                scheduler=self,
                job_id=job_id,
                job=job,
                array=job.array,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
            )
        return JobResult(
            scheduler=self,
            job_id=job_id,
            job=job,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )

    def _sbatch(self, argv: list[str], *, script: str, job: Job) -> str:
        """Run sbatch and return the job id.

        Raises:
            SubmissionError: sbatch exited non-zero (message is its stderr)
                or its output carried no job id.
        """
        try:
            result = self._run(argv, cwd=Path(job.workdir))
        except SchedulerCommandError as exc:
            raise SubmissionError(exc.stderr, script=script, argv=exc.argv) from exc
        job_id = parse_sbatch_output(result.stdout)
        if job_id is None:
            raise SubmissionError(
                f"could not parse job id from sbatch output: {result.stdout.strip()!r}",
                script=script,
                argv=argv,
                stderr=result.stderr,
            )
        return job_id

    def _submit_interactive(
        self, job: Job, keep_script: bool = False, verbose: bool = False
    ) -> JobResult:
        """Run via ``srun <script>`` and wait for it.

        The wrapper script (the full environment, then the command) is
        written to the shared script directory, not a node-local one, and
        deletes itself after running unless *keep_script*.
        """
        script_path = get_script_dir() / f"hpc_interactive_{uuid.uuid4().hex[:8]}.sh"
        script = self._render_interactive(
            job, str(script_path), keep_script=keep_script, verbose=verbose
        )
        script_path.write_text(script)
        script_path.chmod(0o755)
        recorder.note_script(script_path, script)

        if keep_script:
            logger.debug("Script saved: %s", script_path)

        cmd = self._build_srun_command(job, str(script_path))

        # srun blocks for the job's lifetime, so no timeout, and its output
        # streams straight to the terminal.
        result = self._run(cmd, timeout=None, check=False, capture=False, cwd=Path(job.workdir))

        if not keep_script:
            script_path.unlink(missing_ok=True)

        return JobResult(scheduler=self, job_id=None, job=job, exit_code=result.returncode)

    @staticmethod
    def _at_a_terminal() -> bool:
        """Whether stdin and stdout are both terminals."""
        return sys.stdin.isatty() and sys.stdout.isatty()

    def _build_srun_command(self, job: Job, script_path: str) -> list[str]:
        """Build the srun argv that runs the wrapper script.

        srun has no directives, so options go on its command line.
        ``--pty`` only at a terminal; ``--output`` and ``--error`` never
        (``interactive_ok``).
        """
        cmd = ["srun"]
        if self._at_a_terminal():
            cmd.append("--pty")
        cmd.extend(self._render_interactive_args(job))
        for renderer, value in self._requests(job):
            if renderer.interactive_ok:
                cmd.extend(renderer.to_args(value))
        cmd.extend(self._passthrough_args(job))
        cmd.append(script_path)
        return cmd

    def task_job_id(self, base: str, index: int) -> str:
        """Slurm names an array task ``<base>_<index>``."""
        return f"{base}_{index}"

    # =========================================================================
    # Job Management
    # =========================================================================

    def cancel(self, job_id: str) -> bool:
        raise NotImplementedError

    def get_statuses(self, job_ids: Sequence[str]) -> dict[str, JobStatus]:
        raise NotImplementedError

    def get_exit_code(self, job_id: str) -> int | None:
        raise NotImplementedError
