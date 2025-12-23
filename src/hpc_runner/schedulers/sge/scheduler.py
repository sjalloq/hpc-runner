"""SGE scheduler implementation."""

from __future__ import annotations

import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from hpc_runner.core.config import get_config
from hpc_runner.core.exceptions import AccountingNotAvailable, JobNotFoundError
from hpc_runner.core.job_info import JobInfo
from hpc_runner.core.result import ArrayJobResult, JobResult, JobStatus
from hpc_runner.schedulers.base import BaseScheduler
from hpc_runner.schedulers.sge.args import (
    SGECpuArg,
    SGECwdArg,
    SGEErrorArg,
    SGEJobNameArg,
    SGEJoinOutputArg,
    SGEMemArg,
    SGEOutputArg,
    SGEQueueArg,
    SGETimeArg,
)
from hpc_runner.schedulers.sge.parser import (
    parse_qacct_output,
    parse_qstat_plain,
    parse_qstat_xml,
    parse_qsub_output,
    state_to_status,
)
from hpc_runner.templates import render_template

if TYPE_CHECKING:
    from hpc_runner.core.job import Job
    from hpc_runner.core.job_array import JobArray


class SGEScheduler(BaseScheduler):
    """Sun Grid Engine scheduler implementation."""

    name = "sge"

    # Descriptor-based argument definitions
    cpu_arg = SGECpuArg()
    mem_arg = SGEMemArg()
    time_arg = SGETimeArg()
    queue_arg = SGEQueueArg()
    job_name_arg = SGEJobNameArg()
    stdout_arg = SGEOutputArg()
    stderr_arg = SGEErrorArg()
    join_output_arg = SGEJoinOutputArg()
    cwd_arg = SGECwdArg()

    def __init__(self) -> None:
        # Load scheduler-specific config
        config = get_config()
        sge_config = config.get_scheduler_config("sge")

        self.pe_name = sge_config.get("parallel_environment", "smp")
        self.mem_resource = sge_config.get("memory_resource", "mem_free")
        self.time_resource = sge_config.get("time_resource", "h_rt")
        self.merge_output_default = sge_config.get("merge_output", True)

    def submit(self, job: "Job", interactive: bool = False) -> JobResult:
        """Submit a job to SGE."""
        if interactive:
            return self._submit_interactive(job)
        return self._submit_batch(job)

    def _submit_batch(self, job: "Job") -> JobResult:
        """Submit via qsub."""
        script = self.generate_script(job)

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".sh", delete=False, prefix="hpc_"
        ) as f:
            f.write(script)
            script_path = f.name

        try:
            cmd = ["qsub", script_path]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            job_id = parse_qsub_output(result.stdout)

            if job_id is None:
                raise RuntimeError(f"Failed to parse job ID from qsub output: {result.stdout}")

            return JobResult(job_id=job_id, scheduler=self, job=job)
        finally:
            Path(script_path).unlink(missing_ok=True)

    def _submit_interactive(self, job: "Job") -> JobResult:
        """Submit via qrsh for interactive execution."""
        cmd = self.build_interactive_command(job)
        result = subprocess.run(cmd, check=False)
        # For interactive jobs, we don't have a real job ID
        return JobResult(job_id="interactive", scheduler=self, job=job)

    def submit_array(self, array: "JobArray") -> ArrayJobResult:
        """Submit array job."""
        job = array.job
        script = self.generate_script(job, array_range=array.range_str)

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".sh", delete=False, prefix="hpc_"
        ) as f:
            f.write(script)
            script_path = f.name

        try:
            cmd = ["qsub", script_path]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            job_id = parse_qsub_output(result.stdout)

            if job_id is None:
                raise RuntimeError(f"Failed to parse job ID from qsub output: {result.stdout}")

            return ArrayJobResult(base_job_id=job_id, scheduler=self, array=array)
        finally:
            Path(script_path).unlink(missing_ok=True)

    def cancel(self, job_id: str) -> bool:
        """Cancel a job via qdel."""
        try:
            subprocess.run(["qdel", job_id], check=True, capture_output=True)
            return True
        except subprocess.CalledProcessError:
            return False

    def get_status(self, job_id: str) -> JobStatus:
        """Get job status via qstat."""
        # Try qstat first (running/pending jobs)
        try:
            result = subprocess.run(
                ["qstat", "-j", job_id],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                # Job exists, check state from regular qstat
                result2 = subprocess.run(
                    ["qstat"],
                    capture_output=True,
                    text=True,
                )
                if result2.returncode == 0:
                    jobs = parse_qstat_plain(result2.stdout)
                    # Handle array job task IDs (e.g., 12345.1)
                    base_id = job_id.split(".")[0]
                    if base_id in jobs:
                        state = jobs[base_id].get("state", "")
                        return state_to_status(state)
                    # Check if full ID matches
                    if job_id in jobs:
                        state = jobs[job_id].get("state", "")
                        return state_to_status(state)

                # Job exists but not in qstat output - likely running
                return JobStatus.RUNNING
        except subprocess.CalledProcessError:
            pass

        # Job not in qstat, check qacct for completed jobs
        try:
            result = subprocess.run(
                ["qacct", "-j", job_id],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                info = parse_qacct_output(result.stdout)
                exit_status = info.get("exit_status", "")
                if exit_status == "0":
                    return JobStatus.COMPLETED
                else:
                    return JobStatus.FAILED
        except subprocess.CalledProcessError:
            pass

        return JobStatus.UNKNOWN

    def get_exit_code(self, job_id: str) -> int | None:
        """Get exit code from qacct."""
        try:
            result = subprocess.run(
                ["qacct", "-j", job_id],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                info = parse_qacct_output(result.stdout)
                exit_status = info.get("exit_status")
                if exit_status is not None:
                    return int(exit_status)
        except (subprocess.CalledProcessError, ValueError):
            pass
        return None

    def get_output_path(self, job_id: str, stream: str) -> Path | None:
        """Determine output path.

        SGE uses patterns that need to be resolved.
        """
        # This is tricky with SGE as paths can use $JOB_ID, etc.
        # For now, return None and let user check
        return None

    def generate_script(self, job: "Job", array_range: str | None = None) -> str:
        """Generate qsub script using template."""
        directives = self._build_directives(job, array_range)
        return render_template(
            "sge/templates/job.sh.j2",
            job=job,
            scheduler=self,
            directives=directives,
        )

    def _build_directives(self, job: "Job", array_range: str | None = None) -> list[str]:
        """Build #$ directives."""
        directives: list[str] = []

        # Shell
        directives.append("#$ -S /bin/bash")

        # Use current working directory
        if job.workdir is None:
            directives.append("#$ -cwd")

        # Job name
        if job.name:
            directives.append(f"#$ -N {job.name}")

        # CPU/slots via parallel environment
        if job.cpu:
            directives.append(f"#$ -pe {self.pe_name} {job.cpu}")

        # Memory
        if job.mem:
            directives.append(f"#$ -l {self.mem_resource}={job.mem}")

        # Time
        if job.time:
            directives.append(f"#$ -l {self.time_resource}={job.time}")

        # Queue
        if job.queue:
            directives.append(f"#$ -q {job.queue}")

        # Output handling - merge by default
        if job.merge_output:
            directives.append("#$ -j y")
            if job.stdout:
                directives.append(f"#$ -o {job.stdout}")
        else:
            if job.stdout:
                directives.append(f"#$ -o {job.stdout}")
            if job.stderr:
                directives.append(f"#$ -e {job.stderr}")

        # Array job
        if array_range:
            directives.append(f"#$ -t {array_range}")

        # Resources (GRES-style)
        for resource in job.resources:
            directives.append(f"#$ -l {resource.name}={resource.value}")

        # Dependencies (string-based from CLI)
        if job.dependency:
            # Parse dependency spec (e.g., "afterok:12345" or just "12345")
            if ":" in job.dependency:
                # Format: "type:job_id,job_id,..."
                dep_spec = job.dependency.split(":", 1)[1]
            else:
                dep_spec = job.dependency
            directives.append(f"#$ -hold_jid {dep_spec}")
        # Dependencies (programmatic from Job.after())
        elif job.dependencies:
            dep_ids = ",".join(dep.job_id for dep in job.dependencies)
            # SGE uses -hold_jid for dependencies
            directives.append(f"#$ -hold_jid {dep_ids}")

        # Raw args
        for arg in job.raw_args + job.sge_args:
            if arg.startswith("-"):
                directives.append(f"#$ {arg}")
            else:
                directives.append(f"#$ -{arg}")

        return directives

    def build_submit_command(self, job: "Job") -> list[str]:
        """Build qsub command line."""
        cmd = ["qsub"]

        if job.name:
            cmd.extend(["-N", job.name])
        if job.cpu:
            cmd.extend(["-pe", self.pe_name, str(job.cpu)])
        if job.mem:
            cmd.extend(["-l", f"{self.mem_resource}={job.mem}"])
        if job.time:
            cmd.extend(["-l", f"{self.time_resource}={job.time}"])
        if job.queue:
            cmd.extend(["-q", job.queue])

        cmd.extend(job.raw_args)
        cmd.extend(job.sge_args)

        return cmd

    def build_interactive_command(self, job: "Job") -> list[str]:
        """Build qrsh command for interactive jobs."""
        cmd = ["qrsh"]

        if job.cpu:
            cmd.extend(["-pe", self.pe_name, str(job.cpu)])
        if job.mem:
            cmd.extend(["-l", f"{self.mem_resource}={job.mem}"])
        if job.time:
            cmd.extend(["-l", f"{self.time_resource}={job.time}"])
        if job.queue:
            cmd.extend(["-q", job.queue])

        cmd.extend(job.raw_args)
        cmd.extend(job.sge_args)

        # Add the command
        if isinstance(job.command, str):
            cmd.append(job.command)
        else:
            cmd.extend(job.command)

        return cmd

    # -------------------------------------------------------------------------
    # TUI Monitor API (stubs - to be implemented in Stage 5 and Stage 14)
    # -------------------------------------------------------------------------

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

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError:
            # qstat failed - likely no jobs or scheduler not available
            return []
        except FileNotFoundError:
            # qstat not found
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
                    from datetime import timedelta
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

        TODO: Implement in Stage 14 using qacct.
        """
        raise NotImplementedError("SGE list_completed_jobs() not yet implemented")

    def has_accounting(self) -> bool:
        """Check if SGE accounting is available.

        TODO: Implement in Stage 14 by testing qacct availability.
        """
        # Stub: assume accounting is available (will be properly checked later)
        return True

    def get_job_details(self, job_id: str) -> JobInfo:
        """Get detailed information for an SGE job using qstat -j -xml.

        Parses the full job details including output paths.
        """
        cmd = ["qstat", "-j", job_id, "-xml"]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError:
            raise ValueError(f"Job {job_id} not found")
        except FileNotFoundError:
            raise RuntimeError("qstat not found")

        # Parse XML output
        job_data = self._parse_qstat_j_xml(result.stdout)

        # Get basic info from qstat -xml first
        basic_jobs = self.list_active_jobs()
        basic_info = next((j for j in basic_jobs if j.job_id == job_id), None)

        if basic_info:
            # Merge detailed info with basic info
            if job_data.get("stdout_path"):
                basic_info.stdout_path = job_data["stdout_path"]
            if job_data.get("stderr_path"):
                basic_info.stderr_path = job_data["stderr_path"]
            if job_data.get("node"):
                basic_info.node = job_data["node"]
            return basic_info
        else:
            # Build from scratch using qstat -j data
            return JobInfo(
                job_id=job_id,
                name=job_data.get("name", job_id),
                user=job_data.get("user", "unknown"),
                status=job_data.get("status", JobStatus.UNKNOWN),
                queue=job_data.get("queue"),
                stdout_path=job_data.get("stdout_path"),
                stderr_path=job_data.get("stderr_path"),
                node=job_data.get("node"),
            )

    def _parse_qstat_j_xml(self, xml_output: str) -> dict[str, object]:
        """Parse qstat -j -xml output to extract job details."""
        import xml.etree.ElementTree as ET

        data: dict[str, object] = {}

        try:
            root = ET.fromstring(xml_output)
        except ET.ParseError:
            return data

        # Find job info element
        job_info = root.find(".//JB_job_number/..")
        if job_info is None:
            # Try alternative structure
            job_info = root.find(".//djob_info/element")
        if job_info is None:
            return data

        # Extract fields
        name_elem = job_info.find(".//JB_job_name")
        if name_elem is not None and name_elem.text:
            data["name"] = name_elem.text

        owner_elem = job_info.find(".//JB_owner")
        if owner_elem is not None and owner_elem.text:
            data["user"] = owner_elem.text

        # Get cwd for resolving relative paths
        cwd: Path | None = None
        cwd_elem = job_info.find(".//JB_cwd")
        if cwd_elem is not None and cwd_elem.text:
            cwd = Path(cwd_elem.text)

        # stdout path - look for PN_path in stdout_path_list
        stdout_path_elem = job_info.find(".//JB_stdout_path_list//PN_path")
        if stdout_path_elem is not None and stdout_path_elem.text:
            stdout_path = Path(stdout_path_elem.text)
            # Resolve relative paths against cwd
            if not stdout_path.is_absolute() and cwd:
                stdout_path = cwd / stdout_path
            data["stdout_path"] = stdout_path

        # stderr path
        stderr_path_elem = job_info.find(".//JB_stderr_path_list//PN_path")
        if stderr_path_elem is not None and stderr_path_elem.text:
            stderr_path = Path(stderr_path_elem.text)
            if not stderr_path.is_absolute() and cwd:
                stderr_path = cwd / stderr_path
            data["stderr_path"] = stderr_path

        # Check merge flag
        merge_elem = job_info.find(".//JB_merge_stderr")
        if merge_elem is not None and merge_elem.text:
            if merge_elem.text.lower() in ("true", "1", "y"):
                data["merge"] = True

        # If merge is enabled and we have stdout but no stderr, use stdout for both
        if data.get("merge") and data.get("stdout_path") and not data.get("stderr_path"):
            data["stderr_path"] = data["stdout_path"]

        return data
