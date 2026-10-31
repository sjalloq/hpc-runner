"""SGE scheduler implementation."""

from __future__ import annotations

import logging
import os
import time
import uuid
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from hpc_runner.core.dependency import DependencyType
from hpc_runner.core.exceptions import (
    AccountingNotAvailable,
    ConfigError,
    JobNotFoundError,
    NotSupportedError,
    SchedulerCommandError,
    SchedulerUnavailableError,
    SubmissionError,
)
from hpc_runner.core.job_info import JobInfo
from hpc_runner.core.result import (
    HPC_EXIT_DEP_FAILED,
    ArrayJobResult,
    JobResult,
    JobStatus,
    status_from_exit_code,
)
from hpc_runner.schedulers.base import BaseScheduler, refuse_interactive_dependencies
from hpc_runner.schedulers.sge.args import (
    MEMORY_PER,
    SGEArrayArg,
    SGEArrayConcurrencyArg,
    SGECpuArg,
    SGECwdArg,
    SGEErrorArg,
    SGEHoldArg,
    SGEInheritEnvArg,
    SGEJobNameArg,
    SGEMemArg,
    SGEMergeOutputArg,
    SGEOutputArg,
    SGEPriorityArg,
    SGEProjectArg,
    SGEQueueArg,
    SGEShellArg,
    SGETimeArg,
    sanitise_job_name,
)
from hpc_runner.schedulers.sge.parser import (
    parse_qacct_output,
    parse_qacct_records,
    parse_qstat_j_xml,
    parse_qstat_xml,
    parse_qstat_xml_states,
    parse_qsub_output,
    qacct_status,
    qacct_to_job_info,
    state_to_status,
)
from hpc_runner.templates import render_template

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from hpc_runner.core.config import HPCConfig
    from hpc_runner.core.job import Job

# Dependency types the script guard emulates from the parents' exit sentinels.
_GUARDED = frozenset({DependencyType.AFTEROK, DependencyType.AFTERNOTOK})

# Script path shown by preview(); never written or deleted.
_PREVIEW_SCRIPT_PATH = "<script>"


def get_script_dir() -> Path:
    """Get directory for temporary job scripts.

    Uses HPC_SCRIPT_DIR environment variable if set, otherwise
    defaults to ~/.cache/hpc-runner/scripts/.

    Returns:
        Path to script directory (created if needed).
    """
    if env_dir := os.environ.get("HPC_SCRIPT_DIR"):
        script_dir = Path(env_dir)
    else:
        script_dir = Path.home() / ".cache" / "hpc-runner" / "scripts"

    script_dir.mkdir(parents=True, exist_ok=True)
    return script_dir


def get_exit_dir(sweep_ttl_days: float = 7.0) -> Path:
    """Get directory for exit-code sentinel files.

    Each job publishes ``<exit_dir>/<JOB_ID>.rc`` (``<JOB_ID>.<TASK>.rc``
    for an array task) containing its exit code, so that afterok and
    afternotok dependents can test how their parents finished — SGE's
    ``-hold_jid`` is afterany (it releases dependents on parent failure too),
    so we emulate both in the job script.

    Uses ``HPC_EXIT_DIR`` if set, otherwise ``~/.cache/hpc-runner/exit/``
    (sibling of the script dir, on the shared/NFS home as required so the
    dependent can read what the parent wrote).

    The same sentinel is also the status fallback: when ``qacct`` cannot
    report a finished job (accounting unavailable or already rotated),
    ``SGEScheduler.get_status`` / ``get_exit_code`` read the exit code from
    here instead of returning UNKNOWN.

    Stale sentinels older than *sweep_ttl_days* are opportunistically removed.

    Returns:
        Path to exit-sentinel directory (created if needed).
    """
    if env_dir := os.environ.get("HPC_EXIT_DIR"):
        exit_dir = Path(env_dir)
    else:
        exit_dir = Path.home() / ".cache" / "hpc-runner" / "exit"

    exit_dir.mkdir(parents=True, exist_ok=True)

    # Opportunistic cleanup so the directory does not grow without bound.
    cutoff = time.time() - sweep_ttl_days * 86400
    try:
        for rc in exit_dir.glob("*.rc"):
            try:
                if rc.stat().st_mtime < cutoff:
                    rc.unlink()
            except OSError:
                pass
    except OSError:
        pass

    return exit_dir


