"""SGE accounting ``failed`` codes, signal exit codes and an unreachable qmaster.

The texts are what gridengine 8.1.9 in the testbed wrote, captured by
``hpc-testbed capture`` into tests/unit/fixtures/sge/ (BUG-T3, T5, T6 in
docs/dev/testbed/TESTBED_REVIEW.md); the code table was produced by that
image's own qacct. The one retyped text is the ``h_rt`` kill (not captured:
it takes a minute of wall clock).
"""

from __future__ import annotations

import pytest

from hpc_runner.core.config import HPCConfig
from hpc_runner.core.exceptions import SchedulerUnavailableError
from hpc_runner.core.result import JobStatus
from hpc_runner.schedulers.sge import SGEScheduler
from hpc_runner.schedulers.sge.parser import (
    SGE_FAILED_CODES,
    failed_reason,
    parse_failed,
    parse_qacct_output,
    qacct_status,
    qacct_to_job_info,
    qstat_unreachable,
)
from tests.fakes import SGEFixtures, patch_seam

FIXTURES = SGEFixtures()

#: qacct -j of a job whose node was killed mid-run (BUG-T3): exit_status is 0.
QACCT_LOST_NODE = FIXTURES.text("qacct_lost_node.txt")
LOST_NODE_ID = FIXTURES.job("lost_node")

#: qacct -j of a running job deleted with qdel (BUG-T5).
QACCT_SIGNAL = FIXTURES.text("qacct_signal.txt")
SIGNAL_ID = FIXTURES.job("signal")

#: qacct -j of an h_rt kill enforced by the qmaster (D-T30).
QACCT_H_RT = """\
==============================================================
owner        tester
jobname      hrt
jobnumber    33
failed       37  : qmaster enforced h_rt, h_cpu, or h_vmem limit
exit_status  137 (Killed)
"""

#: qstat -xml -u '*' with the master stopped (BUG-T6): (returncode, stdout, stderr).
QSTAT_MASTER_DOWN = FIXTURES.result("qstat_master_down.json")


class TestFailedTable:
    def test_table_covers_the_shepherd_states_and_after_job(self):
        assert set(SGE_FAILED_CODES) == (set(range(1, 39)) - {23}) | {100}
        assert SGE_FAILED_CODES[19] == "before writing exit_status"
        assert SGE_FAILED_CODES[37].startswith("qmaster enforced")
        assert SGE_FAILED_CODES[100] == "assumedly after job"

    @pytest.mark.parametrize("code", [0, 23, 39, 45])
    def test_reason_outside_the_table(self, code):
        assert failed_reason(code) == ("" if code == 0 else "invalid execution state")

    def test_parse_failed_prefers_the_text_qacct_printed(self):
        assert parse_failed("19  : before writing exit_status") == (
            19,
            "before writing exit_status",
        )
        assert parse_failed("0") == (0, "")
        assert parse_failed("28") == (28, "changing into working directory")
        assert parse_failed(None) is None
        assert parse_failed("garbage") is None

    @pytest.mark.parametrize(
        ("code", "exit_status", "expected"),
        [
            (0, "0", JobStatus.COMPLETED),
            (0, "1", JobStatus.FAILED),
            (0, "113", JobStatus.SKIPPED),
            (37, "137 (Killed)", JobStatus.TIMEOUT),
            (37, "0", JobStatus.TIMEOUT),
            (100, "137 (Killed)", JobStatus.FAILED),
            (100, "0", JobStatus.COMPLETED),
            (100, "113", JobStatus.SKIPPED),
            (24, "0", None),
            (25, "0", None),
            *[
                (c, "0", JobStatus.FAILED)
                for c in sorted(SGE_FAILED_CODES)
                if c not in (24, 25, 37, 100)
            ],
            (39, "0", JobStatus.FAILED),
        ],
    )
    def test_status_table(self, code, exit_status, expected):
        text = f"{code}  : {failed_reason(code)}" if code else "0"
        record = {"failed": text, "exit_status": exit_status}
        assert qacct_status(record) is expected

    def test_no_exit_status_is_undecided(self):
        assert qacct_status({}) is None
        assert qacct_status({"failed": "100 : assumedly after job"}) is None
        assert qacct_status({"failed": "19  : before writing exit_status"}) is JobStatus.FAILED


