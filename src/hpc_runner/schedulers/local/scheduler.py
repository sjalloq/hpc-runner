"""Local scheduler - executes jobs as subprocesses."""

from __future__ import annotations

import os
import shlex

# Exempt from the scheduler subprocess seam (schedulers/_subprocess.py):
# this scheduler runs the *job itself* as a child process; it never calls a
# scheduler CLI, so there is no argv to log or scheduler error to convert.
import subprocess
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from hpc_runner.core.exceptions import (
    AccountingNotAvailable,
    JobNotFoundError,
    NotSupportedError,
)
from hpc_runner.core.job_info import JobInfo
from hpc_runner.core.result import JobResult, JobStatus, status_from_exit_code
from hpc_runner.schedulers.base import BaseScheduler, Stream, refuse_interactive_dependencies
from hpc_runner.templates import ShellWord, render_template

if TYPE_CHECKING:
    from hpc_runner.core.config import HPCConfig
    from hpc_runner.core.job import Job


def _check_supported(job: Job) -> None:
    """Refuse what the local scheduler cannot honour rather than degrade."""
    refuse_interactive_dependencies(job)
    if job.array is not None:
        raise NotSupportedError("the local scheduler does not support array jobs")
    if job.dependencies:
        raise NotSupportedError("the local scheduler does not support job dependencies")


