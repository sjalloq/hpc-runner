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

    def test_one_entry_per_id_running_array_wins(self):
        jobs = parse_qstat_xml(FX.text("qstat_mixed.xml"))
        assert set(jobs) == {FX.job(s) for s in ("running", "held", "eqw", "array")}
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
