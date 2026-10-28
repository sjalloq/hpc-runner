"""Local scheduler - executes jobs as subprocesses."""

from __future__ import annotations

import os

# Exempt from the scheduler subprocess seam (schedulers/_subprocess.py):
# this scheduler runs the *job itself* as a child process; it never calls a
# scheduler CLI, so there is no argv to log or scheduler error to convert.
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from hpc_runner.core.config import get_config
from hpc_runner.core.exceptions import AccountingNotAvailable, JobNotFoundError
from hpc_runner.core.job_info import JobInfo
from hpc_runner.core.result import JobResult, JobStatus
from hpc_runner.schedulers.base import BaseScheduler
from hpc_runner.templates import render_template

if TYPE_CHECKING:
    from hpc_runner.core.job import Job
    from hpc_runner.core.job_array import JobArray
    from hpc_runner.core.result import ArrayJobResult


class LocalScheduler(BaseScheduler):
    """Execute jobs locally (for development/testing)."""

    name = "local"

    _job_counter: int = 0

    def __init__(self) -> None:
        """Initialize local scheduler with config-driven settings."""
        config = get_config()
        local_config = config.get_scheduler_config("local")

        self.purge_modules = local_config.get("purge_modules", True)
        self.silent_modules = local_config.get("silent_modules", False)
        self.module_init_script = local_config.get("module_init_script", "")

        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._exit_codes: dict[str, int] = {}
        self._output_paths: dict[str, dict[str, Path]] = {}
        self._script_paths: dict[str, Path] = {}

    def submit(self, job: Job, interactive: bool = False, keep_script: bool = False) -> JobResult:
        """Run job as local subprocess."""
        LocalScheduler._job_counter += 1
        job_id = f"local_{LocalScheduler._job_counter}_{datetime.now().strftime('%Y%m%d%H%M%S')}"

        # Set up environment
        env = os.environ.copy() if job.inherit_env else {}
        if job.env_vars:
            env.update(job.env_vars)

        workdir = Path(job.workdir).resolve() if job.workdir else Path.cwd()

        # Resolve output paths for template-based redirection
        stdout_path: Path | None = None
        stderr_path: Path | None = None

        if job.stdout is not None or job.stderr is not None:
            stdout_file = job.stdout or f"{job.name}.{job_id}.out"
            stdout_path = workdir / stdout_file
            if job.merge_output:
                stderr_path = None
            else:
                stderr_file = job.stderr or f"{job.name}.{job_id}.err"
                stderr_path = workdir / stderr_file

            self._output_paths[job_id] = {
                "stdout": stdout_path,
                "stderr": stderr_path if stderr_path else stdout_path,
            }

        # Generate and write script (template handles output redirection)
        script = self.generate_script(
            job,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )
        script_path = Path(tempfile.gettempdir()) / f".hpc_local_{job_id}.sh"
        script_path.write_text(script)
        script_path.chmod(0o755)

        if interactive:
            result = subprocess.run(
                [str(script_path)],
                cwd=workdir,
                env=env,
            )
            self._exit_codes[job_id] = result.returncode
            if not keep_script:
                script_path.unlink(missing_ok=True)
        else:
            proc = subprocess.Popen(
                [str(script_path)],
                cwd=workdir,
                env=env,
            )
            self._processes[job_id] = proc
            self._script_paths[job_id] = script_path

        return JobResult(job_id=job_id, scheduler=self, job=job)

    def submit_array(self, array: JobArray) -> ArrayJobResult:
        """Array jobs are not supported by the local scheduler."""
        raise NotImplementedError("Array jobs are not supported by the local scheduler")

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

    def generate_script(
        self,
        job: Job,
        array_range: str | None = None,
        *,
        stdout_path: Path | None = None,
        stderr_path: Path | None = None,
    ) -> str:
        """Generate local execution script."""
        return render_template(
            "local/templates/job.sh.j2",
            job=job,
            scheduler=self,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            merge_output=job.merge_output,
        )

    def build_submit_command(self, job: Job) -> list[str]:
        """Build command - for local, just bash."""
        cmd = job.command if isinstance(job.command, str) else " ".join(job.command)
        return ["bash", "-c", cmd]

    def build_interactive_command(self, job: Job) -> list[str]:
        """Build interactive command - for local, just bash."""
        cmd = job.command if isinstance(job.command, str) else " ".join(job.command)
        return ["bash", "-c", cmd]

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
