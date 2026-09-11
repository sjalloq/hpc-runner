"""SGE scheduler implementation."""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

from hpc_runner.core.config import get_config
from hpc_runner.core.job_info import JobInfo
from hpc_runner.core.result import ArrayJobResult, JobResult, JobStatus
from hpc_runner.schedulers.base import BaseScheduler
from hpc_runner.schedulers.sge.args import (
    SGEArrayArg,
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
    SGEQueueArg,
    SGEShellArg,
    SGETimeArg,
)
from hpc_runner.schedulers.sge.parser import (
    parse_qacct_output,
    parse_qacct_records,
    parse_qstat_plain,
    parse_qstat_xml,
    parse_qsub_output,
    qacct_to_job_info,
    state_to_status,
)
from hpc_runner.templates import render_template

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from hpc_runner.core.job import Job
    from hpc_runner.core.job_array import JobArray


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
    """Get directory for afterok exit-code sentinel files.

    Each job publishes ``<exit_dir>/<JOB_ID>.rc`` containing its exit code,
    so that afterok dependents can verify their parents succeeded — SGE's
    ``-hold_jid`` is afterany (it releases dependents on parent failure too),
    so we emulate afterok in the job script.

    Uses ``HPC_EXIT_DIR`` if set, otherwise ``~/.cache/hpc-runner/exit/``
    (sibling of the script dir, on the shared/NFS home as required so the
    dependent can read what the parent wrote).

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

    def __init__(self) -> None:
        """Initialize SGE scheduler with config-driven settings."""
        config = get_config()
        sge_config = config.get_scheduler_config("sge")

        # Extract config values (also stored as attributes for testing/introspection)
        self.pe_name = sge_config.get("parallel_environment", "smp")
        self.mem_resource = sge_config.get("memory_resource", "mem_free")
        self.time_resource = sge_config.get("time_resource", "h_rt")

        # Module handling config
        self.purge_modules = sge_config.get("purge_modules", True)
        self.silent_modules = sge_config.get("silent_modules", False)
        self.module_init_script = sge_config.get("module_init_script", "")

        # Environment handling config
        self.expand_makeflags = sge_config.get("expand_makeflags", True)
        self.unset_vars = sge_config.get("unset_vars", [])

        # afterok emulation: SGE -hold_jid is afterany, so each job publishes
        # its exit code to a sentinel file and afterok dependents verify their
        # parents succeeded before running. Disable via [schedulers.sge]
        # enforce_afterok = false to fall back to plain -hold_jid behaviour.
        self.enforce_afterok = sge_config.get("enforce_afterok", True)
        self.exit_dir = str(get_exit_dir()) if self.enforce_afterok else ""

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
            "stdout": SGEOutputArg(),
            "stderr": SGEErrorArg(),
            # Resource attributes (config-driven)
            "cpu": SGECpuArg(pe_name=self.pe_name),
            "mem": SGEMemArg(resource_name=self.mem_resource),
            "time": SGETimeArg(resource_name=self.time_resource),
        }

        # Keep references for special-case rendering
        self._array_arg = SGEArrayArg()
        self._hold_arg = SGEHoldArg()
        self._merge_output_arg = SGEMergeOutputArg()

    # =========================================================================
    # Script Generation
    # =========================================================================

    def generate_script(
        self,
        job: Job,
        array_range: str | None = None,
        keep_script: bool = False,
        script_path: str | None = None,
    ) -> str:
        """Generate qsub script using template.

        Args:
            job: Job to generate script for.
            array_range: Array job range string (e.g., "1-100").
            keep_script: If True, script won't self-delete after execution.
            script_path: Path where script will be written (for self-deletion).
        """
        directives = self._build_directives(job, array_range)
        return render_template(
            "sge/templates/batch.sh.j2",
            job=job,
            scheduler=self,
            directives=directives,
            script_path=script_path,
            keep_script=keep_script,
        )

    def generate_interactive_script(
        self, job: Job, script_path: str, keep_script: bool = False
    ) -> str:
        """Generate wrapper script for interactive jobs."""
        return render_template(
            "sge/templates/interactive.sh.j2",
            job=job,
            scheduler=self,
            script_path=script_path,
            keep_script=keep_script,
        )

    def _build_directives(self, job: Job, array_range: str | None = None) -> list[str]:
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

        # 3. Array job range
        if array_range:
            if d := self._array_arg.to_directive(array_range):
                directives.append(d)

        # 4. Dependencies
        dep_str = self._build_dependency_string(job)
        if dep_str:
            if d := self._hold_arg.to_directive(dep_str):
                directives.append(d)

        # 5. Custom resources (ResourceSet)
        for resource in job.resources:
            directives.append(f"#$ -l {resource.name}={resource.value}")

        # 6. Raw passthrough arguments
        for arg in job.raw_args + job.sge_args:
            if arg.startswith("-"):
                directives.append(f"#$ {arg}")
            else:
                directives.append(f"#$ -{arg}")

        return directives

    def _build_dependency_string(self, job: Job) -> str | None:
        """Build SGE dependency string from job dependencies."""
        # String-based dependency from CLI
        if job.dependency:
            if ":" in job.dependency:
                return job.dependency.split(":", 1)[1]
            return job.dependency

        # Programmatic dependencies from Job.after()
        if job.dependencies:
            return ",".join(dep.job_id for dep in job.dependencies)

        return None

    # =========================================================================
    # Command Building
    # =========================================================================

    def build_submit_command(self, job: Job) -> list[str]:
        """Build qsub command line."""
        cmd = ["qsub"]
        cmd.extend(self.render_args(job))
        cmd.extend(job.raw_args)
        cmd.extend(job.sge_args)
        return cmd

    def build_interactive_command(self, job: Job) -> list[str]:
        """Build qrsh command for interactive jobs.

        Note: qrsh supports a subset of qsub options. Notably:
        - Does NOT support: -S (shell), -o/-e (output), -j (join), -N (name)
        - Does support: -V, -pe, -l, -q, -cwd
        """
        import shlex

        cmd = ["qrsh"]

        # Only include qrsh-compatible options
        QRSH_COMPATIBLE = {"inherit_env", "use_cwd", "cpu", "mem", "time", "queue"}

        for attr_name, value in job.iter_attributes():
            if attr_name not in QRSH_COMPATIBLE:
                continue
            renderer = self.ARG_RENDERERS.get(attr_name)
            if renderer:
                cmd.extend(renderer.to_args(value))

        cmd.extend(job.raw_args)
        cmd.extend(job.sge_args)

        # Add the command - split it back into parts for proper argument handling
        # This preserves quoting: "bash -c 'echo hello'" -> ['bash', '-c', 'echo hello']
        cmd.extend(shlex.split(job.command))

        return cmd

    # =========================================================================
    # Job Submission
    # =========================================================================

    def submit(self, job: Job, interactive: bool = False, keep_script: bool = False) -> JobResult:
        """Submit a job to SGE.

        Args:
            job: Job to submit.
            interactive: If True, run interactively via qrsh.
            keep_script: If True, don't delete the job script after submission.
                         Useful for debugging.
        """
        if interactive:
            return self._submit_interactive(job, keep_script=keep_script)
        return self._submit_batch(job, keep_script=keep_script)

    def _submit_batch(self, job: Job, keep_script: bool = False) -> JobResult:
        """Submit via qsub."""
        # Determine script path first (needed for self-deletion in template)
        script_dir = get_script_dir()
        script_name = f"hpc_batch_{uuid.uuid4().hex[:8]}.sh"
        script_path = script_dir / script_name

        # Generate script with cleanup instruction
        script = self.generate_script(job, keep_script=keep_script, script_path=str(script_path))

        script_path.write_text(script)
        script_path.chmod(0o755)

        if keep_script:
            logger.debug("Script saved: %s", script_path)

        workdir = Path(job.workdir).resolve() if job.workdir else None

        try:
            result = subprocess.run(
                ["qsub", str(script_path)],
                capture_output=True,
                text=True,
                errors="replace",
                check=True,
                cwd=workdir,
            )
            job_id = parse_qsub_output(result.stdout)

            if job_id is None:
                raise RuntimeError(f"Failed to parse job ID: {result.stdout}")

            return JobResult(job_id=job_id, scheduler=self, job=job)
        finally:
            # Clean up locally after qsub (script is copied to spool)
            # The script inside the job will also self-delete unless keep_script
            if not keep_script:
                script_path.unlink(missing_ok=True)

    def _submit_interactive(self, job: Job, keep_script: bool = False) -> JobResult:
        """Submit via qrsh for interactive execution.

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
        script = self.generate_interactive_script(job, str(script_path), keep_script=keep_script)

        # Write script to shared filesystem
        script_path.write_text(script)
        script_path.chmod(0o755)

        if keep_script:
            logger.debug("Script saved: %s", script_path)

        # Build qrsh command with script path
        cmd = self._build_qrsh_command(job, str(script_path))

        workdir = Path(job.workdir).resolve() if job.workdir else None

        # Run and capture exit code
        result = subprocess.run(cmd, check=False, cwd=workdir)

        # Clean up if script still exists and we're not keeping it
        if not keep_script:
            script_path.unlink(missing_ok=True)

        return JobResult(
            job_id="interactive",
            scheduler=self,
            job=job,
            _exit_code=result.returncode,
        )

    def _build_qrsh_command(self, job: Job, script_path: str) -> list[str]:
        """Build qrsh command to run wrapper script."""
        cmd = ["qrsh"]

        # Only include qrsh-compatible options
        QRSH_COMPATIBLE = {"inherit_env", "use_cwd", "cpu", "mem", "time", "queue"}

        for attr_name, value in job.iter_attributes():
            if attr_name not in QRSH_COMPATIBLE:
                continue
            renderer = self.ARG_RENDERERS.get(attr_name)
            if renderer:
                cmd.extend(renderer.to_args(value))

        cmd.extend(job.raw_args)
        cmd.extend(job.sge_args)

        # Execute the wrapper script
        cmd.append(script_path)

        return cmd

    def submit_array(self, array: JobArray) -> ArrayJobResult:
        """Submit array job."""
        script = self.generate_script(array.job, array_range=array.range_str)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False, prefix="hpc_") as f:
            f.write(script)
            script_path = f.name

        workdir = Path(array.job.workdir).resolve() if array.job.workdir else None

        try:
            result = subprocess.run(
                ["qsub", script_path],
                capture_output=True,
                text=True,
                check=True,
                cwd=workdir,
            )
            job_id = parse_qsub_output(result.stdout)

            if job_id is None:
                raise RuntimeError(f"Failed to parse job ID: {result.stdout}")

            return ArrayJobResult(base_job_id=job_id, scheduler=self, array=array)
        finally:
            Path(script_path).unlink(missing_ok=True)

    # =========================================================================
    # Job Management
    # =========================================================================

    def cancel(self, job_id: str) -> bool:
        """Cancel a job via qdel."""
        try:
            subprocess.run(["qdel", job_id], check=True, capture_output=True)
            return True
        except subprocess.CalledProcessError:
            return False

    def get_status(self, job_id: str) -> JobStatus:
        """Get job status via qstat/qacct."""
        # Try qstat first (running/pending jobs)
        try:
            result = subprocess.run(
                ["qstat", "-j", job_id],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                result2 = subprocess.run(["qstat"], capture_output=True, text=True)
                if result2.returncode == 0:
                    jobs = parse_qstat_plain(result2.stdout)
                    base_id = job_id.split(".")[0]
                    if base_id in jobs:
                        return state_to_status(jobs[base_id].get("state", ""))
                return JobStatus.RUNNING
        except subprocess.CalledProcessError:
            pass

        # Check qacct for completed jobs
        try:
            result = subprocess.run(
                ["qacct", "-j", job_id],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                info = parse_qacct_output(result.stdout)
                if info.get("exit_status") == "0":
                    return JobStatus.COMPLETED
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

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                errors="replace",
                check=True,
            )
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
        from hpc_runner.core.exceptions import AccountingNotAvailable

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
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                errors="replace",
                check=True,
            )
        except FileNotFoundError:
            raise AccountingNotAvailable(
                "qacct not found. SGE accounting is not available on this system."
            )
        except subprocess.CalledProcessError:
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
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                errors="replace",
                check=True,
            )
            output = result.stdout
        except subprocess.CalledProcessError as exc:
            output = exc.stdout or exc.stderr or ""
            if not output:
                raise ValueError(f"Job {job_id} not found")
        except FileNotFoundError:
            raise RuntimeError("qstat not found")

        # Parse XML output
        job_data = self._parse_qstat_j_xml(output)
        if not job_data and output:
            raise ValueError(f"Job {job_id} not found")

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

    def _parse_qstat_j_xml(self, xml_output: str) -> dict[str, object]:
        """Parse qstat -j -xml output to extract job details.

        Returns a dict with:
        - Basic: name, user, stdout_path, stderr_path
        - Resources: dict of resource_name -> value
        - PE: pe_name, pe_range
        - Paths: cwd, script_file
        - Dependencies: list of job IDs
        - Other: project, department
        """

        data: dict[str, object] = {}

        root = self._parse_xml_root(xml_output)
        if root is None:
            return data
        self._strip_xml_namespaces(root)

        # Find job info element
        job_info = root.find(".//JB_job_number/..")
        if job_info is None:
            # Try alternative structure
            job_info = root.find(".//djob_info/element")
        if job_info is None:
            return data

        # Extract basic fields
        name_elem = job_info.find(".//JB_job_name")
        if name_elem is not None and name_elem.text:
            data["name"] = name_elem.text

        owner_elem = job_info.find(".//JB_owner")
        if owner_elem is not None and owner_elem.text:
            data["user"] = owner_elem.text

        # Project and department
        project_elem = job_info.find(".//JB_project")
        if project_elem is not None and project_elem.text:
            data["project"] = project_elem.text

        dept_elem = job_info.find(".//JB_department")
        if dept_elem is not None and dept_elem.text:
            data["department"] = dept_elem.text

        # Get cwd for resolving relative paths
        cwd: Path | None = None
        cwd_elem = job_info.find(".//JB_cwd")
        if cwd_elem is not None and cwd_elem.text:
            cwd = Path(cwd_elem.text)
            data["cwd"] = str(cwd)

        # Script file
        script_elem = job_info.find(".//JB_script_file")
        if script_elem is not None and script_elem.text:
            data["script_file"] = script_elem.text

        # Job arguments/command
        job_args: list[str] = []
        for arg_elem in job_info.findall(".//JB_job_args//ST_name"):
            if arg_elem.text:
                job_args.append(arg_elem.text)
        if job_args:
            data["job_args"] = job_args

        # Submission time
        submit_text = job_info.findtext(".//JB_submission_time")
        if submit_text:
            try:
                data["submit_time"] = int(submit_text)
            except ValueError:
                pass

        # Start time (for running jobs) - in JB_ja_tasks/ulong_sublist/JAT_start_time
        task_start_text = job_info.findtext(".//JB_ja_tasks/ulong_sublist/JAT_start_time")
        if task_start_text:
            try:
                data["start_time"] = int(task_start_text)
            except ValueError:
                pass

        # Also check direct JAT_start_time (alternative structure)
        if "start_time" not in data:
            start_text = job_info.findtext(".//JAT_start_time")
            if start_text:
                try:
                    data["start_time"] = int(start_text)
                except ValueError:
                    pass

        # For interactive jobs (qrsh), get command from QRSH_COMMAND env var
        for env_elem in job_info.findall(".//JB_env_list/job_sublist"):
            var_elem = env_elem.find("VA_variable")
            val_elem = env_elem.find("VA_value")
            if var_elem is not None and var_elem.text == "QRSH_COMMAND":
                if val_elem is not None and val_elem.text:
                    data["command"] = self._normalize_qrsh_command(val_elem.text)
                break

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

        # Parse hard resource list
        resources: dict[str, str] = {}
        for qstat_elem in job_info.findall(".//JB_hard_resource_list/qstat_l_requests"):
            res_name_elem = qstat_elem.find("CE_name")
            res_val_elem = qstat_elem.find("CE_stringval")
            if res_name_elem is not None and res_name_elem.text:
                res_name = res_name_elem.text
                res_val = res_val_elem.text if res_val_elem is not None else ""
                resources[res_name] = res_val or ""

        # Also check soft resources
        for qstat_elem in job_info.findall(".//JB_soft_resource_list/qstat_l_requests"):
            res_name_elem = qstat_elem.find("CE_name")
            res_val_elem = qstat_elem.find("CE_stringval")
            if res_name_elem is not None and res_name_elem.text:
                res_name = res_name_elem.text
                res_val = res_val_elem.text if res_val_elem is not None else ""
                resources[f"{res_name} (soft)"] = res_val or ""

        if resources:
            data["resources"] = resources

        # Parallel environment
        pe_elem = job_info.find(".//JB_pe")
        if pe_elem is not None and pe_elem.text:
            data["pe_name"] = pe_elem.text

        # PE range (min-max slots)
        pe_range_min = job_info.find(".//JB_pe_range//RN_min")
        pe_range_max = job_info.find(".//JB_pe_range//RN_max")
        if pe_range_min is not None and pe_range_max is not None:
            min_val = pe_range_min.text or "1"
            max_val = pe_range_max.text or "1"
            if min_val == max_val:
                data["pe_range"] = min_val
            else:
                data["pe_range"] = f"{min_val}-{max_val}"

        # Dependencies (predecessor jobs)
        dependencies: list[str] = []
        for dep_elem in job_info.findall(".//JB_jid_predecessor_list//JRE_job_number"):
            if dep_elem.text:
                dependencies.append(dep_elem.text)
        if dependencies:
            data["dependencies"] = dependencies

        return data

    def _strip_xml_namespaces(self, root: ET.Element) -> None:
        """Strip namespaces so ElementTree can match tag names directly."""

        for elem in root.iter():
            if isinstance(elem.tag, str) and "}" in elem.tag:
                elem.tag = elem.tag.split("}", 1)[1]

    def _normalize_qrsh_command(self, value: str) -> str:
        """Normalize QRSH_COMMAND by replacing non-ASCII separators with spaces."""
        cleaned = "".join(ch if 32 <= ord(ch) < 127 else " " for ch in value)
        return " ".join(cleaned.split())

    def _parse_xml_root(self, xml_output: str) -> ET.Element | None:
        """Parse XML output, tolerating leading/trailing non-XML noise."""

        try:
            return ET.fromstring(xml_output)
        except ET.ParseError:
            pass
        start = xml_output.find("<")
        end = xml_output.rfind(">")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            return ET.fromstring(xml_output[start : end + 1])
        except ET.ParseError:
            return None
