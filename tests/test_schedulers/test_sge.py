"""Tests for SGE scheduler."""

import dataclasses

import pytest

from hpc_runner.core.config import HPCConfig
from hpc_runner.core.dependency import Dependency, DependencyType
from hpc_runner.core.result import JobStatus
from hpc_runner.schedulers.sge import SGEScheduler
from hpc_runner.schedulers.sge.parser import (
    parse_qacct_output,
    parse_qstat_plain,
    parse_qstat_xml,
    parse_qsub_output,
    state_to_status,
)
from tests.fakes import make_job, patch_seam


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
        scheduler = SGEScheduler(HPCConfig())
        assert scheduler.name == "sge"

    def test_generate_script(self):
        """Test script generation."""
        scheduler = SGEScheduler(HPCConfig())
        job = make_job(
            "echo hello",
            name="test_job",
            cpu=4,
            mem="16G",
            time="2:00:00",
            queue="batch",
        )

        script = scheduler.preview(job)

        assert "#!/bin/bash" in script
        assert "#$ -N test_job" in script
        assert "#$ -pe" in script
        assert "echo hello" in script

    def test_generate_script_module_paths(self):
        """Both batch and interactive SGE scripts honour modules_path_append."""
        scheduler = SGEScheduler(HPCConfig())
        job = make_job(
            "xrun -f top.f",
            modules=["xcelium/26.03.006"],
            modules_path=["/site/modulefiles"],
            modules_path_append=["/proj/modulefiles"],
        )

        scripts = [
            scheduler.preview(job),
            scheduler.preview(dataclasses.replace(job, interactive=True)),
        ]
        for script in scripts:
            assert "module use /site/modulefiles" in script
            assert "module use --append /proj/modulefiles" in script
            assert script.index("module use --append") < script.index("module load xcelium")

    def test_generate_script_merged_output(self):
        """Test script generation with merged output."""
        scheduler = SGEScheduler(HPCConfig())
        job = make_job("echo hello", name="test_job")

        script = scheduler.preview(job)

        assert "#$ -j y" in script  # Join stdout/stderr

    def test_generate_script_separate_stderr(self):
        """Test script generation with separate stderr."""
        scheduler = SGEScheduler(HPCConfig())
        job = make_job("echo hello", name="test_job", stderr="error.log")

        script = scheduler.preview(job)

        assert "#$ -j y" not in script
        assert "#$ -e error.log" in script

    def test_submit_job(self, mock_sge_commands):
        """Test job submission with mocked qsub."""
        scheduler = SGEScheduler(HPCConfig())
        job = make_job("echo hello", name="test_job")

        result = scheduler.submit(job)

        assert result.job_id == "12345"

    def test_cancel_job(self, mock_sge_commands):
        """Test job cancellation."""
        scheduler = SGEScheduler(HPCConfig())
        success = scheduler.cancel("12345")
        assert success is True

    def test_get_status(self, mock_sge_commands):
        """Test getting job status."""
        scheduler = SGEScheduler(HPCConfig())
        status = scheduler.get_status("12345")
        assert status == JobStatus.RUNNING

    def test_batch_options_are_all_directives(self, mock_sge_commands):
        """Batch submission is ``qsub <script>``: every option is a #$ directive."""
        scheduler = SGEScheduler(HPCConfig())
        job = make_job("echo hello", name="test_job", cpu=4, mem="16G", queue="batch")

        script = scheduler.preview(job)
        scheduler.submit(job)

        for directive in ("#$ -N test_job", "#$ -pe smp 4", "#$ -l mem_free=16G", "#$ -q batch"):
            assert directive in script
        qsub = [c.args[0] for c in mock_sge_commands.call_args_list if c.args[0][0] == "qsub"]
        assert len(qsub) == 1
        assert len(qsub[0]) == 2  # qsub <script>, nothing else

    def test_generate_script_env_prepend(self):
        """Test script generation with env_prepend."""
        scheduler = SGEScheduler(HPCConfig())
        job = make_job(
            "echo hello",
            name="test_job",
            env_prepend={"PATH": "/new/bin"},
        )

        script = scheduler.preview(job)

        assert "# Prepend to environment variables" in script
        assert 'export PATH="/new/bin${PATH:+:$PATH}"' in script

    def test_generate_script_env_append(self):
        """Test script generation with env_append."""
        scheduler = SGEScheduler(HPCConfig())
        job = make_job(
            "echo hello",
            name="test_job",
            env_append={"PYTHONPATH": "/extra/lib"},
        )

        script = scheduler.preview(job)

        assert "# Append to environment variables" in script
        assert 'export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}/extra/lib"' in script

    def test_generate_script_all_env_types(self):
        """Test script with env_vars, env_prepend, and env_append together."""
        scheduler = SGEScheduler(HPCConfig())
        job = make_job(
            "echo hello",
            name="test_job",
            env_prepend={"PATH": "/cocotb/bin"},
            env_append={"PYTHONPATH": "/extra/lib"},
            env_vars={"COCOTB_TOPLEVEL": "counter"},
        )

        script = scheduler.preview(job)

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
        scheduler = SGEScheduler(HPCConfig())
        job = make_job(
            "echo hello",
            name="test_job",
            env_vars={"FOO": "bar", "BAZ": "qux"},
        )

        script = scheduler.preview(job)

        assert 'export FOO="bar"' in script
        assert 'export BAZ="qux"' in script

    def test_configurable_pe_name(self):
        """Test that PE name is configurable."""
        config = HPCConfig(
            schedulers={"sge": {"parallel_environment": "mpi", "memory_resource": "h_vmem"}}
        )
        scheduler = SGEScheduler(config)
        assert scheduler.pe_name == "mpi"
        assert scheduler.mem_resource == "h_vmem"
        script = scheduler.preview(make_job(cpu=2, mem="1G"))
        assert "#$ -pe mpi 2" in script
        assert "#$ -l h_vmem=1G" in script

    def test_list_active_jobs(self, mock_sge_commands):
        """Test listing active jobs."""
        scheduler = SGEScheduler(HPCConfig())
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
        scheduler = SGEScheduler(HPCConfig())

        # Only running jobs
        running = scheduler.list_active_jobs(status={JobStatus.RUNNING})
        assert len(running) == 1
        assert running[0].job_id == "12345"

        # Only pending jobs
        pending = scheduler.list_active_jobs(status={JobStatus.PENDING})
        assert len(pending) == 2

    def test_list_active_jobs_filter_by_queue(self, mock_sge_commands):
        """Test filtering jobs by queue."""
        scheduler = SGEScheduler(HPCConfig())

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

    def test_afterok_emits_publish_and_guard(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path))
        scheduler = SGEScheduler(HPCConfig())
        job = make_job("run_sim", name="sim", dependencies=[Dependency("111"), Dependency("222")])

        script = scheduler.preview(job)

        # Publisher (every job) and guard (afterok only) both present.
        assert "trap _hpc_publish_rc EXIT" in script
        assert "afterok guard" in script
        # Guard iterates over the actual dependency job IDs.
        assert "for _dep in 111 222 " in script
        assert str(tmp_path) in script

    def test_afterany_publishes_but_no_guard(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path))
        scheduler = SGEScheduler(HPCConfig())
        job = make_job(
            "run_sim", name="sim", dependencies=[Dependency("111", DependencyType.AFTERANY)]
        )

        script = scheduler.preview(job)

        assert "trap _hpc_publish_rc EXIT" in script  # still publishes its own rc
        assert "afterok guard" not in script  # but does not gate on parents

    def test_no_dependencies_has_no_guard(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path))
        scheduler = SGEScheduler(HPCConfig())
        script = scheduler.preview(make_job("echo hi", name="solo"))

        assert "trap _hpc_publish_rc EXIT" in script
        assert "afterok guard" not in script

    def test_enforce_afterok_disabled(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path))
        scheduler = SGEScheduler(HPCConfig())
        scheduler.enforce_afterok = False
        scheduler.exit_dir = ""
        job = make_job("run_sim", name="sim", dependencies=[Dependency("111")])

        script = scheduler.preview(job)

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
        scheduler = SGEScheduler(HPCConfig())
        job = make_job("echo SIM-RAN", name="sim", dependencies=[Dependency("111")])
        script = scheduler.preview(job)

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


