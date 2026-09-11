"""Tests for SGE scheduler."""

from unittest.mock import patch

import pytest

from hpc_runner.core.job import Job
from hpc_runner.core.result import JobStatus
from hpc_runner.schedulers.sge import SGEScheduler
from hpc_runner.schedulers.sge.parser import (
    parse_qacct_output,
    parse_qstat_plain,
    parse_qstat_xml,
    parse_qsub_output,
    state_to_status,
)


class TestSGEParser:
    """Tests for SGE output parsers."""

    def test_parse_qsub_output(self):
        """Test parsing qsub output."""
        output = 'Your job 12345 ("test_job") has been submitted\n'
        job_id = parse_qsub_output(output)
        assert job_id == "12345"

    def test_parse_qsub_array_output(self):
        """Test parsing qsub array job output."""
        output = 'Your job-array 12345.1-10:1 ("test_job") has been submitted\n'
        job_id = parse_qsub_output(output)
        assert job_id == "12345"

    def test_parse_qstat_plain(self):
        """Test parsing plain qstat output."""
        output = """job-ID  prior   name       user         state submit/start at     queue                          slots ja-task-ID
-----------------------------------------------------------------------------------------------------------------
12345   0.55500 test_job   user         r     01/01/2024 10:00:00 all.q@node1                    1
12346   0.50000 other_job  user         qw    01/01/2024 09:00:00                                2
"""
        jobs = parse_qstat_plain(output)

        assert "12345" in jobs
        assert jobs["12345"]["state"] == "r"
        assert "12346" in jobs
        assert jobs["12346"]["state"] == "qw"

    def test_parse_qacct_output(self):
        """Test parsing qacct output."""
        output = """==============================================================
qname        all.q
hostname     node1
owner        user
jobname      test_job
jobnumber    12345
exit_status  0
"""
        info = parse_qacct_output(output)

        assert info["qname"] == "all.q"
        assert info["exit_status"] == "0"
        assert info["jobnumber"] == "12345"

    def test_state_to_status(self):
        """Test SGE state to JobStatus mapping."""
        assert state_to_status("r") == JobStatus.RUNNING
        assert state_to_status("qw") == JobStatus.PENDING
        assert state_to_status("Eqw") == JobStatus.FAILED
        assert state_to_status("dr") == JobStatus.CANCELLED


