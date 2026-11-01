"""The SGE parsers against output captured from the testbed.

Every text here is in tests/unit/fixtures/sge/ (``hpc-testbed capture``,
tests/integration/capture.py); ids come from its manifest. These tests pin
what gridengine 8.1.9 emits, where the hand-written ones in test_sge.py pin
what the parsers accept.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hpc_runner.core.config import HPCConfig
from hpc_runner.core.exceptions import JobNotFoundError
from hpc_runner.core.result import JobStatus
from hpc_runner.schedulers.sge import SGEScheduler
from hpc_runner.schedulers.sge.parser import (
    _leading_int,
    parse_qacct_output,
    parse_qacct_records,
    parse_qstat_j_xml,
    parse_qstat_plain,
    parse_qstat_xml,
    parse_qstat_xml_states,
    parse_qsub_output,
    qacct_to_job_info,
    state_to_status,
)
from tests.fakes import SGEFixtures, make_sge_responder, patch_seam

FX = SGEFixtures()

#: Every captured finished job: (scenario, status, exit code, qacct failed code).
FINISHED = [
    ("ok", JobStatus.COMPLETED, 0, 0),
    ("exit1", JobStatus.FAILED, 1, 0),
    ("signal", JobStatus.FAILED, 137, 100),
    ("lost_node", JobStatus.FAILED, 0, 19),
    ("timeout", JobStatus.TIMEOUT, 137, 37),
    ("vmem", JobStatus.FAILED, 2, 0),
    ("requeued", JobStatus.FAILED, 137, 100),
    ("skipped", JobStatus.SKIPPED, 113, 0),
]

#: The qacct files behind FINISHED, plus the three-task array (task 3 failed).
FINISHED_RECORDS = [
    "qacct_ok.txt",
    "qacct_exit1.txt",
    "qacct_signal.txt",
    "qacct_lost_node.txt",
    "qacct_timeout.txt",
    "qacct_vmem.txt",
    "qacct_requeued.txt",
    "qacct_skipped.txt",
    "qacct_array_base.txt",
]


def test_manifest_names_every_scenario_and_file():
    assert set(FX.jobs) >= {
        "ok",
        "exit1",
        "array_done",
        "bob",
        "signal",
        "lost_node",
        "running",
        "held",
        "eqw",
        "array",
        "timeout",
        "vmem",
        "deleted_pending",
        "suspended",
        "queue_suspended",
        "requeued",
        "deleting",
        "threshold",
        "requeue_pending",
        "deleting_transfer",
        "skipped",
        "dependent",
    }
    assert all(v.isdigit() for v in FX.jobs.values())
    for name, entry in FX.manifest["files"].items():
        assert (FX.root / name).is_file(), name
        assert entry["argv"][0] in {"qsub", "qstat", "qacct", "qdel"}, name
    assert FX.manifest["scheduler"].startswith("SGE")


class TestQsub:
    def test_plain_and_array(self):
        assert parse_qsub_output(FX.text("qsub.txt")) == FX.submitted_id
        assert parse_qsub_output(FX.text("qsub_array.txt")) == FX.submitted_array_id
        assert "job-array" in FX.text("qsub_array.txt")


class TestQstatMixed:
    """Running, held, Eqw and an array with running and queued tasks, one snapshot."""

    def test_states_per_job_and_task(self):
        states = parse_qstat_xml_states(FX.text("qstat_mixed.xml"))
        array = FX.job("array")
        assert states[FX.job("running")] == "r"
        assert states[FX.job("held")] == "hqw"
        assert states[FX.job("dependent")] == "hqw"  # -hold_jid looks like a user hold
        assert states[FX.job("eqw")] == "Eqw"
        assert array not in states  # an array is known by its tasks only
        task_states = {k: v for k, v in states.items() if k.startswith(f"{array}.")}
        assert len(task_states) == 10
        # Running tasks show as `r` or `t` (transferring); the queued tail is `qw`.
        statuses = {k: state_to_status(v) for k, v in task_states.items()}
        assert set(statuses.values()) == {JobStatus.RUNNING, JobStatus.PENDING}
        assert statuses[f"{array}.1"] is JobStatus.RUNNING
        assert statuses[f"{array}.10"] is JobStatus.PENDING
        assert task_states[f"{array}.10"] == "qw"

    def test_state_codes_map(self):
        assert state_to_status("r") is JobStatus.RUNNING
        assert state_to_status("t") is JobStatus.RUNNING
        assert state_to_status("hqw") is JobStatus.PENDING
        assert state_to_status("qw") is JobStatus.PENDING
        assert state_to_status("Eqw") is JobStatus.FAILED

    @pytest.mark.parametrize("code", ["s", "ts", "S", "tS", "T", "tT"])
    def test_suspended_codes_are_suspended_not_running_or_pending(self, code):
        # ``t`` alone is transferring (RUNNING); with a suspend flag the
        # suspend flag wins, so a suspended-while-transferring task is not
        # reported as running.
        assert state_to_status(code) is JobStatus.SUSPENDED
        assert not JobStatus.SUSPENDED.is_terminal

    @pytest.mark.parametrize("code", ["dr", "dt", "ds", "dS"])
    def test_deleting_wins_over_every_other_flag(self, code):
        assert state_to_status(code) is JobStatus.CANCELLED

    def test_restarted_codes(self):
        assert state_to_status("Rr") is JobStatus.RUNNING
        assert state_to_status("Rq") is JobStatus.PENDING

    def test_one_entry_per_id_running_array_wins(self):
        jobs = parse_qstat_xml(FX.text("qstat_mixed.xml"))
        assert set(jobs) == {FX.job(s) for s in ("running", "held", "dependent", "eqw", "array")}
        array = jobs[FX.job("array")]
        assert state_to_status(array["state"]) is JobStatus.RUNNING
        assert array["array_task_id"] == "1"
        running = jobs[FX.job("running")]
        assert (running["queue"], running["slots"]) == ("all.q", 1)
        assert running["node"] in {"exec1", "exec2"}
        assert "start_time" in running and "submit_time" not in running
        held = jobs[FX.job("held")]
        assert "queue" not in held and "submit_time" in held

    def test_plain_listing_agrees_with_the_xml(self):
        plain = parse_qstat_plain(FX.text("qstat_mixed.txt"))
        xml = parse_qstat_xml(FX.text("qstat_mixed.xml"))
        assert set(plain) == set(xml)
        for job_id, entry in plain.items():
            assert entry["user"] == xml[job_id]["user"]
            assert entry["name"] == xml[job_id]["name"]
        assert plain[FX.job("held")]["state"] == "hqw"

    def test_empty_queue(self):
        assert parse_qstat_xml(FX.text("qstat_empty.xml")) == {}
        assert parse_qstat_xml_states(FX.text("qstat_empty.xml")) == {}


class TestQstatJ:
    def test_running_job_details(self):
        data = parse_qstat_j_xml(FX.text("qstat_j_running.xml"))
        assert data["name"] == "running" and data["user"] == "tester"
        assert data["merge"] is True
        assert data["stdout_path"] == data["stderr_path"] == Path("/dev/null")
        assert data["script_file"] == "sleep" and data["job_args"] == ["600"]
        assert isinstance(data["submit_time"], int) and isinstance(data["start_time"], int)

    def test_unknown_id_is_not_found(self, monkeypatch, tmp_path):
        rc, stdout, _ = FX.result("qstat_j_missing.txt")
        assert rc == 0 and "<unknown_jobs" in stdout  # qstat -j exits 0 for an unknown id
        assert parse_qstat_j_xml(stdout) == {}
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path))
        with patch_seam(make_sge_responder(FX)), pytest.raises(JobNotFoundError):
            SGEScheduler(HPCConfig()).get_job_details("999999")

    def test_get_job_details_of_a_finished_job_comes_from_qacct(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path))
        with patch_seam(make_sge_responder(FX)):
            info, extra = SGEScheduler(HPCConfig()).get_job_details(FX.job("signal"))
        assert extra == {}
        assert (info.status, info.exit_code) == (JobStatus.FAILED, 137)
        assert (info.failed_code, info.failed_reason) == (100, "assumedly after job")
        assert info.name == "signal" and info.user == "tester"

    def test_get_job_details_merges_listing_and_details(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path))
        with patch_seam(make_sge_responder(FX)):
            info, extra = SGEScheduler(HPCConfig()).get_job_details(FX.job("running"))
        assert info.status is JobStatus.RUNNING
        assert info.queue == "all.q" and info.node in {"exec1", "exec2"}
        assert info.stdout_path == Path("/dev/null")
        assert extra["job_args"] == ["600"]
        assert "pe_name" not in extra  # qsub -b y sleep 600: no parallel environment


class TestQacct:
    @pytest.mark.parametrize(
        ("name", "status", "exit_code", "failed"),
        [
            ("qacct_ok.txt", JobStatus.COMPLETED, 0, 0),
            ("qacct_exit1.txt", JobStatus.FAILED, 1, 0),
            ("qacct_signal.txt", JobStatus.FAILED, 137, 100),
            ("qacct_lost_node.txt", JobStatus.FAILED, 0, 19),
            ("qacct_array_task.txt", JobStatus.COMPLETED, 0, 0),
            ("qacct_timeout.txt", JobStatus.TIMEOUT, 137, 37),
            # h_vmem is a setrlimit here: the allocation fails inside the
            # process, which exits by itself; no execd kill, failed 0.
            ("qacct_vmem.txt", JobStatus.FAILED, 2, 0),
            ("qacct_skipped.txt", JobStatus.SKIPPED, 113, 0),
        ],
    )
    def test_records(self, name, status, exit_code, failed):
        record = parse_qacct_output(FX.text(name))
        info = qacct_to_job_info(record)
        assert (info.status, info.exit_code, info.failed_code) == (status, exit_code, failed)
        assert info.user in {"tester", "bob"}
        assert info.queue in {"all.q", "short.q"} and info.node in {"exec1", "exec2"}
        assert info.cpu == 1
        # ru_wallclock is "7s" style on 8.1.9 (older releases print a bare float).
        assert info.runtime is not None and info.runtime.total_seconds() < 120

    def test_array_task_record_names_its_task(self):
        record = parse_qacct_output(FX.text("qacct_array_task.txt"))
        assert record["jobnumber"] == FX.job("array_done") and record["taskid"] == "2"

    def test_array_base_output_is_one_record_per_task(self):
        """``qacct -j <base>`` of the three-task array: three records, task 3 exited 1."""
        records = parse_qacct_records(FX.text("qacct_array_base.txt"))
        by_task = {r["taskid"]: r for r in records}
        assert set(by_task) == {"1", "2", "3"}
        assert all(r["jobnumber"] == FX.job("array_done") for r in records)
        assert [_leading_int(by_task[t]["exit_status"]) for t in ("1", "2", "3")] == [0, 0, 1]
        # The single-record parser folds them into whichever came last: never
        # feed it whole stdout (LIFECYCLE_MATRIX row 27).
        folded = parse_qacct_output(FX.text("qacct_array_base.txt"))
        assert folded["taskid"] == records[-1]["taskid"]

    def test_unknown_id(self):
        rc, stdout, stderr = FX.result("qacct_missing.txt")
        assert rc == 1 and stdout == "" and "not found" in stderr

    def test_completed_listing_places_a_lost_job_by_its_submit_time(self, monkeypatch, tmp_path):
        """A ``failed 19`` record has no start or end time; it must not sort last."""
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path))
        lost, ok = FX.text("qacct_lost_node.txt"), FX.text("qacct_ok.txt")
        assert "end_time     -/-" in lost

        def respond(argv):
            assert argv == ["qacct", "-j", "-o", "tester"]
            return 0, ok + lost, ""

        with patch_seam(respond):
            jobs = SGEScheduler(HPCConfig()).list_completed_jobs(user="tester", limit=1)
        assert [j.job_id for j in jobs] == [FX.job("lost_node")]  # submitted after `ok`
        assert jobs[0].end_time is None and jobs[0].submit_time is not None
        assert (jobs[0].failed_code, jobs[0].status) == (19, JobStatus.FAILED)

    def test_owner_listing(self):
        """``qacct -j -o bob -b <since>``: bob's records, then the owner-summary block.

        The block is a header, a rule and one row per owner. The header
        follows the last record with no separator and lands in it as an
        ``OWNER`` key, harmlessly; the row after the rule is a block of its
        own with no ``jobnumber`` and must not come back as a job (BUG-T2).
        """
        text = FX.text("qacct_owner_bob.txt")
        summary = text.rstrip().rsplit("\n", 1)[-1]
        assert summary.startswith("bob "), summary  # the row BUG-T2 tripped on
        records = parse_qacct_records(text)
        assert records and all(r.get("owner") == "bob" for r in records)
        assert all(r["jobnumber"].isdigit() for r in records)
        assert "OWNER" in records[-1]
        jobs = [qacct_to_job_info(r) for r in records]
        assert all(j.job_id and j.status is not JobStatus.UNKNOWN for j in jobs)


def test_responder_refuses_what_was_not_captured():
    with pytest.raises(AssertionError, match="no captured answer"):
        make_sge_responder(FX)(["qhost"])


class TestFinishedArrayBaseId:
    """The base id of a finished array answers for the array as a whole."""

    @pytest.fixture
    def scheduler(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path))
        return SGEScheduler(HPCConfig())

    def test_one_failed_task_fails_the_array(self, scheduler):
        base = FX.job("array_done")
        with patch_seam(make_sge_responder(FX)):
            assert scheduler.get_status(base) is JobStatus.FAILED
            assert scheduler.get_exit_code(base) == 1
            # Each task on its own.
            assert scheduler.get_status(f"{base}.2") is JobStatus.COMPLETED

    def test_array_handle_status_and_drill_down(self, scheduler):
        from hpc_runner.core.array import ArrayRange
        from hpc_runner.core.result import ArrayJobResult

        base = FX.job("array_done")
        res = ArrayJobResult(scheduler=scheduler, job_id=base, array=ArrayRange(1, 3))
        # Per-task qacct (-t n) is only captured for task 2; the others are UNKNOWN
        # here, which is enough to pin the shape: the whole is not COMPLETED and
        # task 2 is.
        with patch_seam(make_sge_responder(FX)):
            tasks = res.task_statuses()
        assert tasks.tasks[2] is JobStatus.COMPLETED
        assert tasks.overall is not JobStatus.COMPLETED

    def test_details_give_the_whole_and_every_task(self, scheduler):
        base = FX.job("array_done")
        with patch_seam(make_sge_responder(FX)):
            info, extra = scheduler.get_job_details(base)
        assert (info.job_id, info.status, info.exit_code) == (base, JobStatus.FAILED, 1)
        assert info.name == "array_done" and info.user == "tester"
        tasks = extra["tasks"]
        assert [t.array_task_id for t in tasks] == [1, 2, 3]
        assert [t.status for t in tasks] == [
            JobStatus.COMPLETED,
            JobStatus.COMPLETED,
            JobStatus.FAILED,
        ]
        assert [t.exit_code for t in tasks] == [0, 0, 1]
        assert all(t.node in {"exec1", "exec2"} for t in tasks)

    def test_hpc_status_prints_the_task_table(self, scheduler, monkeypatch):
        from click.testing import CliRunner

        from hpc_runner.cli.main import cli

        base = FX.job("array_done")
        monkeypatch.setenv("HPC_SCHEDULER", "sge")
        with patch_seam(make_sge_responder(FX)):
            result = CliRunner().invoke(cli, ["status", base])
        assert result.exit_code == 0, result.output
        assert f"Array job {base}" in result.output
        assert "Tasks" in result.output
        assert result.output.count("COMPLETED") == 2 and result.output.count("FAILED") == 2

    def test_hpc_status_json_lists_the_tasks(self, scheduler, monkeypatch):
        import json

        from click.testing import CliRunner

        from hpc_runner.cli.main import cli

        base = FX.job("array_done")
        monkeypatch.setenv("HPC_SCHEDULER", "sge")
        with patch_seam(make_sge_responder(FX)):
            result = CliRunner().invoke(cli, ["status", base, "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["status"] == "FAILED" and data["exit_code"] == 1
        assert [t["array_task_id"] for t in data["tasks"]] == [1, 2, 3]
        assert [t["exit_code"] for t in data["tasks"]] == [0, 0, 1]


class TestFinishedJobDetails:
    """``get_job_details`` of every captured finished job comes from its qacct record."""

    @pytest.mark.parametrize(("scenario", "status", "exit_code", "failed"), FINISHED)
    def test_details(self, monkeypatch, tmp_path, scenario, status, exit_code, failed):
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path))
        with patch_seam(make_sge_responder(FX)):
            info, extra = SGEScheduler(HPCConfig()).get_job_details(FX.job(scenario))
        assert extra == {}
        assert (info.status, info.exit_code, info.failed_code) == (status, exit_code, failed)
        assert info.job_id == FX.job(scenario) and info.array_task_id is None


def _with_snapshot(name: str, details: dict[str, str] | None = None):
    """The captured responder, but ``qstat -xml -u '*'`` answers with *name*.

    *details* maps a job id to the ``qstat -j`` file to answer for it, for a
    job captured in more than one state.
    """
    base = make_sge_responder(FX)

    def respond(argv: list[str]) -> tuple[int, str, str]:
        if argv == ["qstat", "-xml", "-u", "*"]:
            return 0, FX.text(name), ""
        if details and argv[:2] == ["qstat", "-j"] and argv[2] in details:
            return 0, FX.text(details[argv[2]]), ""
        return base(argv)

    return respond


class TestLifecycleStates:
    """The states the lifecycle matrix had no capture for (rows 7-10, 15, 16, 18, 19).

    Every active state the testbed can produce is in one of four snapshots:
    ``qstat_mixed`` (``r``, ``hqw``, ``Eqw``; the array's ``t``/``qw`` are
    TestQstatMixed's), ``qstat_lifecycle`` (``s``, ``S``, ``Rr``),
    ``qstat_deleting`` (``dr``) and ``qstat_transient`` (``T``, ``Rq``,
    ``dt``). ``SNAPSHOTS`` maps each to the status every reader must give;
    ``hpc status`` (column G) and ``wait()`` (column J) are checked over all
    of them.
    """

    SNAPSHOTS = {
        "qstat_mixed.xml": {
            "running": JobStatus.RUNNING,
            "held": JobStatus.PENDING,
            "dependent": JobStatus.PENDING,
            "eqw": JobStatus.FAILED,
            "array": JobStatus.RUNNING,  # its r, t and qw tasks reduced (rows 1, 6, 24, 25)
        },
        "qstat_lifecycle.xml": {
            "suspended": JobStatus.SUSPENDED,
            "queue_suspended": JobStatus.SUSPENDED,
            "requeued": JobStatus.RUNNING,
        },
        "qstat_deleting.xml": {
            "deleting": JobStatus.CANCELLED,
            "suspended": JobStatus.SUSPENDED,
        },
        "qstat_transient.xml": {
            "threshold": JobStatus.SUSPENDED,
            "requeue_pending": JobStatus.PENDING,
            "deleting_transfer": JobStatus.CANCELLED,
        },
    }

    @pytest.fixture
    def scheduler(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path))
        return SGEScheduler(HPCConfig())

    def test_suspended_and_requeued_snapshot(self, scheduler):
        susp, qsusp, requeued = (FX.job(s) for s in ("suspended", "queue_suspended", "requeued"))
        states = parse_qstat_xml_states(FX.text("qstat_lifecycle.xml"))
        assert (states[susp], states[qsusp], states[requeued]) == ("s", "S", "Rr")
        with patch_seam(_with_snapshot("qstat_lifecycle.xml")):
            statuses = scheduler.get_statuses([susp, qsusp, requeued])
            listed = {j.job_id: j for j in scheduler.list_active_jobs()}
        assert statuses == {
            susp: JobStatus.SUSPENDED,
            qsusp: JobStatus.SUSPENDED,
            requeued: JobStatus.RUNNING,
        }
        assert listed[susp].status is JobStatus.SUSPENDED and listed[susp].is_active
        assert listed[qsusp].node == "exec2" and listed[requeued].node == "exec1"

    def test_plain_listing_of_the_lifecycle_snapshot(self):
        plain = parse_qstat_plain(FX.text("qstat_lifecycle.txt"))
        assert plain[FX.job("suspended")]["state"] == "s"
        assert plain[FX.job("queue_suspended")]["state"] == "S"
        assert plain[FX.job("requeued")]["state"] == "Rr"

    def test_deleting_is_cancelled_until_it_leaves_qstat(self, scheduler):
        deleting = FX.job("deleting")
        assert parse_qstat_xml_states(FX.text("qstat_deleting.xml"))[deleting] == "dr"
        with patch_seam(_with_snapshot("qstat_deleting.xml")):
            assert scheduler.get_status(deleting) is JobStatus.CANCELLED
            assert scheduler.get_status(FX.job("suspended")) is JobStatus.SUSPENDED

    def test_qdel_answers_are_both_success(self, scheduler):
        """``qdel`` of a job its execd cannot reach only registers the deletion."""
        assert "registered the job" in FX.text("qdel_deleting.txt")
        assert "has deleted job" in FX.text("qdel_pending.txt")
        with patch_seam(make_sge_responder(FX)):
            assert scheduler.cancel(FX.job("deleting")) is True
            assert scheduler.cancel(FX.job("deleted_pending")) is True

    def test_deleted_while_pending_leaves_no_record(self, scheduler):
        """Accepted (matrix §4.2): no qacct record, no sentinel, so UNKNOWN."""
        rc, stdout, stderr = FX.result("qacct_deleted_pending.txt")
        assert rc == 1 and stdout == "" and "not found" in stderr
        job_id = FX.job("deleted_pending")
        with patch_seam(make_sge_responder(FX)):
            assert scheduler.get_status(job_id) is JobStatus.UNKNOWN
            assert scheduler.get_exit_code(job_id) is None
            with pytest.raises(JobNotFoundError):
                scheduler.get_job_details(job_id)

    def test_requeued_job_reads_its_final_record(self, scheduler):
        records = parse_qacct_records(FX.text("qacct_requeued.txt"))
        assert [_leading_int(r["failed"]) for r in records] == [25, 100]
        assert records[0]["failed"].endswith("rescheduling")
        job_id = FX.job("requeued")
        with patch_seam(make_sge_responder(FX)):
            assert scheduler.get_status(job_id) is JobStatus.FAILED
            assert scheduler.get_exit_code(job_id) == 137

    def test_transient_snapshot(self, scheduler):
        threshold, rq, dt = (
            FX.job(s) for s in ("threshold", "requeue_pending", "deleting_transfer")
        )
        states = parse_qstat_xml_states(FX.text("qstat_transient.xml"))
        assert (states[threshold], states[rq], states[dt]) == ("T", "Rq", "dt")
        plain = parse_qstat_plain(FX.text("qstat_transient.txt"))
        assert [plain[j]["state"] for j in (threshold, rq, dt)] == ["T", "Rq", "dt"]
        with patch_seam(_with_snapshot("qstat_transient.xml")):
            listed = {j.job_id: j for j in scheduler.list_active_jobs()}
        assert listed[threshold].queue == "short.q" and listed[threshold].node == "exec1"
        assert listed[rq].node is None and listed[dt].node == "exec2"

    @pytest.mark.parametrize("snapshot", sorted(SNAPSHOTS))
    def test_every_reader_agrees(self, scheduler, snapshot):
        expected = {FX.job(s): st for s, st in self.SNAPSHOTS[snapshot].items()}
        with patch_seam(_with_snapshot(snapshot)):
            batched = scheduler.get_statuses(list(expected))
            single = {j: scheduler.get_status(j) for j in expected}
            listed = {j.job_id: j.status for j in scheduler.list_active_jobs()}
        assert batched == expected and single == expected
        assert {j: listed[j] for j in expected} == expected

    @pytest.mark.parametrize("snapshot", sorted(SNAPSHOTS))
    def test_hpc_status_table(self, monkeypatch, snapshot):
        """Column G: the active table (``--json`` for the exact field, then the table)."""
        import json

        from click.testing import CliRunner

        from hpc_runner.cli.main import cli

        expected = {FX.job(s): st.name for s, st in self.SNAPSHOTS[snapshot].items()}
        monkeypatch.setenv("HPC_SCHEDULER", "sge")
        with patch_seam(_with_snapshot(snapshot)):
            as_json = CliRunner().invoke(cli, ["status", "--all", "--json"])
            table = CliRunner().invoke(cli, ["status", "--all"])
        assert as_json.exit_code == 0, as_json.output
        rows = {r["job_id"]: r["status"] for r in json.loads(as_json.output)}
        assert {j: rows[j] for j in expected} == expected
        assert table.exit_code == 0, table.output
        for job_id, status in expected.items():
            line = next(ln for ln in table.output.splitlines() if f" {job_id} " in ln)
            assert status in line, line

    @pytest.mark.parametrize(
        ("snapshot", "scenario"),
        [("qstat_lifecycle.xml", "suspended"), ("qstat_transient.xml", "threshold")],
    )
    def test_hpc_status_of_one_suspended_job(self, monkeypatch, snapshot, scenario):
        """Column G, ``hpc status <id>``: ``qstat -j`` details, the state from the listing."""
        from click.testing import CliRunner

        from hpc_runner.cli.main import cli

        job_id = FX.job(scenario)
        assert parse_qstat_j_xml(FX.text(f"qstat_j_{scenario}.xml"))["name"] == scenario
        monkeypatch.setenv("HPC_SCHEDULER", "sge")
        details = {job_id: f"qstat_j_{scenario}.xml"}
        with patch_seam(_with_snapshot(snapshot, details)):
            result = CliRunner().invoke(cli, ["status", job_id])
        assert result.exit_code == 0, result.output
        assert "SUSPENDED" in result.output and scenario in result.output

    @pytest.mark.parametrize("snapshot", sorted(SNAPSHOTS))
    def test_wait_holds_on_active_states_and_ends_on_terminal(self, scheduler, snapshot):
        """Column J: ``wait()`` polls on through every active state; Eqw, dr and dt end it."""
        with patch_seam(_with_snapshot(snapshot)):
            for scenario, status in self.SNAPSHOTS[snapshot].items():
                handle = scheduler.result(FX.job(scenario))
                if status.is_terminal:
                    assert handle.wait(poll_interval=0.01, timeout=1) is status
                else:
                    with pytest.raises(TimeoutError):
                        handle.wait(poll_interval=0.01, timeout=0.05)

    def test_history_lists_a_requeued_job_once(self, scheduler):
        """The reschedule record (``failed 25``) is not a row of its own."""

        def respond(argv):
            assert argv[:2] == ["qacct", "-j"], argv
            return 0, FX.text("qacct_requeued.txt") + FX.text("qacct_ok.txt"), ""

        with patch_seam(respond):
            jobs = scheduler.list_completed_jobs()
        rows = {(j.job_id, j.status, j.exit_code, j.failed_code) for j in jobs}
        assert rows == {
            (FX.job("requeued"), JobStatus.FAILED, 137, 100),
            (FX.job("ok"), JobStatus.COMPLETED, 0, 0),
        }
        assert len(jobs) == 2

    def test_timeout_record_carries_sges_reason(self):
        info = qacct_to_job_info(parse_qacct_output(FX.text("qacct_timeout.txt")))
        assert info.failed_reason == "qmaster enforced h_rt, h_cpu, or h_vmem limit"
        assert info.queue == "short.q"


def _cli(argv: list[str], respond, monkeypatch, tmp_path):
    from click.testing import CliRunner

    from hpc_runner.cli.main import cli

    monkeypatch.setenv("HPC_SCHEDULER", "sge")
    monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path))
    monkeypatch.setenv("COLUMNS", "200")  # rich truncates cells at the default 80
    with patch_seam(respond):
        result = CliRunner().invoke(cli, argv)
    assert result.exit_code == 0, result.output
    return result.output


class TestMatrixGaps:
    """Readers over captures the matrix had no test for (GAP_PLAN.md W1, W3, W4, W6, W7)."""

    @pytest.fixture
    def scheduler(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path))
        return SGEScheduler(HPCConfig())

    # -- W1: exit codes where there is no record (column C) -----------------

    @pytest.mark.parametrize(
        ("scenario", "snapshot"),
        [("eqw", "qstat_mixed.xml"), ("deleted_pending", "qstat_empty.xml")],
    )
    def test_no_record_no_exit_code(self, scheduler, scenario, snapshot):
        """Row 4 (Eqw never ran) and row 18: no qacct record, no sentinel: None."""
        with patch_seam(_with_snapshot(snapshot)):
            assert scheduler.get_exit_code(FX.job(scenario)) is None

    def test_unknown_id_has_no_exit_code(self, scheduler):
        """Row 22."""
        with patch_seam(make_sge_responder(FX)):
            assert scheduler.get_exit_code("999999") is None

    # -- W3: history over every finished capture (columns E, G) -------------

    @staticmethod
    def _history(argv: list[str]) -> tuple[int, str, str]:
        assert argv[:3] == ["qacct", "-j", "-b"], argv
        return 0, "".join(FX.text(n) for n in FINISHED_RECORDS), ""

    def test_history_has_one_row_per_job_and_task(self, monkeypatch, tmp_path):
        import json

        out = _cli(["status", "--history", "--all", "--json"], self._history, monkeypatch, tmp_path)
        rows = json.loads(out)
        keys = [(r["job_id"], r["array_task_id"]) for r in rows]
        assert len(keys) == len(set(keys)) == len(FINISHED) + 3
        by_id = {r["job_id"]: r for r in rows if r["array_task_id"] is None}
        for scenario, status, exit_code, failed in FINISHED:
            row = by_id[FX.job(scenario)]
            assert (row["status"], row["exit_code"], row["failed_code"]) == (
                status.name,
                exit_code,
                failed,
            ), scenario
        tasks = {r["array_task_id"]: r["status"] for r in rows if r["array_task_id"]}
        assert tasks == {1: "COMPLETED", 2: "COMPLETED", 3: "FAILED"}

    def test_history_table_shows_each_status(self, monkeypatch, tmp_path):
        out = _cli(["status", "--history", "--all"], self._history, monkeypatch, tmp_path)
        for scenario, status, exit_code, _ in FINISHED:
            line = next(ln for ln in out.splitlines() if f" {FX.job(scenario)} " in ln)
            assert status.name in line and f" {exit_code} " in line, line

    # -- W4: hpc status <id> of every finished capture (column G) -----------

    @pytest.mark.parametrize(("scenario", "status", "exit_code", "failed"), FINISHED)
    def test_status_of_a_finished_job(
        self, monkeypatch, tmp_path, scenario, status, exit_code, failed
    ):
        import json

        respond = make_sge_responder(FX)
        job_id = FX.job(scenario)
        data = json.loads(_cli(["status", job_id, "--json"], respond, monkeypatch, tmp_path))
        assert (data["status"], data["exit_code"], data["failed_code"]) == (
            status.name,
            exit_code,
            failed,
        )
        out = _cli(["status", job_id], respond, monkeypatch, tmp_path)
        assert status.name in out and f"Job {job_id}" in out
        if failed:
            assert f"{failed}: " in out  # the reason SGE gave

    def test_status_of_a_job_deleted_while_pending(self, monkeypatch, tmp_path):
        """Row 18: no record anywhere; UNKNOWN, not a traceback."""
        out = _cli(
            ["status", FX.job("deleted_pending")], make_sge_responder(FX), monkeypatch, tmp_path
        )
        assert "UNKNOWN" in out

    def test_status_of_one_finished_array_task(self, monkeypatch, tmp_path):
        """Row 26: ``hpc status <base>.<task>`` is that task's record."""
        import json

        task = f"{FX.job('array_done')}.2"
        data = json.loads(
            _cli(["status", task, "--json"], make_sge_responder(FX), monkeypatch, tmp_path)
        )
        assert (data["status"], data["exit_code"], data["array_task_id"]) == ("COMPLETED", 0, 2)
        assert "tasks" not in data  # the task itself, not an array of one

    # -- W7: wait() where the matrix had none (column J) --------------------

    def test_wait_on_a_sentinel_only_job(self, scheduler, tmp_path):
        """Row 21: gone from qstat, qacct not flushed yet; the sentinel ends the wait."""
        job_id = "424242"
        (tmp_path / f"{job_id}.rc").write_text("0\n")
        with patch_seam(_with_snapshot("qstat_empty.xml")):
            status = scheduler.result(job_id).wait(poll_interval=0.01, timeout=1)
        assert status is JobStatus.COMPLETED

    def test_wait_on_an_unknown_id(self, scheduler):
        """Row 22: UNKNOWN once unknown_after has passed, not a hang."""
        with patch_seam(make_sge_responder(FX)):
            handle = scheduler.result("999999")
            assert handle.wait(poll_interval=0.01, unknown_after=0.05, timeout=2) is (
                JobStatus.UNKNOWN
            )

    # -- W5: details of a job in every captured active state (column F) -----

    @pytest.mark.parametrize(
        ("snapshot", "scenario"),
        [
            (snap, scenario)
            for snap, jobs in TestLifecycleStates.SNAPSHOTS.items()
            for scenario in jobs
        ],
    )
    def test_details_of_every_active_state(self, scheduler, snapshot, scenario):
        """``qstat -j`` gives the details, the listing gives the state."""
        job_id = FX.job(scenario)
        status = TestLifecycleStates.SNAPSHOTS[snapshot][scenario]
        details = {job_id: f"qstat_j_{scenario}.xml"}
        with patch_seam(_with_snapshot(snapshot, details)):
            info, extra = scheduler.get_job_details(job_id)
        assert info.job_id == job_id and info.status is status
        # `deleting` is the queue_suspended job, deleted later.
        name = "queue_suspended" if scenario == "deleting" else scenario
        assert info.name == name and info.user == "tester"
        assert extra.get("script_file") or extra.get("command") or extra.get("job_args"), extra

    # -- W2: accounting with the qmaster down (row 23) ----------------------

    @staticmethod
    def _master_down(argv: list[str]) -> tuple[int, str, str]:
        if argv[0] == "qstat":
            name = "qstat_j_master_down.json" if argv[1] == "-j" else "qstat_master_down.json"
            return FX.result(name)
        if argv == ["qacct", "-j", FX.job("ok")]:
            return FX.result("qacct_master_down.json")
        if argv[:4] == ["qacct", "-j", "-o", "bob"]:
            return FX.result("qacct_history_master_down.json")
        raise AssertionError(argv)

    def test_master_down_capture_says_what_works(self):
        """qacct reads the accounting file, so it answers; ``qstat -j`` exits 0
        with a ``comunication_error`` document (sic) and the reason on stderr."""
        assert FX.result("qacct_master_down.json")[0] == 0
        assert FX.result("qacct_history_master_down.json")[0] == 0
        rc, out, err = FX.result("qstat_j_master_down.json")
        assert rc == 0 and "comunication_error" in out and "commlib" in err

    def test_finished_job_is_still_readable_with_the_master_down(self, scheduler):
        """Row 23, C and F: the exit code and details of a finished job come
        from accounting, which does not need the qmaster."""
        ok = FX.job("ok")
        with patch_seam(self._master_down):
            assert scheduler.get_exit_code(ok) == 0
            info, _ = scheduler.get_job_details(ok)
        assert (info.status, info.exit_code) == (JobStatus.COMPLETED, 0)

    def test_history_with_the_master_down(self, scheduler):
        """Row 23, E: the listing reads accounting too."""
        with patch_seam(self._master_down):
            jobs = scheduler.list_completed_jobs(user="bob")
        # Every bob job the stack has run, so the capture's is one of them.
        assert FX.job("bob") in {j.job_id for j in jobs}
        assert {j.user for j in jobs} == {"bob"}

    def test_active_state_with_the_master_down_raises(self, scheduler):
        """Row 23, B: what does need the qmaster says so (not "not found")."""
        from hpc_runner.core.exceptions import SchedulerUnavailableError

        with patch_seam(self._master_down), pytest.raises(SchedulerUnavailableError):
            scheduler.get_status(FX.job("running"))

    def test_qstat_j_with_scheduler_messages_parses(self):
        """gridengine writes ``<job ids>`` (not XML) when a job has scheduling
        messages; the details must still parse (found by W5, D-T46)."""
        text = FX.text("qstat_j_requeue_pending.xml")
        assert "<job ids>" in text
        assert parse_qstat_j_xml(text)["name"] == "requeue_pending"

    def test_status_of_a_sentinel_only_job(self, monkeypatch, tmp_path):
        """Row 21, F and G: no qstat, no qacct yet; the sentinel gives the status."""
        (tmp_path / "424242.rc").write_text("3\n")
        out = _cli(["status", "424242"], _with_snapshot("qstat_empty.xml"), monkeypatch, tmp_path)
        assert "FAILED" in out and "Exit Code" in out and " 3 " in out

    def test_status_of_an_unknown_id(self, monkeypatch, tmp_path):
        """Rows 20 and 22, G: nothing anywhere (aged out looks the same); UNKNOWN."""
        out = _cli(["status", "999999"], make_sge_responder(FX), monkeypatch, tmp_path)
        assert "UNKNOWN" in out


class TestXmlDefects:
    """gridengine's XML defects (docs/dev/sge-xml-defects.md) against the
    ``xml_defects`` capture: repaired where the fix is certain, an error
    otherwise, never an empty answer."""

    @pytest.fixture
    def scheduler(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path))
        return SGEScheduler(HPCConfig())

    def test_capture_shows_both_defects(self):
        import xml.etree.ElementTree as ET

        text = FX.text("qstat_j_xml_defects.xml")
        assert "<JB_job_name>a%lt;b</JB_job_name>" in text  # defect 2
        assert "\x1b" in text  # defect 3: the ESC of CTRL, passed through
        with pytest.raises(ET.ParseError):
            ET.fromstring(text)

    def test_details_survive_both(self):
        data = parse_qstat_j_xml(FX.text("qstat_j_xml_defects.xml"))
        assert data["name"] == "a<b"
        assert data["job_args"] == ["a<b", "x&y"]

    def test_listing_and_details_through_the_scheduler(self, scheduler):
        job_id = FX.job("xml_defects")
        details = {job_id: "qstat_j_xml_defects.xml"}
        with patch_seam(_with_snapshot("qstat_xml_defects.xml", details)):
            [listed] = scheduler.list_active_jobs()
            info, extra = scheduler.get_job_details(job_id)
        assert (listed.job_id, listed.name, listed.status) == (job_id, "a<b", JobStatus.PENDING)
        assert info.name == "a<b" and extra["job_args"] == ["a<b", "x&y"]

    def test_hpc_status_shows_the_name(self, monkeypatch, tmp_path):
        job_id = FX.job("xml_defects")
        respond = _with_snapshot("qstat_xml_defects.xml", {job_id: "qstat_j_xml_defects.xml"})
        assert "a<b" in _cli(["status", job_id], respond, monkeypatch, tmp_path)

    def test_any_spaced_tag_name_is_repaired(self):
        """Defect 1 is a class (unchecked CULL list names), not one tag."""
        text = FX.text("qstat_j_running.xml").replace(
            "<JB_job_name>", "<env list><x/></env list><JB_job_name>", 1
        )
        assert parse_qstat_j_xml(text)["name"] == "running"

    def test_attributes_are_not_touched(self):
        """``<job_list state="running">`` has a space and an ``=``: valid, left alone."""
        assert parse_qstat_xml_states(FX.text("qstat_mixed.xml"))[FX.job("running")] == "r"

    def test_unknown_defect_is_an_error_naming_the_line(self, scheduler, monkeypatch, capsys):
        """Anything not repaired raises; ``hpc status`` prints it in one line."""
        from hpc_runner.cli.main import main
        from hpc_runner.core.exceptions import SchedulerOutputError

        job_id = FX.job("running")
        broken = FX.text("qstat_j_running.xml").replace(
            "<JB_job_name>running", "<JB_job_name>a&b", 1
        )

        def respond(argv):
            if argv[:2] == ["qstat", "-j"]:
                return 0, broken, ""
            return make_sge_responder(FX)(argv)

        with patch_seam(respond), pytest.raises(SchedulerOutputError, match="JB_job_name"):
            scheduler.get_job_details(job_id)
        # Through main(), the console script, which turns HPCToolsError into
        # one line on stderr and exit 1.
        monkeypatch.setenv("HPC_SCHEDULER", "sge")
        monkeypatch.setattr("sys.argv", ["hpc", "status", job_id])
        with patch_seam(respond), pytest.raises(SystemExit) as exit_info:
            main()
        assert exit_info.value.code == 1
        err = capsys.readouterr().err
        assert err.startswith("error: qstat -j -xml output is not XML") and "a&b" in err
