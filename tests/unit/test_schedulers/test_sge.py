"""Tests for SGE scheduler."""

import dataclasses
import os
import time

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
"""  # noqa: E501
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
        assert "#$ -e /work/error.log" in script

    def test_submit_job(self, mock_sge_commands, sge_fixtures, tmp_path):
        """The id comes from qsub's own "Your job N" line."""
        scheduler = SGEScheduler(HPCConfig())
        job = make_job("echo hello", name="test_job", workdir=str(tmp_path))

        result = scheduler.submit(job)

        assert result.job_id == sge_fixtures.submitted_id

    def test_cancel_job(self, mock_sge_commands, sge_fixtures):
        """qdel of a known job is True; of an unknown id (qdel exits 1) False."""
        scheduler = SGEScheduler(HPCConfig())
        assert scheduler.cancel(sge_fixtures.job("running")) is True
        assert scheduler.cancel("999999") is False

    @pytest.mark.parametrize(
        ("scenario", "task", "expected"),
        [
            ("running", None, JobStatus.RUNNING),
            ("held", None, JobStatus.PENDING),
            ("eqw", None, JobStatus.FAILED),
            ("array", None, JobStatus.RUNNING),  # tasks 1-7 run, 8-10 queued
            ("array", "1", JobStatus.RUNNING),
            ("array", "9", JobStatus.PENDING),
            ("ok", None, JobStatus.COMPLETED),
            ("exit1", None, JobStatus.FAILED),
            ("signal", None, JobStatus.FAILED),  # qdel: failed 100, exit 137 (Killed)
            ("lost_node", None, JobStatus.FAILED),  # failed 19, exit_status 0
            ("array_done", "2", JobStatus.COMPLETED),
        ],
    )
    def test_get_status(
        self, mock_sge_commands, sge_fixtures, monkeypatch, tmp_path, scenario, task, expected
    ):
        """Every captured scenario, through qstat then qacct, reports its status."""
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path))
        scheduler = SGEScheduler(HPCConfig())
        job_id = sge_fixtures.job(scenario)
        if task:
            job_id = scheduler.task_job_id(job_id, int(task))
        assert scheduler.get_status(job_id) is expected

    def test_get_status_unknown_id(self, mock_sge_commands, monkeypatch, tmp_path):
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path))
        assert SGEScheduler(HPCConfig()).get_status("999999") is JobStatus.UNKNOWN

    def test_batch_options_are_all_directives(self, mock_sge_commands, tmp_path):
        """Batch submission is ``qsub <script>``: every option is a #$ directive."""
        scheduler = SGEScheduler(HPCConfig())
        job = make_job(
            "echo hello", name="test_job", cpu=4, mem="16G", queue="batch", workdir=str(tmp_path)
        )

        script = scheduler.preview(job)
        scheduler.submit(job)

        # mem is per job and requested as given (memory_per = "job" by default).
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
        assert "export PATH=/new/bin${PATH:+:$PATH}" in script

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
        assert "export PYTHONPATH=${PYTHONPATH:+$PYTHONPATH:}/extra/lib" in script

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
        assert "export PATH=/cocotb/bin${PATH:+:$PATH}" in script
        assert "export PYTHONPATH=${PYTHONPATH:+$PYTHONPATH:}/extra/lib" in script
        assert "export COCOTB_TOPLEVEL=counter" in script

        # Prepend/append should come before env_vars (full overwrite)
        prepend_pos = script.index("Prepend to environment variables")
        append_pos = script.index("Append to environment variables")
        vars_pos = script.index("Set environment variables")
        assert prepend_pos < append_pos < vars_pos

    def test_generate_script_env_vars(self):
        """env_vars render as quoted exports."""
        scheduler = SGEScheduler(HPCConfig())
        job = make_job(
            "echo hello",
            name="test_job",
            env_vars={"FOO": "bar", "BAZ": "qux"},
        )

        script = scheduler.preview(job)

        assert "export FOO=bar" in script
        assert "export BAZ=qux" in script

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
        assert "#$ -l h_vmem=1G" in script  # requested as given (memory_per = "job")

    def test_list_active_jobs(self, mock_sge_commands, sge_fixtures):
        """One entry per job id from the mixed snapshot: r, hqw, Eqw and a 10-task array."""
        scheduler = SGEScheduler(HPCConfig())
        jobs = {j.job_id: j for j in scheduler.list_active_jobs()}
        argv = mock_sge_commands.call_args[0][0]
        assert argv == ["qstat", "-xml", "-u", "*"]

        ids = {sge_fixtures.job(s) for s in ("running", "held", "eqw", "array")}
        assert set(jobs) == ids

        running = jobs[sge_fixtures.job("running")]
        assert running.status is JobStatus.RUNNING
        assert (running.name, running.user) == ("running", "tester")
        assert (running.queue, running.node, running.cpu) == ("all.q", "exec1", 1)
        assert running.start_time is not None and running.runtime is not None

        # The array has running and queued tasks; its one entry is the running one.
        array = jobs[sge_fixtures.job("array")]
        assert array.status is JobStatus.RUNNING
        assert array.array_task_id == 1

        held = jobs[sge_fixtures.job("held")]
        assert held.status is JobStatus.PENDING
        assert held.queue is None and held.submit_time is not None

        assert jobs[sge_fixtures.job("eqw")].status is JobStatus.FAILED

    def test_list_active_jobs_filter_by_user_changes_the_argv(self, mock_sge_commands):
        SGEScheduler(HPCConfig()).list_active_jobs(user="bob")
        assert mock_sge_commands.call_args[0][0] == ["qstat", "-xml", "-u", "bob"]

    def test_list_active_jobs_filter_by_status(self, mock_sge_commands, sge_fixtures):
        scheduler = SGEScheduler(HPCConfig())
        running = {j.job_id for j in scheduler.list_active_jobs(status={JobStatus.RUNNING})}
        assert running == {sge_fixtures.job("running"), sge_fixtures.job("array")}
        pending = {j.job_id for j in scheduler.list_active_jobs(status={JobStatus.PENDING})}
        assert pending == {sge_fixtures.job("held")}
        failed = {j.job_id for j in scheduler.list_active_jobs(status={JobStatus.FAILED})}
        assert failed == {sge_fixtures.job("eqw")}

    def test_list_active_jobs_filter_by_queue(self, mock_sge_commands, sge_fixtures):
        """Queued jobs have no queue yet, so a queue filter keeps running ones only."""
        scheduler = SGEScheduler(HPCConfig())
        all_q = {j.job_id for j in scheduler.list_active_jobs(queue="all.q")}
        assert all_q == {sge_fixtures.job("running"), sge_fixtures.job("array")}
        assert scheduler.list_active_jobs(queue="gpu.q") == []


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