class TestSGEExitSentinelFallback:
    """Status/exit-code fallback to the exit sentinel when qacct is unavailable.

    Precedence is qstat > qacct > sentinel.
    """

    QACCT_NOT_FOUND = "error: job id {job_id} not found\n"

    def _fake_run(self, qacct_stdout=None, qacct_rc=1):
        """Return a responder for the subprocess seam.

        qstat never knows the job.  qacct returns *qacct_stdout* with
        *qacct_rc*; by default it mimics SGE's "job id not found" (rc 1,
        error on stderr, empty stdout).
        """

        def run(cmd):
            if cmd[0] == "qstat":
                return 1, "", "Following jobs do not exist: \n"
            if cmd[0] == "qacct":
                if qacct_stdout is None:
                    return qacct_rc, "", self.QACCT_NOT_FOUND.format(job_id=cmd[-1])
                return qacct_rc, qacct_stdout, ""
            return 0, "", ""

        return run

    @pytest.fixture
    def scheduler(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path))
        sched = SGEScheduler(HPCConfig())
        assert sched.exit_dir == str(tmp_path)
        return sched

    def test_sentinel_zero_completed(self, scheduler, tmp_path):
        (tmp_path / "123.rc").write_text("0\n")
        with patch_seam(self._fake_run()):
            assert scheduler.get_status("123") == JobStatus.COMPLETED
            assert scheduler.get_exit_code("123") == 0

    def test_sentinel_nonzero_failed(self, scheduler, tmp_path):
        (tmp_path / "123.rc").write_text("3\n")
        with patch_seam(self._fake_run()):
            assert scheduler.get_status("123") == JobStatus.FAILED
            assert scheduler.get_exit_code("123") == 3

    def test_array_task_falls_back_to_base_id(self, scheduler, tmp_path):
        (tmp_path / "123.rc").write_text("0\n")
        with patch_seam(self._fake_run()):
            assert scheduler.get_status("123.4") == JobStatus.COMPLETED
            assert scheduler.get_exit_code("123.4") == 0

    def test_task_specific_sentinel_preferred(self, scheduler, tmp_path):
        (tmp_path / "123.rc").write_text("0\n")
        (tmp_path / "123.4.rc").write_text("5\n")
        assert scheduler._read_exit_sentinel("123.4") == 5

    def test_no_sentinel_unknown(self, scheduler):
        with patch_seam(self._fake_run()):
            assert scheduler.get_status("123") == JobStatus.UNKNOWN
            assert scheduler.get_exit_code("123") is None

    def test_unparsable_sentinel_ignored(self, scheduler, tmp_path):
        (tmp_path / "123.rc").write_text("garbage\n")
        with patch_seam(self._fake_run()):
            assert scheduler.get_status("123") == JobStatus.UNKNOWN
            assert scheduler.get_exit_code("123") is None

    def test_enforce_afterok_off_ignores_sentinel(self, scheduler, tmp_path):
        (tmp_path / "123.rc").write_text("0\n")
        scheduler.enforce_afterok = False
        scheduler.exit_dir = ""
        with patch_seam(self._fake_run()):
            assert scheduler.get_status("123") == JobStatus.UNKNOWN
            assert scheduler.get_exit_code("123") is None

    def test_qacct_without_exit_status_uses_sentinel(self, scheduler, tmp_path):
        (tmp_path / "123.rc").write_text("0\n")
        run = self._fake_run(qacct_stdout="qname  all.q\n", qacct_rc=0)
        with patch_seam(run):
            assert scheduler.get_status("123") == JobStatus.COMPLETED
            assert scheduler.get_exit_code("123") == 0

    def test_qacct_wins_over_sentinel(self, scheduler, tmp_path):
        (tmp_path / "123.rc").write_text("0\n")
        qacct = "==============\nqname        all.q\nexit_status  2\n"
        with patch_seam(self._fake_run(qacct, 0)):
            assert scheduler.get_status("123") == JobStatus.FAILED
            assert scheduler.get_exit_code("123") == 2