class TestSGEScheduler:
    """Tests for SGEScheduler."""

    def test_scheduler_name(self):
        """Test scheduler name."""
        scheduler = SGEScheduler()
        assert scheduler.name == "sge"

    def test_generate_script(self):
        """Test script generation."""
        scheduler = SGEScheduler()
        job = Job(
            command="echo hello",
            name="test_job",
            cpu=4,
            mem="16G",
            time="2:00:00",
            queue="batch",
        )

        script = scheduler.generate_script(job)

        assert "#!/bin/bash" in script
        assert "#$ -N test_job" in script
        assert "#$ -pe" in script
        assert "echo hello" in script

    def test_generate_script_merged_output(self):
        """Test script generation with merged output."""
        scheduler = SGEScheduler()
        job = Job(command="echo hello", name="test_job")

        script = scheduler.generate_script(job)

        assert "#$ -j y" in script  # Join stdout/stderr

    def test_generate_script_separate_stderr(self):
        """Test script generation with separate stderr."""
        scheduler = SGEScheduler()
        job = Job(command="echo hello", name="test_job", stderr="error.log")

        script = scheduler.generate_script(job)

        assert "#$ -j y" not in script
        assert "#$ -e error.log" in script

    def test_submit_job(self, mock_sge_commands):
        """Test job submission with mocked qsub."""
        scheduler = SGEScheduler()
        job = Job(command="echo hello", name="test_job")

        result = scheduler.submit(job)

        assert result.job_id == "12345"

    def test_cancel_job(self, mock_sge_commands):
        """Test job cancellation."""
        scheduler = SGEScheduler()
        success = scheduler.cancel("12345")
        assert success is True

    def test_get_status(self, mock_sge_commands):
        """Test getting job status."""
        scheduler = SGEScheduler()
        status = scheduler.get_status("12345")
        assert status == JobStatus.RUNNING

    def test_build_submit_command(self):
        """Test building qsub command."""
        scheduler = SGEScheduler()
        job = Job(
            command="echo hello",
            name="test_job",
            cpu=4,
            mem="16G",
            queue="batch",
        )

        cmd = scheduler.build_submit_command(job)

        assert cmd[0] == "qsub"
        assert "-N" in cmd
        assert "test_job" in cmd
        assert "-pe" in cmd
        assert "-q" in cmd

    def test_generate_script_env_prepend(self):
        """Test script generation with env_prepend."""
        scheduler = SGEScheduler()
        job = Job(
            command="echo hello",
            name="test_job",
            env_prepend={"PATH": "/new/bin"},
        )

        script = scheduler.generate_script(job)

        assert "# Prepend to environment variables" in script
        assert 'export PATH="/new/bin${PATH:+:$PATH}"' in script

    def test_generate_script_env_append(self):
        """Test script generation with env_append."""
        scheduler = SGEScheduler()
        job = Job(
            command="echo hello",
            name="test_job",
            env_append={"PYTHONPATH": "/extra/lib"},
        )

        script = scheduler.generate_script(job)

        assert "# Append to environment variables" in script
        assert 'export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}/extra/lib"' in script

    def test_generate_script_all_env_types(self):
        """Test script with env_vars, env_prepend, and env_append together."""
        scheduler = SGEScheduler()
        job = Job(
            command="echo hello",
            name="test_job",
            env_prepend={"PATH": "/cocotb/bin"},
            env_append={"PYTHONPATH": "/extra/lib"},
            env_vars={"COCOTB_TOPLEVEL": "counter"},
        )

        script = scheduler.generate_script(job)

        # All three sections should be present
        assert 'export PATH="/cocotb/bin${PATH:+:$PATH}"' in script
        assert 'export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}/extra/lib"' in script
        assert 'export COCOTB_TOPLEVEL="counter"' in script

        # Prepend/append should come before env_vars (full overwrite)
        prepend_pos = script.index("Prepend to environment variables")
        append_pos = script.index("Append to environment variables")
        vars_pos = script.index("Set custom environment variables")
        assert prepend_pos < append_pos < vars_pos

    def test_generate_script_env_vars_unchanged(self):
        """Test that existing env_vars behavior is unchanged."""
        scheduler = SGEScheduler()
        job = Job(
            command="echo hello",
            name="test_job",
            env_vars={"FOO": "bar", "BAZ": "qux"},
        )

        script = scheduler.generate_script(job)

        assert 'export FOO="bar"' in script
        assert 'export BAZ="qux"' in script

    def test_configurable_pe_name(self):
        """Test that PE name is configurable."""
        with patch("hpc_runner.schedulers.sge.scheduler.get_config") as mock_config:
            mock_config.return_value.get_scheduler_config.return_value = {
                "parallel_environment": "mpi",
                "memory_resource": "h_vmem",
            }

            scheduler = SGEScheduler()
            assert scheduler.pe_name == "mpi"
            assert scheduler.mem_resource == "h_vmem"

    def test_list_active_jobs(self, mock_sge_commands):
        """Test listing active jobs."""
        scheduler = SGEScheduler()
        jobs = scheduler.list_active_jobs()

        # Should return 3 jobs from mock XML
        assert len(jobs) == 3

        # Check first job (running)
        running_jobs = [j for j in jobs if j.status == JobStatus.RUNNING]
        assert len(running_jobs) == 1
        assert running_jobs[0].job_id == "12345"
        assert running_jobs[0].name == "running_job"
        assert running_jobs[0].user == "testuser"
        assert running_jobs[0].queue == "batch.q"
        assert running_jobs[0].cpu == 4

        # Check pending jobs
        pending_jobs = [j for j in jobs if j.status == JobStatus.PENDING]
        assert len(pending_jobs) == 2

    def test_list_active_jobs_filter_by_status(self, mock_sge_commands):
        """Test filtering jobs by status."""
        scheduler = SGEScheduler()

        # Only running jobs
        running = scheduler.list_active_jobs(status={JobStatus.RUNNING})
        assert len(running) == 1
        assert running[0].job_id == "12345"

        # Only pending jobs
        pending = scheduler.list_active_jobs(status={JobStatus.PENDING})
        assert len(pending) == 2

    def test_list_active_jobs_filter_by_queue(self, mock_sge_commands):
        """Test filtering jobs by queue."""
        scheduler = SGEScheduler()

        # Filter by batch.q
        batch_jobs = scheduler.list_active_jobs(queue="batch.q")
        assert len(batch_jobs) == 1
        assert batch_jobs[0].job_id == "12345"

        # Filter by gpu.q
        gpu_jobs = scheduler.list_active_jobs(queue="gpu.q")
        assert len(gpu_jobs) == 1
        assert gpu_jobs[0].job_id == "12347"