def _guard_section(script: str) -> str:
    """The dependency-guard part of a rendered script ('' if none)."""
    start = script.find("# --- hpc-runner dependency guard")
    if start < 0:
        return ""
    return script[start : script.index("# Execute command", start)]


class TestSGEDependencies:
    """D8: one -hold_jid, per-parent guards emulating afterok/afternotok.

    SGE's ``-hold_jid`` is afterany (it releases dependents even when the
    parent fails), so each job publishes its exit code to a sentinel and a
    guard in the dependent tests each parent's code with that parent's type.
    """

    @pytest.fixture
    def scheduler(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path))
        monkeypatch.setenv("HPC_SCRIPT_DIR", str(tmp_path / "scripts"))
        monkeypatch.setattr(SGEScheduler, "DEP_WAIT_SECONDS", 1)
        return SGEScheduler(HPCConfig())

    def test_bare_id_renders_glob_fallback(self, scheduler):
        guard = _guard_section(scheduler.preview(make_job(dependencies=[Dependency("50")])))
        assert "_hpc_dep_check_id afterok 50\n" in guard
        assert 'for _f in "$_hpc_exit_dir/$2".*.rc; do' in guard

    @pytest.mark.parametrize(
        "dep_type,task_rcs,expect_ran",
        [
            (DependencyType.AFTEROK, ["0", "0", "0"], True),
            (DependencyType.AFTEROK, ["0", "3", "0"], False),
            (DependencyType.AFTERNOTOK, ["1", "2", "3"], True),
            (DependencyType.AFTERNOTOK, ["1", "0", "3"], False),
        ],
    )
    def test_bare_id_array_parent_globs_task_sentinels(
        self, scheduler, tmp_path, dep_type, task_rcs, expect_ran
    ):
        """No <id>.rc: every <id>.<n>.rc found must pass the parent's test."""
        for n, rc in enumerate(task_rcs, start=1):
            (tmp_path / f"50.{n}.rc").write_text(f"{rc}\n")
        job = make_job("echo RAN", dependencies=[Dependency("50", dep_type)])
        result = self._run(scheduler.preview(job), tmp_path, {"JOB_ID": "60"})
        assert result.returncode == (0 if expect_ran else 113), result.stderr
        assert ("RAN" in result.stdout) is expect_ran

    def test_bare_id_with_no_sentinel_at_all_skips(self, scheduler, tmp_path):
        job = make_job("echo RAN", dependencies=[Dependency("50")])
        result = self._run(scheduler.preview(job), tmp_path, {"JOB_ID": "60"})
        assert result.returncode == 113
        assert "50.rc" in result.stderr

    def test_interactive_with_dependencies_refused(self, scheduler, tmp_path):
        from hpc_runner.core.exceptions import NotSupportedError

        job = make_job("xterm", interactive=True, dependencies=[Dependency("1")])
        with pytest.raises(NotSupportedError, match="interactive jobs cannot have dependencies"):
            scheduler.preview(job)
        with patch_seam(lambda argv: (0, "", "")) as seam:
            with pytest.raises(NotSupportedError):
                scheduler.submit(job)
        seam.assert_not_called()
        assert list((tmp_path / "scripts").glob("*.sh")) == []

    def test_mixed_types_one_hold_per_parent_guards(self, scheduler):
        job = make_job(
            dependencies=[
                Dependency("1", DependencyType.AFTEROK),
                Dependency("2", DependencyType.AFTERANY),
                Dependency("3", DependencyType.AFTERNOTOK),
            ]
        )
        script = scheduler.preview(job)
        assert "#$ -hold_jid 1,2,3\n" in script
        guard = _guard_section(script)
        assert "_hpc_dep_check_id afterok 1\n" in guard
        assert "_hpc_dep_check_id afternotok 3\n" in guard
        assert guard.index("check_id afterok 1") < guard.index("check_id afternotok 3")
        assert " 2\n" not in guard

    def test_duplicate_parent_held_once(self, scheduler):
        job = make_job(
            dependencies=[Dependency("1"), Dependency("1", DependencyType.AFTERANY)],
        )
        assert "#$ -hold_jid 1\n" in scheduler.preview(job)

    def test_array_parent_lists_every_task_sentinel(self, scheduler):
        job = make_job(dependencies=[Dependency("7", DependencyType.AFTEROK, (1, 3, 5))])
        script = scheduler.preview(job)
        assert "#$ -hold_jid 7\n" in script
        guard = _guard_section(script)
        assert "for _s in 7.1.rc 7.3.rc 7.5.rc ; do" in guard
        assert '_hpc_dep_ok afterok "$_rc" || _hpc_dep_skip "$_s" afterok' in guard
        # Task list known: no glob fallback.
        assert "_hpc_dep_check_id" not in guard

    def test_afterany_only_holds_without_guard(self, scheduler):
        job = make_job(dependencies=[Dependency("1", DependencyType.AFTERANY)])
        script = scheduler.preview(job)
        assert "#$ -hold_jid 1\n" in script
        assert "hpc-runner: publish" in script  # still publishes its own rc
        assert _guard_section(script) == ""

    def test_no_dependencies_has_no_guard(self, scheduler):
        script = scheduler.preview(make_job("echo hi"))
        assert "hpc-runner: publish" in script
        assert "#$ -hold_jid" not in script
        assert _guard_section(script) == ""

    def test_guard_exit_code_from_context(self, scheduler):
        from hpc_runner.core.result import HPC_EXIT_DEP_FAILED

        script = scheduler.preview(make_job(dependencies=[Dependency("1")]))
        assert f"exit {HPC_EXIT_DEP_FAILED}\n" in _guard_section(script)

    @pytest.mark.parametrize(
        "deps", [[Dependency("1")], [Dependency("2", (DependencyType.AFTERNOTOK))]]
    )
    def test_script_parses(self, scheduler, tmp_path, deps):
        import subprocess

        path = tmp_path / "s.sh"
        path.write_text(scheduler.preview(make_job(dependencies=deps)))
        subprocess.run(["bash", "-n", str(path)], check=True)

    def test_after_not_supported(self, scheduler, mock_sge_commands):
        from hpc_runner.core.exceptions import NotSupportedError

        job = make_job(dependencies=[Dependency("1", DependencyType.AFTER)])
        with pytest.raises(NotSupportedError, match="start-after"):
            scheduler.preview(job)
        with pytest.raises(NotSupportedError):
            scheduler.submit(job)
        assert not any(c.args[0][0] == "qsub" for c in mock_sge_commands.call_args_list)

    @pytest.mark.parametrize("dep_type", [DependencyType.AFTEROK, DependencyType.AFTERNOTOK])
    def test_guarded_types_need_emulation(self, monkeypatch, tmp_path, dep_type):
        from hpc_runner.core.exceptions import NotSupportedError

        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path))
        scheduler = SGEScheduler(HPCConfig(schedulers={"sge": {"emulate_afterok": False}}))
        assert scheduler.exit_dir == ""
        with pytest.raises(NotSupportedError, match="emulate_afterok"):
            scheduler.preview(make_job(dependencies=[Dependency("1", dep_type)]))

    def test_afterany_without_emulation(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path))
        scheduler = SGEScheduler(HPCConfig(schedulers={"sge": {"emulate_afterok": False}}))
        script = scheduler.preview(
            make_job(dependencies=[Dependency("1", DependencyType.AFTERANY)])
        )
        assert "#$ -hold_jid 1\n" in script
        assert "hpc-runner: publish" not in script
        assert _guard_section(script) == ""

    @staticmethod
    def _run(script: str, tmp_path, env: dict[str, str]):
        import os
        import subprocess

        script_file = tmp_path / "job.sh"
        script_file.write_text(script)
        return subprocess.run(
            ["bash", str(script_file)],
            capture_output=True,
            text=True,
            env={**os.environ, **env},
        )

    @pytest.mark.parametrize(
        "task_env,name",
        [({"SGE_TASK_ID": "undefined"}, "1.rc"), ({"SGE_TASK_ID": "7"}, "1.7.rc"), ({}, "1.rc")],
    )
    def test_sentinel_name(self, scheduler, tmp_path, task_env, name):
        result = self._run(
            scheduler.preview(make_job("true")), tmp_path, {"JOB_ID": "1", **task_env}
        )
        assert result.returncode == 0, result.stderr
        assert (tmp_path / name).read_text().strip() == "0"
        assert sorted(p.name for p in tmp_path.glob("*.rc")) == [name]

    @pytest.mark.parametrize(
        "dep_type,parent_rcs,expect_ran",
        [
            (DependencyType.AFTEROK, ["0"], True),
            (DependencyType.AFTEROK, ["2"], False),
            (DependencyType.AFTERNOTOK, ["2"], True),
            (DependencyType.AFTERNOTOK, ["0"], False),
        ],
    )
    def test_guard_runtime_behaviour(self, scheduler, tmp_path, dep_type, parent_rcs, expect_ran):
        """End-to-end: render the script and run it under bash.

        A satisfied dependency lets the command run; an unsatisfied one
        skips it with exit 113. Either way the job publishes its own code.
        """
        (tmp_path / "111.rc").write_text(f"{parent_rcs[0]}\n")
        job = make_job("echo SIM-RAN", dependencies=[Dependency("111", dep_type)])
        result = self._run(scheduler.preview(job), tmp_path, {"JOB_ID": "999"})

        expect_exit = 0 if expect_ran else 113
        assert result.returncode == expect_exit, result.stderr
        assert ("SIM-RAN" in result.stdout) is expect_ran
        assert (tmp_path / "999.rc").read_text().strip() == str(expect_exit)
        if not expect_ran:
            assert "111.rc" in result.stderr

    @pytest.mark.parametrize(
        "dep_type,task_rcs,expect_ran",
        [
            (DependencyType.AFTEROK, {1: "0", 2: "0"}, True),
            (DependencyType.AFTEROK, {1: "0", 2: "1"}, False),
            (DependencyType.AFTERNOTOK, {1: "1", 2: "1"}, True),
            (DependencyType.AFTERNOTOK, {1: "1", 2: "0"}, False),
        ],
    )
    def test_array_parent_every_task_must_pass(
        self, scheduler, tmp_path, dep_type, task_rcs, expect_ran
    ):
        for task, rc in task_rcs.items():
            (tmp_path / f"50.{task}.rc").write_text(rc)
        job = make_job("echo RAN", dependencies=[Dependency("50", dep_type, (1, 2))])
        result = self._run(scheduler.preview(job), tmp_path, {"JOB_ID": "60"})
        assert result.returncode == (0 if expect_ran else 113), result.stderr
        assert ("RAN" in result.stdout) is expect_ran


