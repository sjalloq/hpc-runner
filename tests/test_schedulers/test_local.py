"""Tests for local scheduler."""

from pathlib import Path

import pytest

from hpc_runner.core.config import HPCConfig
from hpc_runner.core.dependency import Dependency
from hpc_runner.core.exceptions import NotSupportedError
from hpc_runner.core.result import JobStatus
from hpc_runner.schedulers.local import LocalScheduler
from tests.fakes import make_job

MODULEFILES_DIR = str(Path(__file__).parent.parent / "modulefiles")

# The module init files the job script sources (templates/_environment.sh.j2).
has_modules = any(
    Path(init).exists()
    for init in (
        "/etc/profile.d/modules.sh",
        "/usr/share/Modules/init/bash",
        "/etc/modules/init/bash",
    )
)
requires_modules = pytest.mark.skipif(not has_modules, reason="Environment Modules not installed")


class TestLocalScheduler:
    """Tests for LocalScheduler."""

    def test_scheduler_name(self):
        """Test scheduler name."""
        scheduler = LocalScheduler(HPCConfig())
        assert scheduler.name == "local"

    def test_submit_job(self, temp_dir):
        """Test submitting a job."""
        scheduler = LocalScheduler(HPCConfig())
        job = make_job("echo hello", workdir=str(temp_dir))

        result = scheduler.submit(job)

        assert result.job_id.startswith("local_")
        assert result.scheduler is scheduler
        assert result.job is job

    def test_submit_interactive_job(self, temp_dir):
        """Test submitting an interactive (blocking) job."""
        scheduler = LocalScheduler(HPCConfig())
        job = make_job("echo hello", workdir=str(temp_dir), interactive=True)

        result = scheduler.submit(job)

        assert result.job_id is None
        assert result.exit_code == 0
        assert result.returncode == 0

    def test_submit_failing_job(self, temp_dir):
        """Test submitting a failing job."""
        scheduler = LocalScheduler(HPCConfig())
        job = make_job("exit 1", workdir=str(temp_dir), interactive=True)

        result = scheduler.submit(job)

        assert result.returncode == 1

    def test_job_status_running(self, temp_dir):
        """Test getting status of running job."""
        scheduler = LocalScheduler(HPCConfig())
        job = make_job("sleep 10", workdir=str(temp_dir))

        result = scheduler.submit(job)
        status = scheduler.get_status(result.job_id)

        assert status == JobStatus.RUNNING

        # Clean up
        scheduler.cancel(result.job_id)

    def test_job_status_completed(self, temp_dir):
        """Test getting status of completed job."""
        scheduler = LocalScheduler(HPCConfig())
        job = make_job("echo done", workdir=str(temp_dir))

        result = scheduler.submit(job)
        result.wait(poll_interval=0.05)
        status = scheduler.get_status(result.job_id)

        assert status == JobStatus.COMPLETED

    def test_cancel_job(self, temp_dir):
        """Test cancelling a job."""
        scheduler = LocalScheduler(HPCConfig())
        job = make_job("sleep 60", workdir=str(temp_dir))

        result = scheduler.submit(job)
        success = scheduler.cancel(result.job_id)

        assert success is True

    def test_generate_script(self, temp_dir):
        """Test script generation."""
        scheduler = LocalScheduler(HPCConfig())
        job = make_job(
            "echo hello",
            modules=["python/3.11"],
            workdir=str(temp_dir),
        )

        script = scheduler.preview(job)

        assert "#!/bin/bash" in script
        assert "echo hello" in script
        assert "set -e" in script

    def test_output_path(self, temp_dir):
        """Test output file creation."""
        scheduler = LocalScheduler(HPCConfig())
        job = make_job("echo hello", workdir=str(temp_dir), stdout="out.log")

        result = scheduler.submit(job)
        result.wait(poll_interval=0.05)

        stdout_path = scheduler.get_output_path(result.job_id, "stdout")
        assert stdout_path is not None
        assert stdout_path.exists()
        assert "hello" in stdout_path.read_text()

    def test_merged_output(self, temp_dir):
        """Test merged stdout/stderr."""
        scheduler = LocalScheduler(HPCConfig())
        job = make_job(
            "echo stdout; echo stderr >&2",
            workdir=str(temp_dir),
            stdout="merged.log",
        )

        result = scheduler.submit(job)
        result.wait(poll_interval=0.05)

        stdout_path = scheduler.get_output_path(result.job_id, "stdout")
        content = stdout_path.read_text()

        assert "stdout" in content
        assert "stderr" in content  # Merged

    def test_passthrough_mode(self, temp_dir):
        """Test passthrough mode: no stdout/stderr means no output files."""
        scheduler = LocalScheduler(HPCConfig())
        job = make_job("echo passthrough", workdir=str(temp_dir))

        result = scheduler.submit(job)
        result.wait(poll_interval=0.05)

        assert result.returncode == 0
        # No output paths should be stored in passthrough mode
        stdout_path = scheduler.get_output_path(result.job_id, "stdout")
        assert stdout_path is None

    def test_generate_script_with_modules(self, temp_dir):
        """Test that generated script includes module commands."""
        scheduler = LocalScheduler(HPCConfig())
        job = make_job(
            "echo hello",
            modules=["python/3.11", "gcc/12.2"],
            modules_path=["/opt/modulefiles"],
            workdir=str(temp_dir),
        )

        script = scheduler.preview(job)

        assert "module use /opt/modulefiles" in script
        assert "module load python/3.11" in script
        assert "module load gcc/12.2" in script

    def test_generate_script_modules_path_append(self, temp_dir):
        """modules_path_append renders as module use --append, after modules_path."""
        scheduler = LocalScheduler(HPCConfig())
        job = make_job(
            "echo hello",
            modules=["python/3.11"],
            modules_path=["/opt/modulefiles"],
            modules_path_append=["/home/me/modulefiles"],
            workdir=str(temp_dir),
        )

        script = scheduler.preview(job)

        assert "module use /opt/modulefiles" in script
        assert "module use --append /home/me/modulefiles" in script
        assert script.index("module use /opt/modulefiles") < script.index("module use --append")
        assert script.index("module use --append") < script.index("module load python/3.11")

    @requires_modules
    def test_module_loading(self, temp_dir):
        """Test that a module is actually loaded and sets env vars."""
        scheduler = LocalScheduler(HPCConfig())
        job = make_job(
            "echo $DUMMY_TOOL_LOADED",
            modules=["dummy_tool/1.0"],
            modules_path=[MODULEFILES_DIR],
            workdir=str(temp_dir),
            stdout="out.log",
        )

        result = scheduler.submit(job)
        result.wait(poll_interval=0.05)

        assert result.returncode == 0
        stdout_path = scheduler.get_output_path(result.job_id, "stdout")
        content = stdout_path.read_text().strip()
        assert content == "true"

    @requires_modules
    def test_multiple_modules(self, temp_dir):
        """Test loading multiple modules sets all env vars."""
        scheduler = LocalScheduler(HPCConfig())
        job = make_job(
            "echo $DUMMY_TOOL_LOADED $DUMMY_LIB_LOADED",
            modules=["dummy_tool/1.0", "dummy_lib/2.5"],
            modules_path=[MODULEFILES_DIR],
            workdir=str(temp_dir),
            stdout="out.log",
        )

        result = scheduler.submit(job)
        result.wait(poll_interval=0.05)

        assert result.returncode == 0
        stdout_path = scheduler.get_output_path(result.job_id, "stdout")
        content = stdout_path.read_text().strip()
        assert content == "true true"

    @requires_modules
    def test_module_sets_env_var(self, temp_dir):
        """Test that module sets specific version string in environment."""
        scheduler = LocalScheduler(HPCConfig())
        job = make_job(
            "echo $DUMMY_TOOL_VERSION",
            modules=["dummy_tool/1.0"],
            modules_path=[MODULEFILES_DIR],
            workdir=str(temp_dir),
            stdout="out.log",
        )

        result = scheduler.submit(job)
        result.wait(poll_interval=0.05)

        assert result.returncode == 0
        stdout_path = scheduler.get_output_path(result.job_id, "stdout")
        content = stdout_path.read_text().strip()
        assert content == "1.0"