class TestSGEParserXML:
    """Tests for qstat XML parsing."""

    def test_parse_qstat_xml_running_job(self):
        """Test parsing running job from XML."""
        xml = """<?xml version='1.0'?>
<job_info>
  <queue_info>
    <job_list state="running">
      <JB_job_number>99999</JB_job_number>
      <JB_name>my_job</JB_name>
      <JB_owner>alice</JB_owner>
      <state>r</state>
      <queue_name>compute.q@node5</queue_name>
      <slots>8</slots>
    </job_list>
  </queue_info>
</job_info>
"""
        jobs = parse_qstat_xml(xml)

        assert "99999" in jobs
        job = jobs["99999"]
        assert job["name"] == "my_job"
        assert job["user"] == "alice"
        assert job["state"] == "r"
        assert job["queue"] == "compute.q"
        assert job["slots"] == 8

    def test_parse_qstat_xml_pending_job(self):
        """Test parsing pending job from XML."""
        xml = """<?xml version='1.0'?>
<job_info>
  <job_info>
    <job_list state="pending">
      <JB_job_number>88888</JB_job_number>
      <JB_name>waiting_job</JB_name>
      <JB_owner>bob</JB_owner>
      <state>qw</state>
      <slots>1</slots>
    </job_list>
  </job_info>
</job_info>
"""
        jobs = parse_qstat_xml(xml)

        assert "88888" in jobs
        job = jobs["88888"]
        assert job["name"] == "waiting_job"
        assert job["state"] == "qw"

    def test_parse_qstat_xml_empty(self):
        """Test parsing empty qstat XML output."""
        xml = """<?xml version='1.0'?>
<job_info>
  <queue_info>
  </queue_info>
  <job_info>
  </job_info>
</job_info>
"""
        jobs = parse_qstat_xml(xml)
        assert len(jobs) == 0

    def test_parse_qstat_xml_with_timestamps(self):
        """Test parsing jobs with submission/start times."""
        xml = """<?xml version='1.0'?>
<job_info>
  <queue_info>
    <job_list state="running">
      <JB_job_number>77777</JB_job_number>
      <JB_name>timed_job</JB_name>
      <JB_owner>charlie</JB_owner>
      <state>r</state>
      <slots>2</slots>
      <JB_submission_time>1704110400</JB_submission_time>
      <JAT_start_time>1704110460</JAT_start_time>
    </job_list>
  </queue_info>
</job_info>
"""
        jobs = parse_qstat_xml(xml)

        assert "77777" in jobs
        job = jobs["77777"]
        assert job["submit_time"] == 1704110400
        assert job["start_time"] == 1704110460


class TestSGEAfterok:
    """afterok emulation via exit-code sentinel files.

    SGE's ``-hold_jid`` is afterany (it releases dependents even when the
    parent fails), so each job publishes its exit code to a sentinel and
    afterok dependents verify their parents succeeded before running.
    """

    def _job_result(self, scheduler, job_id):
        from hpc_runner.core.result import JobResult

        return JobResult(job_id=job_id, scheduler=scheduler, job=Job("parent", name="p"))

    def test_afterok_emits_publish_and_guard(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path))
        scheduler = SGEScheduler()
        job = Job(command="run_sim", name="sim")
        job.after(self._job_result(scheduler, "111"), self._job_result(scheduler, "222"))

        script = scheduler.generate_script(job)

        # Publisher (every job) and guard (afterok only) both present.
        assert "trap _hpc_publish_rc EXIT" in script
        assert "afterok guard" in script
        # Guard iterates over the actual dependency job IDs.
        assert "for _dep in 111 222 " in script
        assert str(tmp_path) in script

    def test_afterany_publishes_but_no_guard(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path))
        scheduler = SGEScheduler()
        job = Job(command="run_sim", name="sim")
        job.after(self._job_result(scheduler, "111"), type="afterany")

        script = scheduler.generate_script(job)

        assert "trap _hpc_publish_rc EXIT" in script  # still publishes its own rc
        assert "afterok guard" not in script  # but does not gate on parents

    def test_no_dependencies_has_no_guard(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path))
        scheduler = SGEScheduler()
        script = scheduler.generate_script(Job(command="echo hi", name="solo"))

        assert "trap _hpc_publish_rc EXIT" in script
        assert "afterok guard" not in script

    def test_enforce_afterok_disabled(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path))
        scheduler = SGEScheduler()
        scheduler.enforce_afterok = False
        scheduler.exit_dir = ""
        job = Job(command="run_sim", name="sim")
        job.after(self._job_result(scheduler, "111"))

        script = scheduler.generate_script(job)

        assert "_hpc_publish_rc" not in script
        assert "afterok guard" not in script
        assert "#$ -hold_jid 111" in script  # plain hold_jid still applied

    @pytest.mark.parametrize(
        "parent_rc,expect_ran,expect_exit",
        [(0, True, 0), (2, False, 1)],
    )
    def test_guard_runtime_behaviour(
        self, monkeypatch, tmp_path, parent_rc, expect_ran, expect_exit
    ):
        """End-to-end: render the script and run it under bash.

        A passing parent lets the sim run; a failing parent aborts it before
        the command executes. Either way the job publishes its own sentinel.
        """
        import os
        import subprocess

        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path))
        scheduler = SGEScheduler()
        job = Job(command="echo SIM-RAN", name="sim")
        job.after(self._job_result(scheduler, "111"))
        script = scheduler.generate_script(job)

        script_file = tmp_path / "sim.sh"
        script_file.write_text(script)
        (tmp_path / "111.rc").write_text(f"{parent_rc}\n")

        result = subprocess.run(
            ["bash", str(script_file)],
            capture_output=True,
            text=True,
            env={**os.environ, "JOB_ID": "999"},
        )

        assert result.returncode == expect_exit
        assert ("SIM-RAN" in result.stdout) is expect_ran
        # The job always publishes its own exit code for its dependents.
        assert (tmp_path / "999.rc").read_text().strip() == str(expect_exit)