class TestSentinelSweep:
    """Sentinels older than sentinel_ttl_days go at scheduler construction; newer stay."""

    DAY = 86400

    @staticmethod
    def _aged(path, days):
        path.write_text("0\n")
        then = time.time() - days * TestSentinelSweep.DAY
        os.utime(path, (then, then))
        return path

    def test_default_ttl_is_thirty_days(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path))
        old = self._aged(tmp_path / "1.rc", 31)
        recent = self._aged(tmp_path / "2.rc", 29)
        task = self._aged(tmp_path / "3.7.rc", 31)
        other = self._aged(tmp_path / "notes.txt", 400)
        scheduler = SGEScheduler(HPCConfig())
        assert scheduler.sentinel_ttl_days == 30
        assert not old.exists() and not task.exists()
        assert recent.exists() and other.exists()  # only *.rc is swept

    def test_configured_ttl(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path))
        old = self._aged(tmp_path / "1.rc", 2)
        recent = self._aged(tmp_path / "2.rc", 0.5)
        SGEScheduler(HPCConfig(schedulers={"sge": {"sentinel_ttl_days": 1}}))
        assert not old.exists() and recent.exists()

    @pytest.mark.parametrize("ttl", [0, -3])
    def test_ttl_below_one_day_is_a_config_error(self, monkeypatch, tmp_path, ttl):
        from hpc_runner.core.exceptions import ConfigError

        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path))
        with pytest.raises(ConfigError, match="sentinel_ttl_days"):
            SGEScheduler(HPCConfig(schedulers={"sge": {"sentinel_ttl_days": ttl}}))

    def test_no_sweep_without_emulation(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path))
        old = self._aged(tmp_path / "1.rc", 400)
        SGEScheduler(HPCConfig(schedulers={"sge": {"emulate_afterok": False}}))
        assert old.exists()

    def test_sweep_runs_at_construction_not_on_submit(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path))
        scheduler = SGEScheduler(HPCConfig())
        old = self._aged(tmp_path / "1.rc", 400)
        with patch_seam(lambda argv: (0, 'Your job 5 ("x") has been submitted\n', "")):
            scheduler.submit(make_job(workdir=str(tmp_path)))
        assert old.exists()


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

    def test_array_task_reads_only_its_own_sentinel(self, scheduler, tmp_path):
        (tmp_path / "123.rc").write_text("0\n")
        with patch_seam(self._fake_run()):
            assert scheduler.get_status("123.4") == JobStatus.UNKNOWN
        (tmp_path / "123.4.rc").write_text("5\n")
        with patch_seam(self._fake_run()):
            assert scheduler.get_status("123.4") == JobStatus.FAILED
            assert scheduler.get_exit_code("123.4") == 5

    def test_sentinel_113_is_skipped(self, scheduler, tmp_path):
        (tmp_path / "123.rc").write_text("113\n")
        with patch_seam(self._fake_run()):
            assert scheduler.get_status("123") == JobStatus.SKIPPED
            assert scheduler.get_exit_code("123") == 113

    def test_no_sentinel_unknown(self, scheduler):
        with patch_seam(self._fake_run()):
            assert scheduler.get_status("123") == JobStatus.UNKNOWN
            assert scheduler.get_exit_code("123") is None

    def test_unparsable_sentinel_ignored(self, scheduler, tmp_path):
        (tmp_path / "123.rc").write_text("garbage\n")
        with patch_seam(self._fake_run()):
            assert scheduler.get_status("123") == JobStatus.UNKNOWN
            assert scheduler.get_exit_code("123") is None

    def test_emulate_afterok_off_ignores_sentinel(self, scheduler, tmp_path):
        (tmp_path / "123.rc").write_text("0\n")
        scheduler.emulate_afterok = False
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


