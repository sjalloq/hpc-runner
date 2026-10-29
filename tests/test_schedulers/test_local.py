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

    def test_exit_113_is_skipped(self, temp_dir):
        """113 is reserved (HPC_EXIT_DEP_FAILED) on every scheduler."""
        scheduler = LocalScheduler(HPCConfig())
        result = scheduler.submit(make_job("exit 113", workdir=str(temp_dir)))
        assert result.wait(poll_interval=0.05) == JobStatus.SKIPPED
        assert result.returncode == 113

    def test_get_statuses_bulk(self, temp_dir):
        scheduler = LocalScheduler(HPCConfig())
        done = scheduler.submit(make_job("true", workdir=str(temp_dir)))
        done.wait(poll_interval=0.05)
        running = scheduler.submit(make_job("sleep 10", workdir=str(temp_dir)))
        try:
            assert scheduler.get_statuses([done.job_id, running.job_id, "nope"]) == {
                done.job_id: JobStatus.COMPLETED,
                running.job_id: JobStatus.RUNNING,
                "nope": JobStatus.UNKNOWN,
            }
        finally:
            scheduler.cancel(running.job_id)

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

        stdout_path = result.stdout_path
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

        stdout_path = result.stdout_path
        content = stdout_path.read_text()

        assert "stdout" in content
        assert "stderr" in content  # Merged

    def test_default_pattern_read_stdout(self, temp_dir):
        """B7: with no stdout set, the default pattern is recorded and readable."""
        scheduler = LocalScheduler(HPCConfig())
        job = make_job("echo hi", name="greet", workdir=str(temp_dir))

        result = scheduler.submit(job)
        result.wait(poll_interval=0.05)

        assert result.stdout_path == temp_dir / f"greet.{result.job_id}.out"
        assert result.stderr_path is None  # merged
        assert result.read_stdout() == "hi\n"
        assert result.read_stderr() is None

    def test_unsafe_name_sanitised_in_file_name(self, temp_dir):
        scheduler = LocalScheduler(HPCConfig())
        job = make_job(
            "echo hi", name="3 bad/name", workdir=str(temp_dir), env_vars={"N": "${hpc.job_name}"}
        )
        result = scheduler.submit(job)
        assert result.wait(poll_interval=0.05) is JobStatus.COMPLETED
        assert result.stdout_path == temp_dir / f"3_bad_name.{result.job_id}.out"
        assert result.read_stdout() == "hi\n"
        assert "export N=3_bad_name" in scheduler.preview(job)

    def test_separate_stderr(self, temp_dir):
        scheduler = LocalScheduler(HPCConfig())
        job = make_job(
            "echo out; echo err >&2",
            name="n",
            workdir=str(temp_dir),
            stderr="${hpc.job_name}.err",
        )
        result = scheduler.submit(job)
        result.wait(poll_interval=0.05)
        assert result.read_stdout() == "out\n"
        assert result.stderr_path == temp_dir / "n.err"
        assert result.read_stderr() == "err\n"

    def test_output_directory_created(self, temp_dir):
        scheduler = LocalScheduler(HPCConfig())
        job = make_job("echo hi", workdir=str(temp_dir), stdout="logs/day1/${hpc.job_name}.out")
        result = scheduler.submit(job)
        result.wait(poll_interval=0.05)
        assert result.stdout_path is not None
        assert result.stdout_path.parent == temp_dir / "logs" / "day1"
        assert result.read_stdout() == "hi\n"

    def test_host_in_pattern_path_unknown(self, temp_dir):
        scheduler = LocalScheduler(HPCConfig())
        job = make_job("echo hi", workdir=str(temp_dir), stdout="out.${hpc.host}")
        result = scheduler.submit(job)
        result.wait(poll_interval=0.05)
        assert result.stdout_path is None
        with pytest.raises(ValueError, match=r"\$\{hpc.host\}"):
            result.read_stdout()
        assert len(list(temp_dir.glob("out.*"))) == 1  # the shell filled in $HOSTNAME

    def test_interactive_has_no_output_paths(self, temp_dir):
        scheduler = LocalScheduler(HPCConfig())
        job = make_job("true", interactive=True, workdir=str(temp_dir))
        result = scheduler.submit(job)
        assert result.stdout_path is None
        assert result.read_stdout() is None
        assert "exec >" not in scheduler.preview(job)
        assert list(temp_dir.iterdir()) == []

    def test_preview_shows_placeholder_id(self):
        script = LocalScheduler(HPCConfig()).preview(
            make_job("true", name="n", workdir="/tmp/x y", stderr="e.${hpc.host}")
        )
        assert """exec > '/tmp/x y/'n.'<job_id>'.out 2> '/tmp/x y/e.'"$HOSTNAME"\n""" in script

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
        stdout_path = result.stdout_path
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
        stdout_path = result.stdout_path
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
        stdout_path = result.stdout_path
        content = stdout_path.read_text().strip()
        assert content == "1.0"


class TestLocalRefusesWhatItCannotDo:
    """Nothing silently degrades: arrays and dependencies are NotSupportedError."""

    def test_array_not_supported(self):
        job = make_job(array="1-3")
        with pytest.raises(NotSupportedError, match="array"):
            LocalScheduler(HPCConfig()).submit(job)

    def test_interactive_dependencies_message(self):
        job = make_job(interactive=True, dependencies=[Dependency("1")])
        with pytest.raises(NotSupportedError, match="interactive jobs cannot have dependencies"):
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


class TestLocalJobIds:
    """The id counter is per instance; ids stay unique across instances."""

    def test_ids_unique_within_and_across_instances(self, temp_dir):
        first, second = LocalScheduler(HPCConfig()), LocalScheduler(HPCConfig())
        results = [
            sched.submit(make_job("true", workdir=str(temp_dir)))
            for sched in (first, second, first, second)
        ]
        for result in results:
            result.wait(poll_interval=0.01)
        ids = [result.job_id for result in results]
        assert len(set(ids)) == 4
        assert all(i.startswith("local_") for i in ids)
        assert ids[0].startswith("local_1_") and ids[2].startswith("local_2_")
        assert ids[1].startswith("local_1_")  # its own counter

    def test_no_array_task_ids(self):
        with pytest.raises(NotSupportedError, match="array"):
            LocalScheduler(HPCConfig()).task_job_id("1", 2)
