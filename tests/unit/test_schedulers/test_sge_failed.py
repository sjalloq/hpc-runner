"""SGE accounting ``failed`` codes, signal exit codes and an unreachable qmaster.

The texts are what each SGE flavour in the testbed wrote, captured by
``hpc-testbed capture`` into tests/unit/fixtures/sge/<flavour>/ (BUG-T3, T5,
T6 in docs/dev/testbed/TESTBED_REVIEW.md); the code table was produced by
gridengine 8.1.9's own qacct. The one retyped text is a ``failed 37`` record
(test_sge_fixtures.py reads each flavour's captured ``h_rt`` kill).
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


@pytest.fixture
def fx(sge_fixtures: SGEFixtures) -> SGEFixtures:
    """One flavour's captures: ``qacct_lost_node.txt`` (BUG-T3, exit_status 0),
    ``qacct_signal.txt`` (BUG-T5, a qdel) and ``qstat_master_down.json`` (BUG-T6)."""
    return sge_fixtures


#: qacct -j of an h_rt kill enforced by the qmaster (D-T30).
QACCT_H_RT = """\
==============================================================
owner        tester
jobname      hrt
jobnumber    33
failed       37  : qmaster enforced h_rt, h_cpu, or h_vmem limit
exit_status  137 (Killed)
"""


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
            (37, "137 (Killed)", JobStatus.KILLED),
            (37, "0", JobStatus.KILLED),
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

    @pytest.mark.parametrize(
        ("deleted_by", "expected"),
        [
            # OCS: execd killed it for h_rt, h_cpu, h_rss or h_vmem
            ("execd@exec1", JobStatus.KILLED),
            # OCS: a qdel, or a qmaster reschedule; exit_status decides
            ("alice@login", JobStatus.FAILED),
            ("qmaster@master", JobStatus.FAILED),
        ],
    )
    def test_ocs_deleted_by(self, deleted_by, expected):
        record = {
            "failed": "100 : assumedly after job",
            "exit_status": "137",
            "deleted_by": deleted_by,
        }
        assert qacct_status(record) is expected

    def test_ocs_execd_kill_names_the_limits(self):
        info = qacct_to_job_info(
            {
                "jobnumber": "123",
                "failed": "100 : assumedly after job",
                "exit_status": "137",
                "deleted_by": "execd@exec1",
            }
        )
        assert (info.status, info.exit_code, info.failed_code) == (JobStatus.KILLED, 137, 100)
        assert info.failed_reason == (
            "killed by execd@exec1 for exceeding a hard limit (h_rt, h_cpu, h_rss or h_vmem)"
        )

    def test_no_exit_status_is_undecided(self):
        assert qacct_status({}) is None
        assert qacct_status({"failed": "100 : assumedly after job"}) is None
        assert qacct_status({"failed": "19  : before writing exit_status"}) is JobStatus.FAILED


class TestLostNode:
    """BUG-T3: failed 19 with exit_status 0 is not a success."""

    def test_record_is_failed_with_the_reason_kept(self, fx):
        record = parse_qacct_output(fx.text("qacct_lost_node.txt"))
        assert qacct_status(record) is JobStatus.FAILED
        info = qacct_to_job_info(record)
        assert info.status is JobStatus.FAILED
        assert info.exit_code == 0
        assert (info.failed_code, info.failed_reason) == (19, "before writing exit_status")

    def test_clean_record_has_no_failure(self, fx):
        info = qacct_to_job_info(parse_qacct_output(fx.text("qacct_ok.txt")))
        assert (info.status, info.exit_code) == (JobStatus.COMPLETED, 0)
        assert info.failed_code == 0 and info.failed_reason is None
        info = qacct_to_job_info({"jobnumber": "1", "exit_status": "0"})
        assert info.failed_code is None and info.failed_reason is None

    def test_get_status_reports_failed(self, fx, monkeypatch, tmp_path):
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path))
        job_id = fx.job("lost_node")

        def respond(argv):
            if argv[:2] == ["qstat", "-xml"]:
                return 0, fx.text("qstat_empty.xml"), ""
            assert argv == ["qacct", "-j", job_id]
            return 0, fx.text("qacct_lost_node.txt"), ""

        with patch_seam(respond):
            assert SGEScheduler(HPCConfig()).get_status(job_id) is JobStatus.FAILED


class TestSignalExitCode:
    """BUG-T5: ``exit_status 137 (Killed)`` is exit code 137."""

    def test_get_exit_code(self, fx, monkeypatch, tmp_path):
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path))
        job_id = fx.job("signal")

        def respond(argv):
            assert argv == ["qacct", "-j", job_id]
            return 0, fx.text("qacct_signal.txt"), ""

        with patch_seam(respond):
            assert SGEScheduler(HPCConfig()).get_exit_code(job_id) == 137

    def test_job_info(self, fx):
        info = qacct_to_job_info(parse_qacct_output(fx.text("qacct_signal.txt")))
        assert info.exit_code == 137
        assert info.status is JobStatus.FAILED
        assert info.failed_code == 100

    def test_h_rt_kill_is_killed_with_exit_137(self):
        info = qacct_to_job_info(parse_qacct_output(QACCT_H_RT))
        assert (info.status, info.exit_code, info.failed_code) == (JobStatus.KILLED, 137, 37)


class TestMasterDown:
    """BUG-T6: a qstat that cannot reach the qmaster is an error, not an empty queue."""

    def test_captured_text_is_unreachable(self, fx):
        rc, stdout, stderr = fx.result("qstat_master_down.json")
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

    def test_get_status_raises(self, fx, scheduler):
        calls = []

        def respond(argv):
            calls.append(argv)
            return fx.result("qstat_master_down.json")

        with patch_seam(respond), pytest.raises(SchedulerUnavailableError, match="qmaster"):
            scheduler.get_status("5")
        # Nothing fell through to qacct or the sentinel.
        assert calls == [["qstat", "-xml", "-u", "*"]]

    def test_list_active_jobs_raises(self, fx, scheduler):
        with patch_seam(lambda argv: fx.result("qstat_master_down.json")):
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