class SGEScheduler(BaseScheduler):
    """Sun Grid Engine scheduler implementation."""

    name = "sge"

    CONFIG_KEYS = {
        "parallel_environment": str,
        "memory_resource": str,
        "time_resource": str,
        "memory_per": str,
        "module_init_script": str,
        "module_system": str,
        "emulate_afterok": bool,
    }

    HPC_VARS = {
        "cpus": "$NSLOTS",
        "job_id": "$JOB_ID",
        "task_id": "$SGE_TASK_ID",
        "tmpdir": "$TMPDIR",
        "job_name": "$JOB_NAME",
        "host": "$HOSTNAME",
    }
    # SGE expands -o/-e paths itself, where the task id is $TASK_ID.
    HPC_PATH_VARS = {"task_id": "$TASK_ID"}

    # Seconds the dependency guard waits for a parent's sentinel to appear
    # (the NFS directory-entry cache), per sentinel it cannot yet see.
    DEP_WAIT_SECONDS: int = 15

    def __init__(self, config: HPCConfig) -> None:
        """Initialize SGE scheduler with config-driven settings."""
        super().__init__(config)
        sge_config = config.get_scheduler_config("sge")

        # Extract config values (also stored as attributes for testing/introspection)
        self.pe_name = sge_config.get("parallel_environment", "smp")
        self.mem_resource = sge_config.get("memory_resource", "mem_free")
        self.time_resource = sge_config.get("time_resource", "h_rt")
        # How the site's memory resource is consumed. ``mem`` is always per
        # job (D10) and is requested as given by default: a stock SGE
        # ``mem_free`` is a requestable, not a consumable, so SGE does not
        # multiply it by the slots. A site whose resource is a per-slot
        # consumable sets "slot" and the request is divided by cpu.
        self.memory_per: str = sge_config.get("memory_per", "job")
        if self.memory_per not in MEMORY_PER:
            raise ConfigError(
                "[schedulers.sge] memory_per: expected one of "
                f"{', '.join(repr(m) for m in MEMORY_PER)}, got {self.memory_per!r}"
            )

        # afterok/afternotok emulation: SGE -hold_jid is afterany, so each
        # job publishes its exit code to a sentinel file and a guard in the
        # dependent tests its parents' codes before running. With
        # [schedulers.sge] emulate_afterok = false, only afterany is
        # supported (the others raise NotSupportedError).
        self.emulate_afterok: bool = sge_config.get("emulate_afterok", True)
        self.exit_dir = str(get_exit_dir()) if self.emulate_afterok else ""

        # Build the argument renderer registry
        # Maps Job attribute names -> SGE argument renderer instances
        # Note: 'nodes' and 'tasks' are NOT mapped - they're Slurm/MPI concepts.
        # If a job has these set, they'll be silently ignored by SGE.
        self.ARG_RENDERERS = {
            # Basic attributes
            "shell": SGEShellArg(),
            "use_cwd": SGECwdArg(),
            "inherit_env": SGEInheritEnvArg(),
            "name": SGEJobNameArg(),
            "queue": SGEQueueArg(),
            "priority": SGEPriorityArg(),
            "project": SGEProjectArg(),
            "stdout": SGEOutputArg(),
            "stderr": SGEErrorArg(),
            # Resource attributes (config-driven)
            "cpu": SGECpuArg(pe_name=self.pe_name),
            "mem": SGEMemArg(resource_name=self.mem_resource, memory_per=self.memory_per),
            "time": SGETimeArg(resource_name=self.time_resource),
        }

        # Keep references for special-case rendering
        self._mem_arg = cast(SGEMemArg, self.ARG_RENDERERS["mem"])
        self._array_arg = SGEArrayArg()
        self._array_concurrency_arg = SGEArrayConcurrencyArg()
        self._hold_arg = SGEHoldArg()
        self._merge_output_arg = SGEMergeOutputArg()

    # =========================================================================
    # Script Generation
    # =========================================================================

    def job_name(self, job: Job) -> str:
        """*job*'s name as SGE accepts it: the base rules, then :func:`sanitise_job_name`."""
        return sanitise_job_name(super().job_name(job))

    def render_value(self, job: Job, attr_name: str) -> Any:
        """SGE's view of *attr_name*: sanitised name, ``-o``/``-e`` paths, per-slot mem.

        Output paths are rendered absolute (joined onto ``job.workdir``), so
        they do not depend on ``use_cwd`` and match the recorded
        ``stdout_path``. SGE expands them: ``${hpc.job_name}`` becomes
        ``$JOB_NAME``, ``${hpc.task_id}`` ``$TASK_ID`` (see
        :meth:`~hpc_runner.schedulers.base.BaseScheduler.output_pattern`
        for arrays).
        """
        if attr_name == "name":
            return self.job_name(job)
        if attr_name in ("stdout", "stderr"):
            pattern = self.output_pattern(job, "stdout" if attr_name == "stdout" else "stderr")
            if pattern is None:
                return None
            # Always absolute: use_cwd only decides where the command runs.
            path = str(Path(job.workdir) / pattern)
            return self.render_hpc_vars(path, job=job, context="path")
        if attr_name == "mem" and job.mem is not None:
            return self._mem_arg.for_job(job.mem, job.cpu)
        return super().render_value(job, attr_name)

    def preview(self, job: Job, *, verbose: bool = False) -> str:
        """Return the script :meth:`submit` would write for *job*.

        Rendered as with ``keep_script=True`` (no self-delete line), so it
        is byte-for-byte what ``submit(job, keep_script=True)`` writes.

        Raises:
            NotSupportedError: A dependency SGE cannot honour (see
                :meth:`_check_dependencies`).
        """
        self._check_dependencies(job)
        if job.interactive:
            return self._render_interactive(
                job, _PREVIEW_SCRIPT_PATH, keep_script=True, verbose=verbose
            )
        return self._render_batch(job, _PREVIEW_SCRIPT_PATH, keep_script=True, verbose=verbose)

    def _check_dependencies(self, job: Job) -> None:
        """Refuse a dependency SGE cannot honour rather than degrade it.

        Raises:
            NotSupportedError: An interactive job with dependencies (qrsh
                cannot honour them); an ``after`` (start-after) dependency,
                which ``-hold_jid`` cannot express; or an afterok/afternotok
                dependency with ``emulate_afterok`` off.
        """
        refuse_interactive_dependencies(job)
        for dep in job.dependencies:
            if dep.type is DependencyType.AFTER:
                raise NotSupportedError("SGE cannot express 'after' (start-after); use afterany")
            if dep.type in _GUARDED and not self.emulate_afterok:
                raise NotSupportedError(
                    f"SGE cannot express '{dep.type}' without emulation; set "
                    "[schedulers.sge] emulate_afterok = true or use afterany"
                )

    @staticmethod
    def _dependency_guards(job: Job) -> list[dict[str, object]]:
        """One guard per afterok/afternotok parent.

        An array parent (``tasks`` known) lists every task's sentinel; all
        must pass. A parent with ``tasks`` None (e.g. a bare id from the
        CLI, which may be an array) carries its ``job_id`` instead, and the
        script checks ``<id>.rc`` or, failing that, every ``<id>.*.rc``.
        """
        guards: list[dict[str, object]] = []
        for dep in job.dependencies:
            if dep.type not in _GUARDED:
                continue
            if dep.tasks is None:
                guards.append({"type": dep.type.value, "job_id": dep.job_id, "sentinels": None})
            else:
                sentinels = [f"{dep.job_id}.{task}.rc" for task in dep.tasks]
                guards.append({"type": dep.type.value, "job_id": None, "sentinels": sentinels})
        return guards

    def _render_batch(
        self, job: Job, script_path: str, keep_script: bool, verbose: bool = False
    ) -> str:
        """Render the qsub script: every option is a ``#$`` directive."""
        return render_template(
            "sge/templates/batch.sh.j2",
            job=job,
            scheduler=self,
            environment=self.environment_context(job, verbose=verbose),
            directives=self._build_directives(job),
            guards=self._dependency_guards(job),
            dep_failed_exit=HPC_EXIT_DEP_FAILED,
            dep_wait_seconds=self.DEP_WAIT_SECONDS,
            script_path=script_path,
            keep_script=keep_script,
        )

    def _render_interactive(
        self, job: Job, script_path: str, keep_script: bool, verbose: bool = False
    ) -> str:
        """Render the wrapper script qrsh runs (no directives)."""
        return render_template(
            "sge/templates/interactive.sh.j2",
            job=job,
            scheduler=self,
            environment=self.environment_context(job, verbose=verbose),
            script_path=script_path,
            keep_script=keep_script,
        )

    def _build_directives(self, job: Job) -> list[str]:
        """Build complete list of #$ directives for the job.

        Uses the rendering protocol from BaseScheduler, then adds
        special cases that aren't simple attribute mappings.
        """
        directives: list[str] = []

        # 1. Render standard attributes via protocol
        directives.extend(self.render_directives(job))

        # 2. Handle output merging (derived from stderr being None)
        if job.merge_output:
            if d := self._merge_output_arg.to_directive(True):
                directives.append(d)

        # 3. Array job: SGE spells the task range as -t and the concurrency
        #    limit as a separate -tc (there is no %N suffix as in Slurm).
        if job.array is not None:
            if d := self._array_arg.to_directive(job.array.task_range):
                directives.append(d)
            if d := self._array_concurrency_arg.to_directive(job.array.max_concurrent):
                directives.append(d)

        # 4. Dependencies: one -hold_jid for every parent (it is afterany and
        #    guarantees the parents have exited); afterok/afternotok are
        #    guarded in the script. 'after' was refused by _check_dependencies.
        if job.dependencies:
            hold = ",".join(dict.fromkeys(dep.job_id for dep in job.dependencies))
            if d := self._hold_arg.to_directive(hold):
                directives.append(d)

        # 5. Custom resources (ResourceSet)
        for resource in job.resources:
            directives.append(f"#$ -l {resource.name}={resource.value}")

        # 6. Raw passthrough arguments, as one directive line: SGE parses a
        #    directive line like a qsub command line, so "-q batch.q -l gpu=2"
        #    must stay together.
        passthrough = self._passthrough_args(job)
        if passthrough:
            directives.append("#$ " + " ".join(passthrough))

        return directives

    # =========================================================================
    # Job Submission
    # =========================================================================

    def submit(self, job: Job, *, keep_script: bool = False, verbose: bool = False) -> JobResult:
        """Submit a job to SGE: qrsh when ``job.interactive``, else qsub.

        Args:
            job: Job to submit.
            keep_script: If True, don't delete the job script after submission.
                         Useful for debugging.
            verbose: If True, module commands in the script print their
                output rather than only on failure.

        Raises:
            NotSupportedError: A dependency SGE cannot honour; nothing is
                written or submitted.
        """
        self._check_dependencies(job)
        if job.interactive:
            return self._submit_interactive(job, keep_script=keep_script, verbose=verbose)
        return self._submit_batch(job, keep_script=keep_script, verbose=verbose)

    def _submit_batch(
        self, job: Job, keep_script: bool = False, verbose: bool = False
    ) -> JobResult:
        """Submit via ``qsub <script>``; all options are in the script."""
        # Determine script path first (needed for self-deletion in template)
        script_dir = get_script_dir()
        script_name = f"hpc_batch_{uuid.uuid4().hex[:8]}.sh"
        script_path = script_dir / script_name

        # Generate script with cleanup instruction
        script = self._render_batch(job, str(script_path), keep_script=keep_script, verbose=verbose)

        script_path.write_text(script)
        script_path.chmod(0o755)

        if keep_script:
            logger.debug("Script saved: %s", script_path)

        self.make_output_dirs(job)

        try:
            job_id = self._qsub(["qsub", str(script_path)], script=script, cwd=Path(job.workdir))
        finally:
            # Clean up locally after qsub (script is copied to spool)
            # The script inside the job will also self-delete unless keep_script
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

    def _submit_interactive(
        self, job: Job, keep_script: bool = False, verbose: bool = False
    ) -> JobResult:
        """Run via qrsh and wait for it.

        Creates a wrapper script with full environment setup (modules, venv, etc.)
        and executes it via qrsh. The script self-deletes after execution unless
        keep_script is True.

        Note: Script is written to ~/.cache/hpc-runner/scripts/ (shared filesystem)
        rather than /tmp (which is node-local).
        """
        # Generate unique script path in shared script directory
        script_dir = get_script_dir()
        script_name = f"hpc_interactive_{uuid.uuid4().hex[:8]}.sh"
        script_path = script_dir / script_name

        # Generate wrapper script with the actual path (for self-deletion)
        script = self._render_interactive(
            job, str(script_path), keep_script=keep_script, verbose=verbose
        )

        # Write script to shared filesystem
        script_path.write_text(script)
        script_path.chmod(0o755)

        if keep_script:
            logger.debug("Script saved: %s", script_path)

        # Build qrsh command with script path
        cmd = self._build_qrsh_command(job, str(script_path))

        # Run and capture exit code.  qrsh blocks for the job's lifetime, so
        # no timeout, and its output streams straight to the terminal.
        result = self._run(cmd, timeout=None, check=False, capture=False, cwd=Path(job.workdir))

        # Clean up if script still exists and we're not keeping it
        if not keep_script:
            script_path.unlink(missing_ok=True)

        return JobResult(scheduler=self, job_id=None, job=job, exit_code=result.returncode)

    def _build_qrsh_command(self, job: Job, script_path: str) -> list[str]:
        """Build the qrsh argv that runs the wrapper script.

        qrsh has no directives, so options go on its command line; flags
        qrsh rejects are skipped via ``interactive_ok``.
        """
        # -now no: queue until a slot is free instead of failing at once
        # (qrsh's default -now yes).  A later passthrough -now overrides it.
        cmd = ["qrsh", "-now", "no"]
        cmd.extend(self._render_interactive_args(job))
        # Without -V, variables named in env_passthrough are passed one by one.
        for name, value in job.env_passthrough.items():
            cmd.extend(["-v", f"{name}={value}"])
        cmd.extend(self._passthrough_args(job))
        cmd.append(script_path)
        return cmd

    def _qsub(self, argv: list[str], *, script: str, cwd: Path | None) -> str:
        """Run qsub and return the job id.

        Raises:
            SubmissionError: qsub exited non-zero (message is its stderr) or
                its output carried no job id.
        """
        try:
            result = self._run(argv, cwd=cwd)
        except SchedulerCommandError as exc:
            raise SubmissionError(exc.stderr, script=script, argv=exc.argv) from exc
        job_id = parse_qsub_output(result.stdout)
        if job_id is None:
            raise SubmissionError(
                f"could not parse job id from qsub output: {result.stdout.strip()!r}",
                script=script,
                argv=argv,
                stderr=result.stderr,
            )
        return job_id

    # =========================================================================
    # Job Management
    # =========================================================================

    def cancel(self, job_id: str) -> bool:
        """Cancel a job via qdel.

        Returns False when qdel refuses; a missing qdel raises
        ``SchedulerUnavailableError``.
        """
        try:
            self._run(["qdel", job_id])
            return True
        except SchedulerCommandError:
            return False

    def _read_exit_sentinel(self, job_id: str) -> int | None:
        """Read the exit code a job published to its exit sentinel.

        The sentinel is ``<exit_dir>/<job_id>.rc``; an array task id
        (``123.4``) reads its own ``123.4.rc``.

        Returns:
            The exit code, or None if sentinels are disabled
            (``emulate_afterok`` off), the file is missing or unreadable,
            or its content is not an integer.
        """
        if not self.exit_dir:
            return None
        try:
            text = (Path(self.exit_dir) / f"{job_id}.rc").read_text()
        except OSError:
            return None
        try:
            return int(text.strip())
        except ValueError:
            return None

    def _active_states(self) -> dict[str, str]:
        """State code of every active job and array task, from one ``qstat -xml``.

        Some sites' qstat exits non-zero when there are no jobs, so a
        non-zero exit is "nothing active".
        """
        result = self._run(["qstat", "-xml", "-u", "*"], check=False)
        if result.returncode != 0:
            return {}
        return parse_qstat_xml_states(result.stdout)

    def _qacct(self, job_id: str) -> dict[str, str] | None:
        """The parsed qacct record for *job_id* (``-t`` for a task), or None."""
        base, _, task = job_id.partition(".")
        argv = ["qacct", "-j", base] + (["-t", task] if task else [])
        result = self._run(argv, check=False)
        if result.returncode != 0:
            return None
        return parse_qacct_output(result.stdout)

    def get_statuses(self, job_ids: Sequence[str]) -> dict[str, JobStatus]:
        """Statuses from one ``qstat -xml``, then qacct and the exit sentinel.

        Precedence is qstat (active jobs and tasks) > qacct (accounting) >
        exit sentinel. An id not active is looked up one by one (qacct has
        no bulk-by-id form); the sentinel is the fallback when accounting
        has no record. An exit code of ``HPC_EXIT_DEP_FAILED`` (113) is
        SKIPPED; a qacct ``failed`` 37 is TIMEOUT.

        An array task id (``123.4``) reports that task's own state. A base
        id of an array reports the most advanced state of its active tasks.
        """
        active = self._active_states()
        statuses: dict[str, JobStatus] = {}
        for job_id in job_ids:
            state = self._active_state(active, job_id)
            if state is not None:
                statuses[job_id] = state_to_status(state)
                continue
            record = self._qacct(job_id)
            status = qacct_status(record) if record is not None else None
            if status is None:
                rc = self._read_exit_sentinel(job_id)
                status = status_from_exit_code(rc) if rc is not None else JobStatus.UNKNOWN
            statuses[job_id] = status
        return statuses

    def task_job_id(self, base: str, index: int) -> str:
        """SGE names an array task ``<base>.<index>``."""
        return f"{base}.{index}"

    @staticmethod
    def _active_state(active: dict[str, str], job_id: str) -> str | None:
        """*job_id*'s state in *active* (see :meth:`_active_states`), or None.

        A task id with no task entry falls back to its base id's entry (an
        entry without a task list); a base id with only task entries takes
        a running task's state if there is one.
        """
        if job_id in active:
            return active[job_id]
        base, _, task = job_id.partition(".")
        if task:
            return active.get(base)
        task_states = [state for key, state in active.items() if key.startswith(f"{job_id}.")]
        if not task_states:
            return None
        running = [s for s in task_states if state_to_status(s) is JobStatus.RUNNING]
        return (running or task_states)[0]

    def get_exit_code(self, job_id: str) -> int | None:
        """Get exit code from qacct, falling back to the exit sentinel.

        The sentinel is used when accounting is unavailable, e.g. qacct no
        longer returns finished jobs. A guard-skipped job returns 113
        (``HPC_EXIT_DEP_FAILED``).
        """
        record = self._qacct(job_id)
        if record is not None:
            exit_status = record.get("exit_status")
            if exit_status is not None:
                try:
                    return int(exit_status)
                except ValueError:
                    pass
        return self._read_exit_sentinel(job_id)

    # =========================================================================
    # TUI Monitor API
    # =========================================================================

    def list_active_jobs(
        self,
        user: str | None = None,
        status: set[JobStatus] | None = None,
        queue: str | None = None,
    ) -> list[JobInfo]:
        """List active SGE jobs using qstat -xml.

        Args:
            user: Filter by username. None = all users.
            status: Filter by status set. None = all active statuses.
            queue: Filter by queue name. None = all queues.

        Returns:
            List of JobInfo for matching active jobs.
        """
        # Build qstat command
        cmd = ["qstat", "-xml"]
        if user:
            cmd.extend(["-u", user])
        else:
            # Show all users' jobs
            cmd.extend(["-u", "*"])

        # Some sites' qstat exits non-zero when there are no jobs.  A missing
        # qstat raises SchedulerUnavailableError.
        result = self._run(cmd, check=False)
        if result.returncode != 0:
            return []

        # Parse XML output
        parsed_jobs = parse_qstat_xml(result.stdout)

        # Convert to JobInfo and apply filters
        jobs: list[JobInfo] = []
        for job_id, job_data in parsed_jobs.items():
            # Convert state to JobStatus
            state_str = job_data.get("state", "")
            job_status = state_to_status(state_str)

            # Apply status filter
            if status is not None and job_status not in status:
                continue

            # Apply queue filter
            job_queue = job_data.get("queue")
            if queue is not None and job_queue != queue:
                continue

            # Build JobInfo
            job_info = JobInfo(
                job_id=job_id,
                name=job_data.get("name", job_id),
                user=job_data.get("user", "unknown"),
                status=job_status,
                queue=job_queue,
                cpu=job_data.get("slots"),
                node=job_data.get("node"),
            )

            # Add timing info if available
            if "submit_time" in job_data:
                job_info.submit_time = datetime.fromtimestamp(job_data["submit_time"])
            if "start_time" in job_data:
                job_info.start_time = datetime.fromtimestamp(job_data["start_time"])
                # Calculate runtime for running jobs
                if job_info.status == JobStatus.RUNNING:
                    job_info.runtime = datetime.now() - job_info.start_time

            # Array task ID
            if "array_task_id" in job_data:
                try:
                    job_info.array_task_id = int(job_data["array_task_id"])
                except ValueError:
                    pass  # Could be a range like "1-10"

            jobs.append(job_info)

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
        """List completed SGE jobs from qacct.

        Args:
            user: Filter by username. None = all users.
            since: Only jobs ending after this time.
            until: Only jobs ending before this time.
            exit_code: Filter by exit code. None = all.
            queue: Filter by queue name. None = all.
            limit: Maximum number of results to return.

        Returns:
            List of JobInfo sorted by end_time descending.

        Raises:
            AccountingNotAvailable: If qacct is not installed.
        """
        # Build qacct command
        cmd = ["qacct", "-j"]
        if user:
            cmd.extend(["-o", user])

        # qacct -b/-e use [[CC]YYMMDDhhmm[.SS] format
        time_fmt = "%Y%m%d%H%M.%S"
        if since:
            cmd.extend(["-b", since.strftime(time_fmt)])
        if until:
            cmd.extend(["-e", until.strftime(time_fmt)])

        try:
            result = self._run(cmd, check=False)
        except SchedulerUnavailableError as exc:
            raise AccountingNotAvailable(
                "qacct not found. SGE accounting is not available on this system."
            ) from exc
        if result.returncode != 0:
            # No matching records
            return []

        records = parse_qacct_records(result.stdout)
        jobs = [qacct_to_job_info(r) for r in records]

        # Post-filter: qacct doesn't natively filter on exit_code or queue
        if exit_code is not None:
            jobs = [j for j in jobs if j.exit_code == exit_code]
        if queue is not None:
            jobs = [j for j in jobs if j.queue == queue]

        # Sort by end_time descending (most recent first), None last
        jobs.sort(key=lambda j: j.end_time or datetime.min, reverse=True)

        return jobs[:limit]

    def has_accounting(self) -> bool:
        """Check if SGE accounting is available."""
        return True

    def get_job_details(self, job_id: str) -> tuple[JobInfo, dict[str, object]]:
        """Get detailed information for an SGE job using qstat -j -xml.

        Parses the full job details including output paths, resources, etc.

        Returns:
            Tuple of (JobInfo, extra_details dict).
            The extra_details dict contains resources, pe_name, pe_range,
            cwd, script_file, dependencies, project, department.
        """
        cmd = ["qstat", "-j", job_id, "-xml"]
        result = self._run(cmd, check=False)
        output = result.stdout
        if result.returncode != 0:
            output = result.stdout or result.stderr or ""
            if not output:
                raise JobNotFoundError(f"job {job_id} not found")

        # Parse XML output
        job_data = parse_qstat_j_xml(output)
        if not job_data and output:
            raise JobNotFoundError(f"job {job_id} not found")

        # Separate extra details from JobInfo fields
        extra_details: dict[str, object] = {}
        for key in (
            "resources",
            "pe_name",
            "pe_range",
            "cwd",
            "script_file",
            "dependencies",
            "project",
            "department",
            "job_args",
            "command",
        ):
            if key in job_data:
                extra_details[key] = job_data[key]

        # Get basic info from qstat -xml first
        basic_jobs = self.list_active_jobs()
        basic_info = next((j for j in basic_jobs if j.job_id == job_id), None)

        if basic_info:
            # Merge detailed info with basic info
            if job_data.get("stdout_path"):
                basic_info.stdout_path = cast(Path, job_data["stdout_path"])
            if job_data.get("stderr_path"):
                basic_info.stderr_path = cast(Path, job_data["stderr_path"])
            if job_data.get("node"):
                basic_info.node = cast(str, job_data["node"])
            if job_data.get("cwd"):
                basic_info.working_dir = Path(cast(str, job_data["cwd"]))

            # Always use timing from detailed qstat -j output (more reliable)
            if job_data.get("submit_time"):
                ts = cast(float, job_data["submit_time"])
                basic_info.submit_time = datetime.fromtimestamp(ts)
            if job_data.get("start_time"):
                ts = cast(float, job_data["start_time"])
                basic_info.start_time = datetime.fromtimestamp(ts)
                # Calculate runtime if running
                if basic_info.status == JobStatus.RUNNING:
                    basic_info.runtime = datetime.now() - basic_info.start_time

            return basic_info, extra_details
        else:
            # Build from scratch using qstat -j data
            job_info = JobInfo(
                job_id=job_id,
                name=cast(str, job_data.get("name", job_id)),
                user=cast(str, job_data.get("user", "unknown")),
                status=cast(JobStatus, job_data.get("status", JobStatus.UNKNOWN)),
                queue=cast("str | None", job_data.get("queue")),
                stdout_path=cast("Path | None", job_data.get("stdout_path")),
                stderr_path=cast("Path | None", job_data.get("stderr_path")),
                node=cast("str | None", job_data.get("node")),
                working_dir=Path(cast(str, job_data["cwd"])) if job_data.get("cwd") else None,
            )
            # Add timing info
            if job_data.get("submit_time"):
                job_info.submit_time = datetime.fromtimestamp(cast(float, job_data["submit_time"]))
            if job_data.get("start_time"):
                job_info.start_time = datetime.fromtimestamp(cast(float, job_data["start_time"]))
                if job_info.status == JobStatus.RUNNING:
                    job_info.runtime = datetime.now() - job_info.start_time
            return job_info, extra_details