ARRAY_QSTAT_XML = """<?xml version='1.0'?>
<job_info>
  <queue_info>
    <job_list state="running">
      <JB_job_number>500</JB_job_number>
      <state>r</state>
      <tasks>2</tasks>
    </job_list>
    <job_list state="running">
      <JB_job_number>600</JB_job_number>
      <state>r</state>
    </job_list>
  </queue_info>
  <job_info>
    <job_list state="pending">
      <JB_job_number>500</JB_job_number>
      <state>qw</state>
      <tasks>4-6:1</tasks>
    </job_list>
    <job_list state="pending">
      <JB_job_number>700</JB_job_number>
      <state>hqw</state>
    </job_list>
  </job_info>
</job_info>
"""


class TestSGEStatuses:
    """Batched get_statuses: one qstat per call; qacct/sentinel per finished id."""

    @pytest.fixture
    def scheduler(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path))
        return SGEScheduler(HPCConfig())

    @staticmethod
    def _respond(qacct: dict[str, str] | None = None):
        qacct = qacct or {}

        def respond(cmd):
            if cmd[0] == "qstat":
                assert cmd == ["qstat", "-xml", "-u", "*"]
                return 0, ARRAY_QSTAT_XML, ""
            if cmd[0] == "qacct":
                key = " ".join(cmd[2:])
                if key in qacct:
                    return 0, qacct[key], ""
                return 1, "", "error: job id not found\n"
            raise AssertionError(cmd)

        return respond

    def test_one_qstat_for_many_ids(self, scheduler):
        ids = ["600", "700", "500.2", "500.5"]
        with patch_seam(self._respond()) as seam:
            statuses = scheduler.get_statuses(ids)
        assert statuses == {
            "600": JobStatus.RUNNING,
            "700": JobStatus.PENDING,
            "500.2": JobStatus.RUNNING,
            "500.5": JobStatus.PENDING,
        }
        assert [c.args[0][0] for c in seam.call_args_list] == ["qstat"]

    def test_finished_task_from_qacct_per_task(self, scheduler):
        qacct = {"500 -t 1": "jobnumber 500\nfailed 0\nexit_status 3\n"}
        with patch_seam(self._respond(qacct)) as seam:
            statuses = scheduler.get_statuses(["500.1", "500.4"])
        assert statuses == {"500.1": JobStatus.FAILED, "500.4": JobStatus.PENDING}
        argvs = [c.args[0] for c in seam.call_args_list]
        assert argvs == [["qstat", "-xml", "-u", "*"], ["qacct", "-j", "500", "-t", "1"]]

    def test_base_id_of_array_prefers_running_task(self, scheduler):
        with patch_seam(self._respond()):
            assert scheduler.get_status("500") == JobStatus.RUNNING

    def test_task_status_from_array_result(self, scheduler):
        from hpc_runner.core.array import ArrayRange
        from hpc_runner.core.result import ArrayJobResult

        res = ArrayJobResult(scheduler=scheduler, job_id="500", array=ArrayRange(1, 6))
        with patch_seam(self._respond()):
            assert res.task_status(2) == JobStatus.RUNNING
            assert res.task_status(6) == JobStatus.PENDING
            assert res.task_status(1) == JobStatus.UNKNOWN

    def test_qacct_113_is_skipped(self, scheduler):
        qacct = {"800": "jobnumber 800\nfailed 0\nexit_status 113\n"}
        with patch_seam(self._respond(qacct)):
            assert scheduler.get_status("800") == JobStatus.SKIPPED
            assert scheduler.get_exit_code("800") == 113

    def test_qacct_failed_37_is_timeout(self, scheduler):
        qacct = {
            "800": "jobnumber 800\n"
            "failed       37  : qmaster enforced h_rt, h_cpu, or h_vmem limit\n"
            "exit_status  0\n"
        }
        with patch_seam(self._respond(qacct)):
            assert scheduler.get_status("800") == JobStatus.TIMEOUT

    def test_execd_h_rt_kill_stays_failed(self, scheduler):
        qacct = {"800": "jobnumber 800\nfailed 100 : assumedly after job\nexit_status 137\n"}
        with patch_seam(self._respond(qacct)):
            assert scheduler.get_status("800") == JobStatus.FAILED


