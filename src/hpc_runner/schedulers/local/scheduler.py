"""Local scheduler - executes jobs as subprocesses."""

from __future__ import annotations

import os
import shlex

# Exempt from the scheduler subprocess seam (schedulers/_subprocess.py):
# this scheduler runs the *job itself* as a child process; it never calls a
# scheduler CLI, so there is no argv to log or scheduler error to convert.
import subprocess
import tempfile
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from hpc_runner.core.exceptions import (
    AccountingNotAvailable,
    JobNotFoundError,
    NotSupportedError,
)
from hpc_runner.core.job_info import JobInfo
from hpc_runner.core.result import JobResult, JobStatus
from hpc_runner.schedulers.base import BaseScheduler
from hpc_runner.templates import render_template

if TYPE_CHECKING:
    from hpc_runner.core.config import HPCConfig
    from hpc_runner.core.job import Job


def _check_supported(job: Job) -> None:
    """Refuse what the local scheduler cannot honour rather than degrade."""
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

    _job_counter: int = 0

    def __init__(self, config: HPCConfig) -> None:
        """Initialize local scheduler with config-driven settings."""
        super().__init__(config)
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._exit_codes: dict[str, int] = {}
        self._output_paths: dict[str, dict[str, Path]] = {}
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
            "job_name": shlex.quote(job.name),
        }

    def submit(self, job: Job, *, keep_script: bool = False, verbose: bool = False) -> JobResult:
        """Run job as a local subprocess; waits for it when ``job.interactive``.

        Raises:
            NotSupportedError: The job is an array job or has dependencies;
                the local scheduler can honour neither.
        """
        _check_supported(job)

        LocalScheduler._job_counter += 1
        job_id = f"local_{LocalScheduler._job_counter}_{datetime.now().strftime('%Y%m%d%H%M%S')}"

        # The job's base environment: everything (-V), or only the named
        # passthrough variables. The script sets env_* itself.
        env = os.environ.copy() if job.inherit_env else dict(job.env_passthrough)

        workdir = Path(job.workdir).resolve() if job.workdir else Path.cwd()

        stdout_path, stderr_path = self._resolve_output_paths(job, job_id, workdir)
        if stdout_path is not None:
            self._output_paths[job_id] = {
                "stdout": stdout_path,
                "stderr": stderr_path if stderr_path else stdout_path,
            }

        # Generate and write script (template handles output redirection)
        script = self._render(job, job_id, stdout_path, stderr_path, verbose=verbose)
        script_path = Path(tempfile.gettempdir()) / f".hpc_local_{job_id}.sh"
        script_path.write_text(script)
        script_path.chmod(0o755)

        if job.interactive:
            result = subprocess.run(
                [str(script_path)],
                cwd=workdir,
                env=env,
            )
            if not keep_script:
                script_path.unlink(missing_ok=True)
            return JobResult(scheduler=self, job_id=None, job=job, exit_code=result.returncode)

        proc = subprocess.Popen(
            [str(script_path)],
            cwd=workdir,
            env=env,
        )
        self._processes[job_id] = proc
        self._script_paths[job_id] = script_path
        return JobResult(scheduler=self, job_id=job_id, job=job)

    def preview(self, job: Job, *, verbose: bool = False) -> str:
        """Return the script :meth:`submit` would write for *job*.

        Output file names contain the job id, which is only assigned at
        submit, so the preview shows ``<job_id>`` in its place.
        """
        _check_supported(job)
        workdir = Path(job.workdir).resolve() if job.workdir else Path.cwd()
        stdout_path, stderr_path = self._resolve_output_paths(job, "<job_id>", workdir)
        return self._render(job, None, stdout_path, stderr_path, verbose=verbose)

    @staticmethod
    def _resolve_output_paths(
        job: Job, job_id: str, workdir: Path
    ) -> tuple[Path | None, Path | None]:
        """Output files for template-based redirection (None = inherit)."""
        if job.stdout is None and job.stderr is None:
            return None, None
        stdout_path = workdir / (job.stdout or f"{job.name}.{job_id}.out")
        if job.merge_output:
            return stdout_path, None
        return stdout_path, workdir / (job.stderr or f"{job.name}.{job_id}.err")

    def cancel(self, job_id: str) -> bool:
        """Cancel a local job."""
        if job_id in self._processes:
            proc = self._processes[job_id]
            proc.terminate()
            proc.wait()
            self._cleanup_process(job_id)
            return True
        return False

    def get_status(self, job_id: str) -> JobStatus:
        """Get job status."""
        if job_id in self._exit_codes:
            if self._exit_codes[job_id] == 0:
                return JobStatus.COMPLETED
            return JobStatus.FAILED

        if job_id not in self._processes:
            return JobStatus.UNKNOWN

        proc = self._processes[job_id]
        poll = proc.poll()

        if poll is None:
            return JobStatus.RUNNING

        # Process completed
        self._exit_codes[job_id] = poll
        self._cleanup_process(job_id)

        return JobStatus.COMPLETED if poll == 0 else JobStatus.FAILED

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

    def get_output_path(self, job_id: str, stream: str) -> Path | None:
        """Get output file path."""
        if job_id in self._output_paths:
            return self._output_paths[job_id].get(stream)
        return None

    def _render(
        self,
        job: Job,
        job_id: str | None,
        stdout_path: Path | None,
        stderr_path: Path | None,
        *,
        verbose: bool = False,
    ) -> str:
        """Render the local execution script."""
        return render_template(
            "local/templates/job.sh.j2",
            job=job,
            scheduler=self,
            environment=self.environment_context(job, job_id=job_id, verbose=verbose),
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            merge_output=job.merge_output,
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

        if job_id in self._processes:
            proc = self._processes[job_id]
            poll = proc.poll()
            status = (
                JobStatus.RUNNING
                if poll is None
                else (JobStatus.COMPLETED if poll == 0 else JobStatus.FAILED)
            )
            job_info = JobInfo(
                job_id=job_id,
                name=job_id,
                user=current_user,
                status=status,
                queue="local",
                exit_code=poll if poll is not None else None,
                stdout_path=self._output_paths.get(job_id, {}).get("stdout"),
                stderr_path=self._output_paths.get(job_id, {}).get("stderr"),
            )
            return job_info, {}

        if job_id in self._exit_codes:
            exit_code = self._exit_codes[job_id]
            job_info = JobInfo(
                job_id=job_id,
                name=job_id,
                user=current_user,
                status=JobStatus.COMPLETED if exit_code == 0 else JobStatus.FAILED,
                queue="local",
                exit_code=exit_code,
                stdout_path=self._output_paths.get(job_id, {}).get("stdout"),
                stderr_path=self._output_paths.get(job_id, {}).get("stderr"),
            )
            return job_info, {}

        raise JobNotFoundError(f"Job {job_id} not found")