class TestLostNode:
    """BUG-T3: failed 19 with exit_status 0 is not a success."""

    def test_record_is_failed_with_the_reason_kept(self):
        record = parse_qacct_output(QACCT_LOST_NODE)
        assert qacct_status(record) is JobStatus.FAILED
        info = qacct_to_job_info(record)
        assert info.status is JobStatus.FAILED
        assert info.exit_code == 0
        assert (info.failed_code, info.failed_reason) == (19, "before writing exit_status")

    def test_clean_record_has_no_failure(self):
        info = qacct_to_job_info(parse_qacct_output(FIXTURES.text("qacct_ok.txt")))
        assert (info.status, info.exit_code) == (JobStatus.COMPLETED, 0)
        assert info.failed_code == 0 and info.failed_reason is None
        info = qacct_to_job_info({"jobnumber": "1", "exit_status": "0"})
        assert info.failed_code is None and info.failed_reason is None

    def test_get_status_reports_failed(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path))

        def respond(argv):
            if argv[:2] == ["qstat", "-xml"]:
                return 0, FIXTURES.text("qstat_empty.xml"), ""
            assert argv == ["qacct", "-j", LOST_NODE_ID]
            return 0, QACCT_LOST_NODE, ""

        with patch_seam(respond):
            assert SGEScheduler(HPCConfig()).get_status(LOST_NODE_ID) is JobStatus.FAILED


class TestSignalExitCode:
    """BUG-T5: ``exit_status 137 (Killed)`` is exit code 137."""

    def test_get_exit_code(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path))

        def respond(argv):
            assert argv == ["qacct", "-j", SIGNAL_ID]
            return 0, QACCT_SIGNAL, ""

        with patch_seam(respond):
            assert SGEScheduler(HPCConfig()).get_exit_code(SIGNAL_ID) == 137

    def test_job_info(self):
        info = qacct_to_job_info(parse_qacct_output(QACCT_SIGNAL))
        assert info.exit_code == 137
        assert info.status is JobStatus.FAILED
        assert info.failed_code == 100

    def test_h_rt_kill_is_timeout_with_exit_137(self):
        info = qacct_to_job_info(parse_qacct_output(QACCT_H_RT))
        assert (info.status, info.exit_code, info.failed_code) == (JobStatus.TIMEOUT, 137, 37)


class TestMasterDown:
    """BUG-T6: a qstat that cannot reach the qmaster is an error, not an empty queue."""

    def test_captured_text_is_unreachable(self):
        rc, stdout, stderr = QSTAT_MASTER_DOWN
        assert rc != 0 and stdout == ""
        assert "unable to contact qmaster" in stderr
        assert qstat_unreachable(stderr)

    @pytest.mark.parametrize(
        "stderr",
        [
            'error: unable to contact qmaster using port 6444 on host "master"\n',
            "error: commlib error: got select error (Connection refused)\n",
            "error: commlib error: can't connect to service (master/6444)\n",
        ],
    )
    def test_unreachable_texts(self, stderr):
        assert qstat_unreachable(stderr)

    @pytest.mark.parametrize("stderr", ["", "no jobs\n", "error: job 5 does not exist\n"])
    def test_other_failures_are_nothing_active(self, stderr):
        assert not qstat_unreachable(stderr)

    @pytest.fixture
    def scheduler(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path))
        return SGEScheduler(HPCConfig())

    def test_get_status_raises(self, scheduler):
        calls = []

        def respond(argv):
            calls.append(argv)
            return QSTAT_MASTER_DOWN

        with patch_seam(respond), pytest.raises(SchedulerUnavailableError, match="qmaster"):
            scheduler.get_status("5")
        # Nothing fell through to qacct or the sentinel.
        assert calls == [["qstat", "-xml", "-u", "*"]]

    def test_list_active_jobs_raises(self, scheduler):
        with patch_seam(lambda argv: QSTAT_MASTER_DOWN):
            with pytest.raises(SchedulerUnavailableError):
                scheduler.list_active_jobs(user="alice")

    def test_other_nonzero_qstat_is_still_nothing_active(self, scheduler, tmp_path):
        def respond(argv):
            if argv[0] == "qstat":
                return 1, "", "no jobs\n"
            return 1, "", "error: job id 5 not found\n"

        with patch_seam(respond):
            assert scheduler.get_status("5") is JobStatus.UNKNOWN
            assert scheduler.list_active_jobs() == []