class TestSGEParserStates:
    def test_task_states(self):
        from hpc_runner.schedulers.sge.parser import parse_qstat_xml_states

        assert parse_qstat_xml_states(ARRAY_QSTAT_XML) == {
            "500.2": "r",
            "600": "r",
            "500.4": "qw",
            "500.5": "qw",
            "500.6": "qw",
            "700": "hqw",
        }

    def test_task_list_forms(self):
        from hpc_runner.schedulers.sge.parser import _expand_task_list

        assert _expand_task_list("3") == [3]
        assert _expand_task_list("1-7:3") == [1, 4, 7]
        assert _expand_task_list("1,5-6") == [1, 5, 6]
        assert _expand_task_list("junk") == []

    def test_bad_xml(self):
        from hpc_runner.schedulers.sge.parser import parse_qstat_xml_states

        assert parse_qstat_xml_states("<not xml") == {}

    def test_qacct_to_job_info_skipped_and_timeout(self):
        from hpc_runner.schedulers.sge.parser import qacct_to_job_info

        skipped = qacct_to_job_info({"jobnumber": "1", "failed": "0", "exit_status": "113"})
        assert skipped.status == JobStatus.SKIPPED
        assert skipped.exit_code == 113
        timeout = qacct_to_job_info(
            {"jobnumber": "2", "failed": "37  : qmaster", "exit_status": "0"}
        )
        assert timeout.status == JobStatus.TIMEOUT


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
        job = make_job(
            "echo hello", name="j", cpu=2, dependencies=[Dependency("5")], workdir=str(tmp_path)
        )
        scheduler.submit(job, keep_script=True)
        written = list((tmp_path / "scripts").glob("hpc_batch_*.sh"))
        assert len(written) == 1
        assert written[0].read_text() == scheduler.preview(job)

    def test_script_deleted_without_keep_script(self, scheduler, tmp_path, mock_sge_commands):
        scheduler.submit(make_job(workdir=str(tmp_path)))
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
        assert "_hpc_dep_check_id afterok 1\n" in script
        assert "_hpc_dep_check_id afterany" not in script

    def test_array_job(self, scheduler, mock_sge_commands, sge_fixtures, tmp_path):
        from hpc_runner.core.result import ArrayJobResult

        job = make_job(array="1-10:2%3", workdir=str(tmp_path))
        script = scheduler.preview(job)
        # SGE has no %N suffix: the range is -t, the concurrency limit is -tc.
        assert "#$ -t 1-10:2\n" in script
        assert "#$ -tc 3\n" in script
        assert "%3" not in script
        result = scheduler.submit(job)
        assert isinstance(result, ArrayJobResult)
        # qsub answered with its job-array line because the script has #$ -t.
        assert result.job_id == sge_fixtures.submitted_array_id
        assert result.array.count == 5

    def test_array_job_without_limit_has_no_tc(self, scheduler):
        script = scheduler.preview(make_job(array="1-4"))
        assert "#$ -t 1-4\n" in script
        assert "-tc" not in script

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
        assert argv[:3] == ["qrsh", "-now", "no"]
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

    def test_result_handle_without_job(self, scheduler, mock_sge_commands, sge_fixtures):
        job_id = sge_fixtures.job("running")
        handle = scheduler.result(job_id)
        assert handle.job is None
        assert handle.job_id == job_id
        assert handle.status == JobStatus.RUNNING


