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

    @pytest.mark.parametrize(
        ("scenario", "status", "exit_code", "failed"),
        [
            ("ok", JobStatus.COMPLETED, 0, 0),
            ("exit1", JobStatus.FAILED, 1, 0),
            ("signal", JobStatus.FAILED, 137, 100),
            ("lost_node", JobStatus.FAILED, 0, 19),
            ("timeout", JobStatus.TIMEOUT, 137, 37),
            ("vmem", JobStatus.FAILED, 2, 0),
            ("requeued", JobStatus.FAILED, 137, 100),
            ("skipped", JobStatus.SKIPPED, 113, 0),
        ],
    )
    def test_details(self, monkeypatch, tmp_path, scenario, status, exit_code, failed):
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path))
        with patch_seam(make_sge_responder(FX)):
            info, extra = SGEScheduler(HPCConfig()).get_job_details(FX.job(scenario))
        assert extra == {}
        assert (info.status, info.exit_code, info.failed_code) == (status, exit_code, failed)
        assert info.job_id == FX.job(scenario) and info.array_task_id is None


def _with_snapshot(name: str):
    """The captured responder, but ``qstat -xml -u '*'`` answers with *name*."""
    base = make_sge_responder(FX)

    def respond(argv: list[str]) -> tuple[int, str, str]:
        if argv == ["qstat", "-xml", "-u", "*"]:
            return 0, FX.text(name), ""
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
        with patch_seam(_with_snapshot(snapshot)):
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