class TestLocalRefusesWhatItCannotDo:
    """Nothing silently degrades: arrays and dependencies are NotSupportedError."""

    def test_array_not_supported(self):
        job = make_job(array="1-3")
        with pytest.raises(NotSupportedError, match="array"):
            LocalScheduler(HPCConfig()).submit(job)

    def test_dependencies_not_supported(self):
        job = make_job(dependencies=[Dependency("1")])
        with pytest.raises(NotSupportedError, match="dependencies"):
            LocalScheduler(HPCConfig()).submit(job)

    def test_preview_refuses_too(self):
        with pytest.raises(NotSupportedError):
            LocalScheduler(HPCConfig()).preview(make_job(array="1-3"))


class TestLocalEnvUnset:
    def test_env_unset_rendered_and_applied(self, temp_dir, monkeypatch):
        monkeypatch.setenv("HPC_TEST_UNSET_ME", "present")
        job = make_job(
            'echo "[${HPC_TEST_UNSET_ME:-gone}]"',
            env_unset=["HPC_TEST_UNSET_ME"],
            workdir=str(temp_dir),
            stdout="out.log",
        )
        scheduler = LocalScheduler(HPCConfig())
        assert "unset HPC_TEST_UNSET_ME" in scheduler.preview(job)
        result = scheduler.submit(job)
        result.wait(poll_interval=0.05)
        assert result.read_stdout().strip() == "[gone]"