QSUB_OK = (0, 'Your job 4242 ("x") has been submitted\n', "")
QSUB_ARRAY_OK = (0, 'Your job-array 4242.1-4:1 ("x") has been submitted\n', "")


def _directives(script: str) -> list[str]:
    return [line for line in script.splitlines() if line.startswith("#$ ")]


class TestSGEOutputPaths:
    """D9: -o/-e are always rendered and the result records where they go."""

    @pytest.fixture
    def scheduler(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HPC_SCRIPT_DIR", str(tmp_path / "scripts"))
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path / "exit"))
        return SGEScheduler(HPCConfig())

    def test_default_pattern(self, scheduler):
        directives = _directives(scheduler.preview(make_job()))
        assert "#$ -o /work/$JOB_NAME.$JOB_ID.out" in directives
        assert "#$ -j y" in directives
        assert not any(d.startswith("#$ -e") for d in directives)

    def test_array_appends_task_id(self, scheduler):
        directives = _directives(scheduler.preview(make_job(array="1-4", stderr="e.log")))
        assert "#$ -o /work/$JOB_NAME.$JOB_ID.out.$TASK_ID" in directives
        assert "#$ -e /work/e.log.$TASK_ID" in directives

    def test_array_pattern_with_task_id_kept(self, scheduler):
        job = make_job(array="1-4", stdout="t/${hpc.job_id}.${hpc.task_id}")
        assert "#$ -o /work/t/$JOB_ID.$TASK_ID" in _directives(scheduler.preview(job))

    def test_non_array_has_no_task_suffix(self, scheduler):
        assert "$TASK_ID" not in scheduler.preview(make_job())

    def test_separate_stderr(self, scheduler):
        directives = _directives(scheduler.preview(make_job(stderr="${hpc.job_name}.err")))
        assert "#$ -e /work/$JOB_NAME.err" in directives
        assert "#$ -j y" not in directives

    @pytest.mark.parametrize("use_cwd", [True, False])
    def test_absolute_regardless_of_use_cwd(self, scheduler, use_cwd):
        job = make_job(use_cwd=use_cwd, workdir="/proj/run", stdout="o/${hpc.job_id}.out")
        assert "#$ -o /proj/run/o/$JOB_ID.out" in _directives(scheduler.preview(job))

    def test_absolute_pattern_kept(self, scheduler):
        job = make_job(stdout="/logs/${hpc.job_id}.out")
        assert "#$ -o /logs/$JOB_ID.out" in _directives(scheduler.preview(job))

    def test_host_renders_hostname(self, scheduler):
        job = make_job(stdout="${hpc.host}.out")
        assert "#$ -o /work/$HOSTNAME.out" in _directives(scheduler.preview(job))

    def test_submit_records_paths(self, scheduler, tmp_path):
        job = make_job("echo hi", name="9 lives", workdir=str(tmp_path), stderr="e/${hpc.job_id}")
        with patch_seam(lambda argv: QSUB_OK):
            result = scheduler.submit(job)
        assert result.stdout_path == tmp_path / "j9_lives.4242.out"
        assert result.stderr_path == tmp_path / "e" / "4242"
        assert result.read_stdout() is None  # not written yet

    def test_read_stdout_after_job_writes(self, scheduler, tmp_path):
        job = make_job("echo hi", name="n", workdir=str(tmp_path))
        with patch_seam(lambda argv: QSUB_OK):
            result = scheduler.submit(job)
        (tmp_path / "n.4242.out").write_text("hi\n")
        assert result.read_stdout() == "hi\n"

    def test_array_task_paths(self, scheduler, tmp_path):
        job = make_job(name="a", array="1-4", workdir=str(tmp_path))
        with patch_seam(lambda argv: QSUB_ARRAY_OK):
            result = scheduler.submit(job)
        assert result.task_stdout_path(3) == tmp_path / "a.4242.out.3"
        assert result.task_stderr_path(3) is None

    def test_directory_created_before_qsub(self, scheduler, tmp_path):
        seen = []

        def respond(argv):
            seen.append((tmp_path / "logs" / "n").is_dir())
            return QSUB_OK

        job = make_job(name="n", workdir=str(tmp_path), stdout="logs/n/${hpc.job_name}.out")
        with patch_seam(respond):
            scheduler.submit(job)
        assert seen == [True]

    def test_host_in_pattern_unknown_path(self, scheduler, tmp_path):
        job = make_job(workdir=str(tmp_path), stdout="out.${hpc.host}")
        with patch_seam(lambda argv: QSUB_OK):
            result = scheduler.submit(job)
        assert result.stdout_path is None
        with pytest.raises(ValueError, match="not known at submit time"):
            result.read_stdout()

    def test_qsub_runs_in_workdir(self, scheduler, tmp_path):
        with patch_seam(lambda argv: QSUB_OK) as seam:
            scheduler.submit(make_job(workdir=str(tmp_path)))
        assert seam.call_args.kwargs["cwd"] == tmp_path

    def test_interactive_has_no_paths(self, scheduler, tmp_path):
        job = make_job("xterm", interactive=True, workdir=str(tmp_path), stdout="logs/x")
        with patch_seam(lambda argv: (0, "", "")):
            result = scheduler.submit(job)
        assert (result.stdout_path, result.stderr_path) == (None, None)
        assert result.read_stdout() is None
        assert not (tmp_path / "logs").exists()


