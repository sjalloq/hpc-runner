"""Local scheduler - executes jobs as subprocesses."""

from __future__ import annotations

import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from hpc_runner.core.exceptions import AccountingNotAvailable, JobNotFoundError
from hpc_runner.core.job_info import JobInfo
from hpc_runner.core.result import ArrayJobResult, JobResult, JobStatus
from hpc_runner.schedulers.base import BaseScheduler
from hpc_runner.templates import render_template

if TYPE_CHECKING:
    from hpc_runner.core.job import Job
    from hpc_runner.core.job_array import JobArray


class LocalScheduler(BaseScheduler):
    """Execute jobs locally (for development/testing)."""

    name = "local"

    _job_counter: int = 0
    _processes: dict[str, subprocess.Popen] = {}  # type: ignore[type-arg]
    _exit_codes: dict[str, int] = {}
    _output_paths: dict[str, dict[str, Path]] = {}

    def submit(self, job: "Job", interactive: bool = False) -> JobResult:
        """Run job as local subprocess."""
        LocalScheduler._job_counter += 1
        job_id = f"local_{LocalScheduler._job_counter}_{datetime.now().strftime('%Y%m%d%H%M%S')}"

        # Set up environment with modules (modules not actually loaded locally)
        env = os.environ.copy() if job.inherit_env else {}

        # Generate and write script
        script = self.generate_script(job)
        script_path = Path(tempfile.gettempdir()) / f".hpc_local_{job_id}.sh"
        script_path.write_text(script)
        script_path.chmod(0o755)

        workdir = Path(job.workdir) if job.workdir else Path.cwd()

        # Determine output paths
        stdout_file = job.stdout or f"{job.name}.{job_id}.out"
        stdout_path = workdir / stdout_file
        if job.merge_output:
            stderr_path = stdout_path  # Merge stderr into stdout
        else:
            stderr_file = job.stderr or f"{job.name}.{job_id}.err"
            stderr_path = workdir / stderr_file

        # Store output paths
        LocalScheduler._output_paths[job_id] = {
            "stdout": stdout_path,
            "stderr": stderr_path,
        }

        if interactive:
            # Blocking execution
            with open(stdout_path, "w") as stdout_f:
                if job.merge_output:
                    result = subprocess.run(
                        [str(script_path)],
                        cwd=workdir,
                        env=env,
                        stdout=stdout_f,
                        stderr=subprocess.STDOUT,
                    )
                else:
                    with open(stderr_path, "w") as stderr_f:
                        result = subprocess.run(
                            [str(script_path)],
                            cwd=workdir,
                            env=env,
                            stdout=stdout_f,
                            stderr=stderr_f,
                        )
            LocalScheduler._exit_codes[job_id] = result.returncode
            script_path.unlink(missing_ok=True)
        else:
            # Background execution
            stdout_f = open(stdout_path, "w")
            if job.merge_output:
                proc = subprocess.Popen(
                    [str(script_path)],
                    cwd=workdir,
                    env=env,
                    stdout=stdout_f,
                    stderr=subprocess.STDOUT,
                )
            else:
                stderr_f = open(stderr_path, "w")
                proc = subprocess.Popen(
                    [str(script_path)],
                    cwd=workdir,
                    env=env,
                    stdout=stdout_f,
                    stderr=stderr_f,
                )
            LocalScheduler._processes[job_id] = proc
            # Store script path for cleanup
            proc._script_path = script_path  # type: ignore[attr-defined]
            proc._stdout_file = stdout_f  # type: ignore[attr-defined]
            if not job.merge_output:
                proc._stderr_file = stderr_f  # type: ignore[attr-defined]

        return JobResult(job_id=job_id, scheduler=self, job=job)

    def submit_array(self, array: "JobArray") -> ArrayJobResult:
        """Simulate array job by submitting multiple jobs."""
        # For local scheduler, we just run one job
        # and return an ArrayJobResult pointing to it
        LocalScheduler._job_counter += 1
        base_job_id = f"local_array_{LocalScheduler._job_counter}"

        # Run jobs sequentially (or could be parallel)
        for idx in array.indices:
            # Set array index environment variable
            os.environ["HPC_ARRAY_TASK_ID"] = str(idx)
            os.environ["SGE_TASK_ID"] = str(idx)  # SGE compat
            os.environ["SLURM_ARRAY_TASK_ID"] = str(idx)  # Slurm compat

            # Create a job ID for this task
            task_job_id = f"{base_job_id}.{idx}"
            self._submit_array_task(array.job, task_job_id, idx)

        return ArrayJobResult(base_job_id=base_job_id, scheduler=self, array=array)

    def _submit_array_task(self, job: "Job", job_id: str, index: int) -> None:
        """Submit a single array task."""
        env = os.environ.copy() if job.inherit_env else {}
        env["HPC_ARRAY_TASK_ID"] = str(index)

        script = self.generate_script(job)
        script_path = Path(tempfile.gettempdir()) / f".hpc_local_{job_id}.sh"
        script_path.write_text(script)
        script_path.chmod(0o755)

        workdir = Path(job.workdir) if job.workdir else Path.cwd()
        stdout_path = workdir / f"{job.name}.{job_id}.out"

        LocalScheduler._output_paths[job_id] = {"stdout": stdout_path, "stderr": stdout_path}

        stdout_f = open(stdout_path, "w")
        proc = subprocess.Popen(
            [str(script_path)],
            cwd=workdir,
            env=env,
            stdout=stdout_f,
            stderr=subprocess.STDOUT,
        )
        LocalScheduler._processes[job_id] = proc
        proc._script_path = script_path  # type: ignore[attr-defined]
        proc._stdout_file = stdout_f  # type: ignore[attr-defined]

    def cancel(self, job_id: str) -> bool:
        """Cancel a local job."""
        if job_id in LocalScheduler._processes:
            proc = LocalScheduler._processes[job_id]
            proc.terminate()
            proc.wait()
            self._cleanup_process(job_id)
            return True
        return False

    def get_status(self, job_id: str) -> JobStatus:
        """Get job status."""
        if job_id in LocalScheduler._exit_codes:
            # Already completed
            return JobStatus.COMPLETED if LocalScheduler._exit_codes[job_id] == 0 else JobStatus.FAILED

        if job_id not in LocalScheduler._processes:
            return JobStatus.UNKNOWN

        proc = LocalScheduler._processes[job_id]
        poll = proc.poll()

        if poll is None:
            return JobStatus.RUNNING

        # Process completed
        LocalScheduler._exit_codes[job_id] = poll
        self._cleanup_process(job_id)

        return JobStatus.COMPLETED if poll == 0 else JobStatus.FAILED

    def _cleanup_process(self, job_id: str) -> None:
        """Clean up process resources."""
        if job_id in LocalScheduler._processes:
            proc = LocalScheduler._processes[job_id]
            # Close file handles
            if hasattr(proc, "_stdout_file"):
                proc._stdout_file.close()  # type: ignore[attr-defined]
            if hasattr(proc, "_stderr_file"):
                proc._stderr_file.close()  # type: ignore[attr-defined]
            # Remove script
            if hasattr(proc, "_script_path"):
                proc._script_path.unlink(missing_ok=True)  # type: ignore[attr-defined]
            del LocalScheduler._processes[job_id]

    def get_exit_code(self, job_id: str) -> int | None:
        """Get exit code."""
        # First check if we have a cached exit code
        if job_id in LocalScheduler._exit_codes:
            return LocalScheduler._exit_codes[job_id]

        # Check if process is done
        if job_id in LocalScheduler._processes:
            proc = LocalScheduler._processes[job_id]
            poll = proc.poll()
            if poll is not None:
                LocalScheduler._exit_codes[job_id] = poll
                return poll

        return None

    def get_output_path(self, job_id: str, stream: str) -> Path | None:
        """Get output file path."""
        if job_id in LocalScheduler._output_paths:
            return LocalScheduler._output_paths[job_id].get(stream)
        return None

    def generate_script(self, job: "Job") -> str:
        """Generate local execution script."""
        return render_template(
            "local/templates/job.sh.j2",
            job=job,
            scheduler=self,
        )

    def build_submit_command(self, job: "Job") -> list[str]:
        """Build command - for local, just bash."""
        return ["bash", "-c", job.command if isinstance(job.command, str) else " ".join(job.command)]

    # -------------------------------------------------------------------------
    # TUI Monitor API (stubs for local scheduler)
    # -------------------------------------------------------------------------

    def list_active_jobs(
        self,
        user: str | None = None,
        status: set[JobStatus] | None = None,
        queue: str | None = None,
    ) -> list[JobInfo]:
        """List active local jobs.

        The local scheduler tracks running processes in memory.
        """
        jobs: list[JobInfo] = []
        current_user = os.environ.get("USER", "unknown")

        for job_id, proc in LocalScheduler._processes.items():
            poll = proc.poll()
            if poll is None:  # Still running
                job_status = JobStatus.RUNNING
            else:
                continue  # Skip completed jobs

            # Apply filters
            if user is not None and user != current_user:
                continue
            if status is not None and job_status not in status:
                continue
            # queue filter doesn't apply to local scheduler

            jobs.append(
                JobInfo(
                    job_id=job_id,
                    name=job_id,  # Local scheduler doesn't track job names
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
        """Check if job accounting is available.

        Local scheduler does not have persistent accounting.
        """
        return False

    def get_job_details(self, job_id: str) -> JobInfo:
        """Get details for a local job."""
        current_user = os.environ.get("USER", "unknown")

        # Check running processes
        if job_id in LocalScheduler._processes:
            proc = LocalScheduler._processes[job_id]
            poll = proc.poll()
            status = JobStatus.RUNNING if poll is None else (
                JobStatus.COMPLETED if poll == 0 else JobStatus.FAILED
            )
            return JobInfo(
                job_id=job_id,
                name=job_id,
                user=current_user,
                status=status,
                queue="local",
                exit_code=poll if poll is not None else None,
                stdout_path=LocalScheduler._output_paths.get(job_id, {}).get("stdout"),
                stderr_path=LocalScheduler._output_paths.get(job_id, {}).get("stderr"),
            )

        # Check completed jobs with cached exit codes
        if job_id in LocalScheduler._exit_codes:
            exit_code = LocalScheduler._exit_codes[job_id]
            return JobInfo(
                job_id=job_id,
                name=job_id,
                user=current_user,
                status=JobStatus.COMPLETED if exit_code == 0 else JobStatus.FAILED,
                queue="local",
                exit_code=exit_code,
                stdout_path=LocalScheduler._output_paths.get(job_id, {}).get("stdout"),
                stderr_path=LocalScheduler._output_paths.get(job_id, {}).get("stderr"),
            )

        raise JobNotFoundError(f"Job {job_id} not found")