class TestSGESubmissionContract:
    """One submit()/preview() shape; directives-only batch; qrsh for interactive."""

    @pytest.fixture
    def scheduler(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HPC_SCRIPT_DIR", str(tmp_path / "scripts"))
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path / "exit"))
        return SGEScheduler(HPCConfig())

    def test_passthrough_is_one_directive_line(self, scheduler):
        """B1: '-q batch.q -l gpu=2' stays together on one #$ line."""
        job = make_job("python train.py", scheduler_args={"sge": ["-q", "batch.q", "-l", "gpu=2"]})
        script = scheduler.preview(job)
        assert "#$ -q batch.q -l gpu=2\n" in script
        assert "#$ -batch.q" not in script
        assert "#$ -gpu=2" not in script

    def test_other_schedulers_passthrough_ignored(self, scheduler):
        job = make_job(scheduler_args={"slurm": ["--constraint=x"]})
        assert "--constraint" not in scheduler.preview(job)

    def test_preview_equals_submitted_script(self, scheduler, tmp_path, mock_sge_commands):
        job = make_job("echo hello", name="j", cpu=2, dependencies=[Dependency("5")])
        scheduler.submit(job, keep_script=True)
        written = list((tmp_path / "scripts").glob("hpc_batch_*.sh"))
        assert len(written) == 1
        assert written[0].read_text() == scheduler.preview(job)

    def test_script_deleted_without_keep_script(self, scheduler, tmp_path, mock_sge_commands):
        scheduler.submit(make_job())
        assert list((tmp_path / "scripts").glob("*.sh")) == []

    def test_project_directive(self, scheduler):
        assert "#$ -P proj1" in scheduler.preview(make_job(project="proj1"))

    def test_env_unset_rendered(self, scheduler):
        script = scheduler.preview(make_job(env_unset=["https_proxy"]))
        assert "unset https_proxy" in script

    def test_hold_jid_lists_all_parents_guard_only_afterok(self, scheduler):
        job = make_job(
            dependencies=[Dependency("1"), Dependency("2", DependencyType.AFTERANY)],
        )
        script = scheduler.preview(job)
        assert "#$ -hold_jid 1,2" in script
        assert "for _dep in 1 ; do" in script

    def test_array_job(self, scheduler, mock_sge_commands):
        from hpc_runner.core.result import ArrayJobResult

        job = make_job(array="1-10:2%3")
        assert "#$ -t 1-10:2%3" in scheduler.preview(job)
        result = scheduler.submit(job)
        assert isinstance(result, ArrayJobResult)
        assert result.job_id == "12345"
        assert result.array.count == 5

    def test_interactive_runs_qrsh_and_returns_exit_code(self, scheduler):
        calls = []

        def respond(argv):
            calls.append(argv)
            return 3, "", ""

        job = make_job(
            "xterm",
            interactive=True,
            name="gui",
            queue="interactive.q",
            priority=-5,
            project="proj1",
            stdout="out.log",
            cpu=2,
            scheduler_args={"sge": ["-l", "display=1"]},
        )
        with patch_seam(respond):
            result = scheduler.submit(job)

        assert result.job_id is None
        assert result.exit_code == 3
        assert result.job is job
        argv = calls[0]
        assert argv[0] == "qrsh"
        # qrsh rejects -N/-o/-e/-j/-S; those are skipped
        for flag in ("-N", "-o", "-e", "-j", "-S"):
            assert flag not in argv
        for pair in (["-q", "interactive.q"], ["-p", "-5"], ["-P", "proj1"], ["-pe", "smp", "2"]):
            idx = argv.index(pair[0])
            assert argv[idx : idx + len(pair)] == pair
        assert argv[-3:-1] == ["-l", "display=1"]
        assert argv[-1].endswith(".sh")

    def test_interactive_preview_has_no_directives(self, scheduler):
        script = scheduler.preview(make_job("xterm", interactive=True))
        assert "#$" not in script
        assert "xterm" in script

    def test_result_handle_without_job(self, scheduler, mock_sge_commands):
        handle = scheduler.result("12345")
        assert handle.job is None
        assert handle.job_id == "12345"
        assert handle.status == JobStatus.RUNNING