class TestSGEJobName:
    @pytest.mark.parametrize(
        ("name", "rendered"),
        [
            ("alice_sim", "alice_sim"),
            ("9lives", "j9lives"),
            ("my job", "my_job"),
            ("a/b", "a_b"),
            ("a\tb:c@d", "a_b_c_d"),
            ("3 bad/name", "j3_bad_name"),
        ],
    )
    def test_sanitised(self, name, rendered):
        script = SGEScheduler(HPCConfig()).preview(make_job(name=name))
        assert f"#$ -N {rendered}" in _directives(script)


class TestSGEUnits:
    """D10: mem is per job; the site says how its resource is consumed."""

    def _script(self, job, **sge):
        return SGEScheduler(HPCConfig(schedulers={"sge": sge})).preview(job)

    def test_per_job_is_the_default(self):
        assert "#$ -l mem_free=16G" in self._script(make_job(cpu=4, mem="16G"))

    def test_per_job_not_divided(self):
        script = self._script(make_job(cpu=4, mem="16G"), memory_per="job")
        assert "#$ -l mem_free=16G" in script

    def test_per_slot_divides_by_cpu(self):
        script = self._script(make_job(cpu=4, mem="16G"), memory_per="slot")
        assert "#$ -l mem_free=4G" in script

    def test_per_slot_rounds_up(self):
        script = self._script(make_job(cpu=3, mem="1G"), memory_per="slot")
        assert "#$ -l mem_free=342M" in script

    def test_per_slot_single_cpu_unchanged(self):
        assert "#$ -l mem_free=16G" in self._script(make_job(mem="16G"), memory_per="slot")

    def test_bad_memory_per(self):
        from hpc_runner.core.exceptions import ConfigError

        with pytest.raises(ConfigError, match="memory_per: expected one of 'slot', 'job'"):
            SGEScheduler(HPCConfig(schedulers={"sge": {"memory_per": "node"}}))

    def test_qrsh_gets_divided_mem(self):
        cmd = SGEScheduler(
            HPCConfig(schedulers={"sge": {"memory_per": "slot"}})
        )._build_qrsh_command(make_job(cpu=2, mem="8G", interactive=True), "/s.sh")
        assert cmd[cmd.index("-l") + 1] == "mem_free=4G"

    def test_time_rendered_h_rt(self):
        assert "#$ -l h_rt=4:00:00" in self._script(make_job(time="4h"))
        assert "#$ -l h_rt=48:00:00" in self._script(make_job(time="2d"))
