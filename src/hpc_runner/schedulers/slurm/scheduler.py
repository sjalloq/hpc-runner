"""Slurm scheduler implementation."""

from __future__ import annotations

import logging
import re
import sys
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from hpc_runner.core.exceptions import (
    AccountingNotAvailable,
    ConfigError,
    JobNotFoundError,
    SchedulerCommandError,
    SchedulerUnavailableError,
    SubmissionError,
)
from hpc_runner.core.job import RENDER_ORDER, SCHEDULER_OPTIONS
from hpc_runner.core.job_info import JobInfo
from hpc_runner.core.result import ArrayJobResult, JobResult, JobStatus
from hpc_runner.schedulers.base import (
    MAPS,
    REJECTS,
    BaseScheduler,
    NotApplicable,
    Support,
    array_job_info,
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
from hpc_runner.schedulers.slurm.parser import (
    SACCT_FORMAT,
    SQUEUE_FORMAT,
    Row,
    SacctJob,
    parse_exit_code,
    parse_sacct,
    parse_sbatch_output,
    parse_scontrol_job,
    parse_squeue,
    sacct_status,
    sacct_to_job_info,
    scontrol_base,
    scontrol_exit_code,
    scontrol_has_ended,
    scontrol_is_completing,
    scontrol_output_paths,
    scontrol_tasks,
    select_jobs,
    split_job_id,
    state_to_status,
)
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

#: A new job reaches accounting a moment after the queue: how long ``sacct``
#: is asked again for a job it has no row for, and how often (seconds).
ACCOUNTING_WAIT = 10.0
ACCOUNTING_POLL = 1.0
#: A job that has just ended is ``COMPLETING`` to the controller for a moment,
#: with no exit code yet: how long ``scontrol`` is asked again, and how often.
EXIT_CODE_WAIT = 5.0
EXIT_CODE_POLL = 0.5

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
        # Not $HOSTNAME: an interactive job that inherits the environment
        # has the submit host's.
        "host": "$SLURMD_NODENAME",
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
            command=self.render_command(job),
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
            command=self.render_command(job),
            environment=self.environment_context(job, verbose=verbose),
            script_path=script_path,
            keep_script=keep_script,
        )

    # =========================================================================
    # Job Submission
    # =========================================================================

    def submit(
        self,
        job: Job,
        *,
        keep_script: bool = False,
        verbose: bool = False,
        wait: bool = False,
        on_submit: Callable[[JobResult], None] | None = None,
    ) -> JobResult:
        """Submit a job to Slurm: srun when ``job.interactive``, else sbatch.

        Args:
            job: Job to submit.
            keep_script: If True, don't delete the job script after submission.
            verbose: If True, module commands in the script print their
                output rather than only on failure.
            wait: Submit with ``sbatch --wait``, which prints the job id at
                once and exits when the job ends, with the job's exit code.
            on_submit: Called with the batch job's handle once sbatch has
                printed its id.

        Raises:
            NotSupportedError, SubmissionError: See :meth:`_check`; nothing
                is written or submitted.
        """
        self._check(job)
        if job.interactive:
            return self._submit_interactive(job, keep_script=keep_script, verbose=verbose)
        return self._submit_batch(
            job, keep_script=keep_script, verbose=verbose, wait=wait, on_submit=on_submit
        )

    def _submit_batch(
        self,
        job: Job,
        keep_script: bool = False,
        verbose: bool = False,
        *,
        wait: bool = False,
        on_submit: Callable[[JobResult], None] | None = None,
    ) -> JobResult:
        """Submit via ``sbatch <script>``; all options are in the script.

        With *wait* the command is ``sbatch --wait <script>`` and the
        result is of a job that has ended (:meth:`_waited`).
        """
        script = self._render_batch(job, verbose=verbose)
        script_path = get_script_dir() / f"hpc_batch_{uuid.uuid4().hex[:8]}.sh"
        script_path.write_text(script)
        script_path.chmod(0o755)
        recorder.note_script(script_path, script)

        if keep_script:
            logger.debug("Script saved: %s", script_path)

        self.make_output_dirs(job)

        try:
            if wait:
                result, returncode = self._sbatch_wait(
                    ["sbatch", "--wait", str(script_path)],
                    script=script,
                    job=job,
                    on_submit=on_submit,
                )
            else:
                job_id = self._sbatch(["sbatch", str(script_path)], script=script, job=job)
        finally:
            # sbatch has taken its own copy of the script by the time it returns.
            if not keep_script:
                script_path.unlink(missing_ok=True)
        if wait:
            return self._waited(result, returncode)
        result = self._batch_result(job, job_id)
        if on_submit is not None:
            on_submit(result)
        return result

    def _batch_result(self, job: Job, job_id: str) -> JobResult:
        """The handle on *job*, submitted as *job_id*."""
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

    def _sbatch_wait(
        self,
        argv: list[str],
        *,
        script: str,
        job: Job,
        on_submit: Callable[[JobResult], None] | None,
    ) -> tuple[JobResult, int]:
        """Run ``sbatch --wait``; returns the job's handle and sbatch's exit status.

        sbatch runs for the job's lifetime, so there is no timeout. Its
        ``Submitted batch job <id>`` line is read as it is printed.

        Raises:
            SubmissionError: sbatch ended without printing a job id
                (message is its stderr when it exited non-zero).
        """
        results: list[JobResult] = []

        def on_line(line: str) -> None:
            job_id = parse_sbatch_output(line)
            if job_id is not None and not results:
                results.append(self._batch_result(job, job_id))
                if on_submit is not None:
                    on_submit(results[0])

        done = self._run(
            argv, timeout=None, check=False, cwd=Path(job.workdir), on_stdout_line=on_line
        )
        if not results:
            if done.returncode != 0:
                raise SubmissionError(done.stderr, script=script, argv=argv)
            raise SubmissionError(
                f"could not parse job id from sbatch output: {done.stdout.strip()!r}",
                script=script,
                argv=argv,
                stderr=done.stderr,
            )
        return results[0], done.returncode

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
        """Cancel a job, or every task of an array, via ``scancel <id>``.

        Returns False when scancel refuses (somebody else's job); a
        missing scancel raises ``SchedulerUnavailableError``.
        """
        try:
            self._run(["scancel", job_id])
            return True
        except SchedulerCommandError:
            return False

    def _sacct(self, job_ids: Sequence[str]) -> list[SacctJob]:
        """The ``sacct`` rows of *job_ids*, from one command.

        Asked by the id each job was submitted under: a task id is looked
        up in the rows of its array. No ``-X``, so the ``.batch`` rows
        come too.
        """
        bases = list(dict.fromkeys(split_job_id(job_id)[0] for job_id in job_ids))
        if not bases:
            return []
        argv = ["sacct", "-P", "-n", "-j", ",".join(bases), f"--format={SACCT_FORMAT}"]
        return parse_sacct(self._run(argv).stdout)

    def _sacct_again(self, job_ids: Sequence[str], jobs: list[SacctJob]) -> list[SacctJob]:
        """*jobs*, the ``sacct`` rows of *job_ids*, with the rows that come late.

        ``sacct`` is asked again every :data:`ACCOUNTING_POLL` seconds, for
        the ids with no row only, until each has one or
        :data:`ACCOUNTING_WAIT` seconds have gone.
        """
        deadline = time.monotonic() + ACCOUNTING_WAIT
        while time.monotonic() < deadline:
            missing = [job_id for job_id in job_ids if not select_jobs(jobs, job_id)]
            if not missing:
                break
            time.sleep(ACCOUNTING_POLL)
            asked = {split_job_id(job_id)[0] for job_id in missing}
            jobs = [job for job in jobs if job.base not in asked] + [
                job for job in self._sacct(missing) if job.base in asked
            ]
        return jobs

    def _scontrol(self, job_id: str) -> list[Row]:
        """The ``scontrol show job`` records of *job_id*; empty once the controller forgot it."""
        result = self._run(["scontrol", "show", "job", job_id], check=False)
        return parse_scontrol_job(result.stdout) if result.returncode == 0 else []

    def _scontrol_settled(self, job_id: str) -> list[Row]:
        """:meth:`_scontrol`, asked again while its one record is ``COMPLETING``.

        Every :data:`EXIT_CODE_POLL` seconds for up to :data:`EXIT_CODE_WAIT`.
        """
        records = self._scontrol(job_id)
        deadline = time.monotonic() + EXIT_CODE_WAIT
        while (
            len(records) == 1 and scontrol_is_completing(records[0]) and time.monotonic() < deadline
        ):
            time.sleep(EXIT_CODE_POLL)
            records = self._scontrol(job_id)
        return records

    def get_statuses(self, job_ids: Sequence[str]) -> dict[str, JobStatus]:
        """Statuses from one ``sacct``, which has pending and running jobs too.

        The status is read from the job's own row (see
        :func:`~hpc_runner.schedulers.slurm.parser.state_to_status`). A job
        just submitted may not be there yet, so ``sacct`` is asked again
        for the ids with no row, every second for up to ten seconds; a job
        with no row after that is UNKNOWN.

        A task id (``123_4``) reports that task's own state: its own row
        once it has started, before that the row the waiting tasks share.
        The base id of an array reports the array as a whole, the
        reduction of its rows
        (:func:`~hpc_runner.core.result.reduce_statuses`).
        """
        jobs = self._sacct_again(job_ids, self._sacct(job_ids))
        return {job_id: sacct_status(jobs, job_id) for job_id in job_ids}

    def get_exit_code(self, job_id: str) -> int | None:
        """What a shell would have reported for the job's command; None when there is none.

        While the controller knows the job the code is read from
        ``scontrol show job``, which has the exit status as the script
        gave it. ``sacct`` is asked when the controller no longer knows
        the job, when its record does not settle the code, and for an
        array (the controller may have forgotten some of its tasks);
        ``sacct`` prints an exit status above 128 less 128.

        A job that has just ended is ``COMPLETING`` to the controller for
        a moment, with no exit code yet: ``scontrol`` is asked again every
        half second, for up to five seconds, until its one record says
        something else or the controller has forgotten the job.

        Never 0 unless the job is COMPLETED, and None for a job that has
        not ended or in which nothing ran. The base id of an array gives
        the first non-zero task code, 0 when every task is COMPLETED.
        """
        return self._exit_code(job_id, self._scontrol_settled(job_id), None)

    def _exit_code(
        self, job_id: str, records: list[Row], jobs: list[SacctJob] | None
    ) -> int | None:
        """:meth:`get_exit_code` from *records*; *jobs* are the ``sacct`` rows if already read."""
        if jobs is None and len(records) == 1 and not scontrol_tasks(records[0]):
            record = records[0]
            code = scontrol_exit_code(record)
            if code is not None or not scontrol_has_ended(record):
                return code
        if jobs is None:
            jobs = self._sacct([job_id])
        selected = select_jobs(jobs, job_id)
        if not selected:
            return None
        if not selected[0].tasks:
            return self._job_exit_code(selected[0], records)
        codes = [self._job_exit_code(job, records) for job in selected]
        failed = next((code for code in codes if code), None)
        if failed is not None:
            return failed
        if all(job.status is JobStatus.COMPLETED for job in selected):
            return 0
        return None

    @staticmethod
    def _job_exit_code(job: SacctJob, records: list[Row]) -> int | None:
        """The exit code of one ``sacct`` job: the controller's when it settles one."""
        if not job.status.is_terminal:
            return None
        for record in records:
            if scontrol_base(record) == job.base and scontrol_tasks(record) == job.tasks:
                code = scontrol_exit_code(record)
                if code is None and scontrol_is_completing(record):
                    # sacct has the job ended; the record already has its code.
                    code = parse_exit_code(record.get("ExitCode", "")) or None
                if code is not None:
                    return code
        return job.exit_code

    # =========================================================================
    # Listings and details
    # =========================================================================

    def list_active_jobs(
        self,
        user: str | None = None,
        status: set[JobStatus] | None = None,
        queue: str | None = None,
    ) -> list[JobInfo]:
        """List live jobs from ``squeue``, one entry per job id.

        Args:
            user: Filter by username. None = all users.
            status: Filter by status set. None = all active statuses.
            queue: Filter by partition. None = all partitions.
        """
        argv = ["squeue", "-h", *(["-u", user] if user else []), "-o", SQUEUE_FORMAT]
        jobs = parse_squeue(self._run(argv).stdout)
        if status is not None:
            jobs = [job for job in jobs if job.status in status]
        if queue is not None:
            jobs = [job for job in jobs if job.queue == queue]
        return jobs

    def list_completed_jobs(
        self,
        user: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        exit_code: int | None = None,
        queue: str | None = None,
        limit: int = 100,
    ) -> list[JobInfo]:
        """List finished jobs from ``sacct``, most recent first.

        ``sacct -S <since>`` gives every job since then, finished or not;
        the ones that have not ended are dropped here. One entry per job
        and per array task, as ``sacct`` has them. The exit code is
        ``sacct``'s (see :attr:`SacctJob.exit_code`).

        Args:
            user: Filter by username. None = all users.
            since: Only jobs since this time.
            until: Only jobs before this time.
            exit_code: Filter by exit code. None = all.
            queue: Filter by partition. None = all.
            limit: Maximum number of results to return.

        Raises:
            AccountingNotAvailable: If sacct is not installed.
        """
        time_format = "%Y-%m-%dT%H:%M:%S"
        argv = ["sacct", "-P", "-n", *(["-u", user] if user else ["-a"])]
        if since:
            argv += ["-S", since.strftime(time_format)]
        if until:
            argv += ["-E", until.strftime(time_format)]
        argv.append(f"--format={SACCT_FORMAT}")
        try:
            output = self._run(argv).stdout
        except SchedulerUnavailableError as exc:
            raise AccountingNotAvailable(
                "sacct not found. Slurm accounting is not available on this system."
            ) from exc
        jobs = [sacct_to_job_info(job) for job in parse_sacct(output) if job.status.is_terminal]
        if exit_code is not None:
            jobs = [job for job in jobs if job.exit_code == exit_code]
        if queue is not None:
            jobs = [job for job in jobs if job.queue == queue]
        jobs.sort(
            key=lambda j: j.end_time or j.start_time or j.submit_time or datetime.min,
            reverse=True,
        )
        return jobs[:limit]

    def has_accounting(self) -> bool:
        """Slurm accounting (slurmdbd) is required."""
        return True

    def get_job_details(self, job_id: str) -> tuple[JobInfo, dict[str, object]]:
        """Details of one job from ``scontrol show job`` and ``sacct``.

        The status and what the job used come from ``sacct``. The
        controller, while it knows the job, adds the output paths, the
        exit status as the script gave it (see :meth:`get_exit_code`),
        and the reason, dependency and command in the extra details.
        ``sacct`` has no output path, so a job the controller has forgotten
        has none.

        Each command is asked once: this never waits. A job the controller
        knows and ``sacct`` has no row for yet (one just submitted) has the
        controller's status. A job that has just ended and is still
        ``COMPLETING`` to the controller has the exit code of that record.

        The base id of an array gives the array as a whole (status and
        exit code reduced over the tasks) and every task's own
        ``JobInfo`` under ``extra["tasks"]``.

        Returns:
            Tuple of (JobInfo, extra_details dict). ``state`` is Slurm's
            own state (``OUT_OF_MEMORY``, ``CANCELLED by 1003``).

        Raises:
            JobNotFoundError: Neither command knows *job_id*.
        """
        records = self._scontrol(job_id)
        jobs = self._sacct([job_id])
        selected = select_jobs(jobs, job_id)
        if not selected:
            if not records:
                raise JobNotFoundError(f"job {job_id} not found")
            return self._details_without_accounting(job_id, records[0])

        if not selected[0].tasks or split_job_id(job_id)[1] is not None:
            job = selected[0]
            task = None
            if job.tasks:
                task = int(job_id.rpartition("_")[2])
            info = self._task_info(job, task, records)
            return info, self._extra(job, self._record_of(job, task, records))

        tasks = [self._task_info(job, task, records) for job in selected for task in job.tasks]
        tasks.sort(key=lambda t: t.array_task_id or 0)
        info = array_job_info(job_id, tasks)
        info.exit_code = self._exit_code(job_id, records, jobs)
        info.working_dir = tasks[0].working_dir
        lead = next((j for j in selected if j.status is info.status), selected[0])
        extra = self._extra(lead, self._record_of(lead, None, records))
        return info, {**extra, "tasks": tasks}

    @staticmethod
    def _record_of(job: SacctJob, task: int | None, records: list[Row]) -> Row | None:
        """The controller's record for *job* (for *task* of the tasks it stands for)."""
        for record in records:
            if scontrol_base(record) != job.base:
                continue
            tasks = scontrol_tasks(record)
            if tasks == job.tasks or (task is not None and task in tasks):
                return record
        return None

    def _task_info(self, job: SacctJob, task: int | None, records: list[Row]) -> JobInfo:
        """The ``JobInfo`` of *job* (of *task* of it), with what the controller adds."""
        info = sacct_to_job_info(job, task)
        info.exit_code = self._job_exit_code(job, records)
        record = self._record_of(job, task, records)
        if record is not None:
            info.stdout_path, info.stderr_path = scontrol_output_paths(record)
        return info

    @staticmethod
    def _extra(job: SacctJob | None, record: Row | None) -> dict[str, object]:
        """The extra details: Slurm's state and account, and what the controller knows."""
        extra: dict[str, object] = {}
        if job is not None:
            extra["state"] = job.state
            if job.row["Account"]:
                extra["project"] = job.row["Account"]
        if record is not None:
            if job is None:
                extra["state"] = record.get("JobState", "")
                if record.get("Account"):
                    extra["project"] = record["Account"]
            for key, name, nothing in (
                ("reason", "Reason", "None"),
                ("dependencies", "Dependency", "(null)"),
                ("command", "Command", ""),
            ):
                value = record.get(name, nothing)
                if value != nothing:
                    extra[key] = value
        return extra

    def _details_without_accounting(
        self, job_id: str, record: Row
    ) -> tuple[JobInfo, dict[str, object]]:
        """Details of a job the controller knows and ``sacct`` has no row for yet."""
        stdout_path, stderr_path = scontrol_output_paths(record)
        cpus = record.get("NumCPUs", "")
        info = JobInfo(
            job_id=job_id,
            name=record.get("JobName", job_id),
            user=record.get("UserId", "unknown").partition("(")[0],
            status=state_to_status(record.get("JobState", "")),
            queue=record.get("Partition") or None,
            cpu=int(cpus) if cpus.isdigit() else None,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            node=record.get("NodeList") or None,
            working_dir=Path(record["WorkDir"]) if record.get("WorkDir") else None,
        )
        return info, self._extra(None, record)
