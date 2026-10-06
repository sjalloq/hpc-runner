"""Tests for local scheduler."""

import shutil
from pathlib import Path

import pytest

from hpc_runner.core.job import Job
from hpc_runner.core.result import JobStatus
from hpc_runner.schedulers.local import LocalScheduler

MODULEFILES_DIR = str(Path(__file__).parent.parent / "modulefiles")

has_modules = (
    shutil.which("modulecmd") is not None
    or Path("/usr/share/Modules/init/bash").exists()
    or Path("/etc/profile.d/modules.sh").exists()
)
requires_modules = pytest.mark.skipif(not has_modules, reason="Environment Modules not installed")


class TestLocalScheduler:
    """Tests for LocalScheduler."""

    def test_scheduler_name(self):
        """Test scheduler name."""
        scheduler = LocalScheduler()
        assert scheduler.name == "local"

    def test_submit_job(self, temp_dir):
        """Test submitting a job."""
        scheduler = LocalScheduler()
        job = Job(command="echo hello", workdir=temp_dir)

        result = scheduler.submit(job)

        assert result.job_id.startswith("local_")
        assert result.scheduler is scheduler
        assert result.job is job

    def test_submit_interactive_job(self, temp_dir):
        """Test submitting an interactive (blocking) job."""
        scheduler = LocalScheduler()
        job = Job(command="echo hello", workdir=temp_dir)

        result = scheduler.submit(job, interactive=True)

        assert result.returncode == 0

    def test_submit_failing_job(self, temp_dir):
        """Test submitting a failing job."""
        scheduler = LocalScheduler()
        job = Job(command="exit 1", workdir=temp_dir)

        result = scheduler.submit(job, interactive=True)

        assert result.returncode == 1

    def test_job_status_running(self, temp_dir):
        """Test getting status of running job."""
        scheduler = LocalScheduler()
        job = Job(command="sleep 10", workdir=temp_dir)

        result = scheduler.submit(job, interactive=False)
        status = scheduler.get_status(result.job_id)

        assert status == JobStatus.RUNNING

        # Clean up
        scheduler.cancel(result.job_id)

    def test_job_status_completed(self, temp_dir):
        """Test getting status of completed job."""
        scheduler = LocalScheduler()
        job = Job(command="echo done", workdir=temp_dir)

        result = scheduler.submit(job, interactive=True)
        status = scheduler.get_status(result.job_id)

        assert status == JobStatus.COMPLETED

    def test_cancel_job(self, temp_dir):
        """Test cancelling a job."""
        scheduler = LocalScheduler()
        job = Job(command="sleep 60", workdir=temp_dir)

        result = scheduler.submit(job, interactive=False)
        success = scheduler.cancel(result.job_id)

        assert success is True

    def test_generate_script(self, temp_dir):
        """Test script generation."""
        scheduler = LocalScheduler()
        job = Job(
            command="echo hello",
            modules=["python/3.11"],
            workdir=temp_dir,
        )

        script = scheduler.generate_script(job)

        assert "#!/bin/bash" in script
        assert "echo hello" in script
        assert "set -e" in script

    def test_output_path(self, temp_dir):
        """Test output file creation."""
        scheduler = LocalScheduler()
        job = Job(command="echo hello", workdir=temp_dir, stdout="out.log")

        result = scheduler.submit(job, interactive=True)

        stdout_path = scheduler.get_output_path(result.job_id, "stdout")
        assert stdout_path is not None
        assert stdout_path.exists()
        assert "hello" in stdout_path.read_text()

    def test_merged_output(self, temp_dir):
        """Test merged stdout/stderr."""
        scheduler = LocalScheduler()
        job = Job(
            command="echo stdout; echo stderr >&2",
            workdir=temp_dir,
            stdout="merged.log",
        )

        result = scheduler.submit(job, interactive=True)

        stdout_path = scheduler.get_output_path(result.job_id, "stdout")
        content = stdout_path.read_text()

        assert "stdout" in content
        assert "stderr" in content  # Merged

    def test_passthrough_mode(self, temp_dir):
        """Test passthrough mode: no stdout/stderr means no output files."""
        scheduler = LocalScheduler()
        job = Job(command="echo passthrough", workdir=temp_dir)

        result = scheduler.submit(job, interactive=True)

        assert result.returncode == 0
        # No output paths should be stored in passthrough mode
        stdout_path = scheduler.get_output_path(result.job_id, "stdout")
        assert stdout_path is None

    def test_generate_script_with_modules(self, temp_dir):
        """Test that generated script includes module commands."""
        scheduler = LocalScheduler()
        job = Job(
            command="echo hello",
            modules=["python/3.11", "gcc/12.2"],
            modules_path=["/opt/modulefiles"],
            workdir=temp_dir,
        )

        script = scheduler.generate_script(job)

        assert "module use /opt/modulefiles" in script
        assert "module load python/3.11" in script
        assert "module load gcc/12.2" in script

    def test_generate_script_modules_path_append(self, temp_dir):
        """modules_path_append renders as module use --append, after modules_path."""
        scheduler = LocalScheduler()
        job = Job(
            command="echo hello",
            modules=["python/3.11"],
            modules_path=["/opt/modulefiles"],
            modules_path_append=["/home/me/modulefiles"],
            workdir=temp_dir,
        )

        script = scheduler.generate_script(job)

        assert "module use /opt/modulefiles" in script
        assert "module use --append /home/me/modulefiles" in script
        assert script.index("module use /opt/modulefiles") < script.index("module use --append")
        assert script.index("module use --append") < script.index("module load python/3.11")

    @requires_modules
    def test_module_loading(self, temp_dir):
        """Test that a module is actually loaded and sets env vars."""
        scheduler = LocalScheduler()
        job = Job(
            command="echo $DUMMY_TOOL_LOADED",
            modules=["dummy_tool/1.0"],
            modules_path=[MODULEFILES_DIR],
            workdir=temp_dir,
            stdout="out.log",
        )

        result = scheduler.submit(job, interactive=True)

        assert result.returncode == 0
        stdout_path = scheduler.get_output_path(result.job_id, "stdout")
        content = stdout_path.read_text().strip()
        assert content == "true"

    @requires_modules
    def test_multiple_modules(self, temp_dir):
        """Test loading multiple modules sets all env vars."""
        scheduler = LocalScheduler()
        job = Job(
            command="echo $DUMMY_TOOL_LOADED $DUMMY_LIB_LOADED",
            modules=["dummy_tool/1.0", "dummy_lib/2.5"],
            modules_path=[MODULEFILES_DIR],
            workdir=temp_dir,
            stdout="out.log",
        )

        result = scheduler.submit(job, interactive=True)

        assert result.returncode == 0
        stdout_path = scheduler.get_output_path(result.job_id, "stdout")
        content = stdout_path.read_text().strip()
        assert content == "true true"

    @requires_modules
    def test_module_sets_env_var(self, temp_dir):
        """Test that module sets specific version string in environment."""
        scheduler = LocalScheduler()
        job = Job(
            command="echo $DUMMY_TOOL_VERSION",
            modules=["dummy_tool/1.0"],
            modules_path=[MODULEFILES_DIR],
            workdir=temp_dir,
            stdout="out.log",
        )

        result = scheduler.submit(job, interactive=True)

        assert result.returncode == 0
        stdout_path = scheduler.get_output_path(result.job_id, "stdout")
        content = stdout_path.read_text().strip()
        assert content == "1.0"