class LocalScheduler(BaseScheduler):
    """Execute jobs locally (for development/testing)."""

    name = "local"

    CONFIG_KEYS = {
        "module_init_script": str,
        "module_system": str,
    }

    # Shell-side values; cpus, job_id, task_id and job_name are literals
    # known before the script runs (see hpc_vars).
    HPC_VARS = {"tmpdir": "$TMPDIR", "host": "$HOSTNAME"}

    def __init__(self, config: HPCConfig) -> None:
        """Initialize local scheduler with config-driven settings."""
        super().__init__(config)
        # Job ids are local_<n>_<timestamp>_<token>: the counter keeps one
        # instance's ids unique, the per-instance token keeps two instances
        # in the same process (or second) apart.
        self._job_counter = 0
        self._token = uuid.uuid4().hex[:6]
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._exit_codes: dict[str, int] = {}
        self._results: dict[str, JobResult] = {}
        self._script_paths: dict[str, Path] = {}

    def hpc_vars(self, job: Job, *, job_id: str | None = None) -> Mapping[str, str]:
        """``${hpc.*}`` for a local run: literals, except tmpdir and host.

        The id is the one :meth:`submit` allocates before rendering
        (``<job_id>`` in a preview); there is no array task id.
        """
        return {
            **self.HPC_VARS,
            "cpus": shlex.quote(str(job.cpu or 1)),
            "job_id": shlex.quote(job_id or "<job_id>"),
            "task_id": "''",
            "job_name": shlex.quote(self.job_name(job)),
        }

    def submit(self, job: Job, *, keep_script: bool = False, verbose: bool = False) -> JobResult:
        """Run job as a local subprocess; waits for it when ``job.interactive``.

        Raises:
            NotSupportedError: The job is an array job or has dependencies;
                the local scheduler can honour neither.
        """
        _check_supported(job)

        self._job_counter += 1
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        job_id = f"local_{self._job_counter}_{stamp}_{self._token}"

        # The job's base environment: everything (-V), or only the named
        # passthrough variables. The script sets env_* itself.
        env = os.environ.copy() if job.inherit_env else dict(job.env_passthrough)

        workdir = Path(job.workdir)
        self.make_output_dirs(job)

        # Generate and write script (template handles output redirection)
        script = self._render(job, job_id, verbose=verbose)
        script_path = Path(tempfile.gettempdir()) / f".hpc_local_{job_id}.sh"
        script_path.write_text(script)
        script_path.chmod(0o755)

        if job.interactive:
            completed = subprocess.run(
                [str(script_path)],
                cwd=workdir,
                env=env,
            )
            if not keep_script:
                script_path.unlink(missing_ok=True)
            return JobResult(scheduler=self, job_id=None, job=job, exit_code=completed.returncode)

        proc = subprocess.Popen(
            [str(script_path)],
            cwd=workdir,
            env=env,
        )
        self._processes[job_id] = proc
        self._script_paths[job_id] = script_path
        stdout_path, stderr_path = self.output_paths(job, job_id)
        result = JobResult(
            scheduler=self,
            job_id=job_id,
            job=job,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )
        self._results[job_id] = result
        return result

    def preview(self, job: Job, *, verbose: bool = False) -> str:
        """Return the script :meth:`submit` would write for *job*.

        Output file names contain the job id, which is only assigned at
        submit, so the preview shows ``<job_id>`` in its place.
        """
        _check_supported(job)
        return self._render(job, None, verbose=verbose)

    def _redirect(self, job: Job, stream: Stream, job_id: str | None) -> ShellWord | None:
        """The shell word a batch job's *stream* is redirected to, or None.

        None for an interactive run (output goes to the terminal) and for
        a merged stderr. The pattern's ``${hpc.*}`` values are the literals
        of :meth:`hpc_vars` (``$HOSTNAME`` for the host).
        """
        pattern = self.output_pattern(job, stream)
        if job.interactive or pattern is None:
            return None
        return self.shell_word(str(Path(job.workdir) / pattern), job=job, job_id=job_id)

    def cancel(self, job_id: str) -> bool:
        """Cancel a local job."""
        if job_id in self._processes:
            proc = self._processes[job_id]
            proc.terminate()
            proc.wait()
            self._cleanup_process(job_id)
            return True
        return False

    def get_statuses(self, job_ids: Sequence[str]) -> dict[str, JobStatus]:
        """Statuses from this process's in-memory job tables (no subprocess).

        An exit code of ``HPC_EXIT_DEP_FAILED`` (113) is SKIPPED, as on
        SGE; the local scheduler has no dependencies, so only a command that
        exits 113 itself reaches it.
        """
        return {job_id: self._status(job_id) for job_id in job_ids}

    def _status(self, job_id: str) -> JobStatus:
        if job_id in self._exit_codes:
            return status_from_exit_code(self._exit_codes[job_id])

        if job_id not in self._processes:
            return JobStatus.UNKNOWN

        poll = self._processes[job_id].poll()
        if poll is None:
            return JobStatus.RUNNING

        # Process completed
        self._exit_codes[job_id] = poll
        self._cleanup_process(job_id)
        return status_from_exit_code(poll)

    def _cleanup_process(self, job_id: str) -> None:
        """Clean up process resources."""
        self._processes.pop(job_id, None)
        script_path = self._script_paths.pop(job_id, None)
        if script_path:
            script_path.unlink(missing_ok=True)

    def get_exit_code(self, job_id: str) -> int | None:
        """Get exit code."""
        if job_id in self._exit_codes:
            return self._exit_codes[job_id]

        if job_id in self._processes:
            proc = self._processes[job_id]
            poll = proc.poll()
            if poll is not None:
                self._exit_codes[job_id] = poll
                return poll

        return None

    def _render(
        self,
        job: Job,
        job_id: str | None,
        *,
        verbose: bool = False,
    ) -> str:
        """Render the local execution script.

        Local runs ignore ``mem`` and ``time``: there is no scheduler to
        request them from, and limits are not enforced.
        """
        return render_template(
            "local/templates/job.sh.j2",
            job=job,
            scheduler=self,
            environment=self.environment_context(job, job_id=job_id, verbose=verbose),
            stdout=self._redirect(job, "stdout", job_id),
            stderr=self._redirect(job, "stderr", job_id),
        )

    # -------------------------------------------------------------------------
    # TUI Monitor API (stubs for local scheduler)
    # -------------------------------------------------------------------------

    def list_active_jobs(
        self,
        user: str | None = None,
        status: set[JobStatus] | None = None,
        queue: str | None = None,
    ) -> list[JobInfo]:
        """List active local jobs."""
        jobs: list[JobInfo] = []
        current_user = os.environ.get("USER", "unknown")

        for job_id, proc in self._processes.items():
            poll = proc.poll()
            if poll is None:
                job_status = JobStatus.RUNNING
            else:
                continue

            if user is not None and user != current_user:
                continue
            if status is not None and job_status not in status:
                continue

            jobs.append(
                JobInfo(
                    job_id=job_id,
                    name=job_id,
                    user=current_user,
                    status=job_status,
                    queue="local",
                )
            )

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
        """List completed local jobs.

        The local scheduler does not persist job history, so this
        raises AccountingNotAvailable.
        """
        raise AccountingNotAvailable(
            "Local scheduler does not persist job history. "
            "Completed job information is only available during the current session."
        )

    def has_accounting(self) -> bool:
        """Check if job accounting is available."""
        return False

    def get_job_details(self, job_id: str) -> tuple[JobInfo, dict[str, object]]:
        """Get details for a local job."""
        current_user = os.environ.get("USER", "unknown")
        result = self._results.get(job_id)

        if job_id in self._processes:
            proc = self._processes[job_id]
            poll = proc.poll()
            status = JobStatus.RUNNING if poll is None else status_from_exit_code(poll)
            job_info = JobInfo(
                job_id=job_id,
                name=job_id,
                user=current_user,
                status=status,
                queue="local",
                exit_code=poll if poll is not None else None,
                stdout_path=result.stdout_path if result else None,
                stderr_path=result.stderr_path if result else None,
            )
            return job_info, {}

        if job_id in self._exit_codes:
            exit_code = self._exit_codes[job_id]
            job_info = JobInfo(
                job_id=job_id,
                name=job_id,
                user=current_user,
                status=status_from_exit_code(exit_code),
                queue="local",
                exit_code=exit_code,
                stdout_path=result.stdout_path if result else None,
                stderr_path=result.stderr_path if result else None,
            )
            return job_info, {}

        raise JobNotFoundError(f"Job {job_id} not found")
