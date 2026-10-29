"""Tests for the scheduler subprocess seam and the errors it raises."""

import logging
import sys

import pytest

from hpc_runner.core.config import HPCConfig
from hpc_runner.core.exceptions import (
    ConfigError,
    JobNotFoundError,
    SchedulerCommandError,
    SchedulerTimeoutError,
    SchedulerUnavailableError,
    SubmissionError,
)
from hpc_runner.schedulers import get_scheduler
from hpc_runner.schedulers._subprocess import run_command
from hpc_runner.schedulers.sge import SGEScheduler
from tests.fakes import make_job, patch_seam

PY = sys.executable

QSUB_REJECT = (
    'Unable to run job: Job was rejected because job requests unknown queue "nope.q".\nExiting.\n'
)


class TestRunCommand:
    """The real seam, against real processes."""

    def test_missing_binary(self):
        with pytest.raises(SchedulerUnavailableError, match="no-such-binary-xyz not found on PATH"):
            run_command(["no-such-binary-xyz"], timeout=5)

    def test_nonzero_exit_raises_with_stderr_headline(self):
        code = "import sys; print('out'); sys.stderr.write('\\nbad thing\\nmore\\n'); sys.exit(3)"
        with pytest.raises(SchedulerCommandError) as info:
            run_command([PY, "-c", code], timeout=10)
        exc = info.value
        assert str(exc) == "bad thing"
        assert exc.returncode == 3
        assert exc.argv == [PY, "-c", code]
        assert exc.stdout == "out\n"
        assert exc.stderr == "\nbad thing\nmore\n"

    def test_nonzero_exit_without_stderr_message(self):
        with pytest.raises(SchedulerCommandError, match=r"exited with status 2"):
            run_command([PY, "-c", "import sys; sys.exit(2)"], timeout=10)

    def test_check_false_returns_result(self):
        result = run_command([PY, "-c", "import sys; sys.exit(4)"], timeout=10, check=False)
        assert result.returncode == 4

    def test_timeout(self):
        with pytest.raises(SchedulerTimeoutError) as info:
            run_command([PY, "-c", "import time; time.sleep(10)"], timeout=0.2)
        assert info.value.timeout == 0.2
        assert info.value.argv[0] == PY

    def test_logs_argv_at_debug(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="hpc_runner.schedulers"):
            run_command([PY, "-c", "pass"], timeout=10)
        assert any("-c pass" in r.getMessage() for r in caplog.records)


@pytest.fixture
def sge(monkeypatch, tmp_path):
    monkeypatch.setenv("HPC_SCRIPT_DIR", str(tmp_path / "scripts"))
    monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path / "exit"))
    return SGEScheduler(HPCConfig())


class TestSGEErrors:
    """SGE raise sites, through a fake seam that honours ``check``."""

    def test_qsub_rejection_is_submission_error(self, sge, tmp_path):
        with patch_seam(lambda argv: (1, "", QSUB_REJECT)):
            with pytest.raises(SubmissionError) as info:
                sge.submit(make_job("echo hi", name="j", workdir=str(tmp_path)))
        exc = info.value
        assert str(exc).startswith("Unable to run job: Job was rejected")
        assert exc.stderr == QSUB_REJECT
        assert exc.script is not None and "echo hi" in exc.script
        assert exc.argv is not None and exc.argv[0] == "qsub"

    def test_unparseable_qsub_output(self, sge, tmp_path):
        with patch_seam(lambda argv: (0, "something odd\n", "")):
            with pytest.raises(SubmissionError, match="could not parse job id") as info:
                sge.submit(make_job("echo hi", name="j", workdir=str(tmp_path)))
        assert info.value.script is not None

    def test_array_qsub_rejection(self, sge, tmp_path):
        job = make_job("echo hi", name="j", array="1-3", workdir=str(tmp_path))
        with patch_seam(lambda argv: (1, "", QSUB_REJECT)):
            with pytest.raises(SubmissionError) as info:
                sge.submit(job)
        assert info.value.script is not None

    def test_missing_qsub(self, sge, tmp_path):
        def respond(argv):
            raise SchedulerUnavailableError(f"{argv[0]} not found on PATH")

        with patch_seam(respond):
            with pytest.raises(SchedulerUnavailableError):
                sge.submit(make_job("echo hi", name="j", workdir=str(tmp_path)))

    def test_qsub_timeout(self, sge, tmp_path):
        def respond(argv):
            raise SchedulerTimeoutError(argv, 60.0)

        with patch_seam(respond):
            with pytest.raises(SchedulerTimeoutError):
                sge.submit(make_job("echo hi", name="j", workdir=str(tmp_path)))

    def test_query_commands_get_default_timeout(self, sge):
        with patch_seam(lambda argv: (0, "", "")) as mock:
            sge.list_active_jobs()
        assert mock.call_args.kwargs["timeout"] == sge.COMMAND_TIMEOUT

    def test_get_job_details_unknown(self, sge):
        with patch_seam(lambda argv: (1, "", "")):
            with pytest.raises(JobNotFoundError):
                sge.get_job_details("999")

    def test_get_job_details_missing_qstat(self, sge):
        def respond(argv):
            raise SchedulerUnavailableError("qstat not found on PATH")

        with patch_seam(respond):
            with pytest.raises(SchedulerUnavailableError):
                sge.get_job_details("999")

    def test_list_active_jobs_nonzero_is_empty(self, sge):
        with patch_seam(lambda argv: (1, "", "no jobs")):
            assert sge.list_active_jobs() == []

    def test_list_active_jobs_missing_qstat_propagates(self, sge):
        def respond(argv):
            raise SchedulerUnavailableError("qstat not found on PATH")

        with patch_seam(respond):
            with pytest.raises(SchedulerUnavailableError):
                sge.list_active_jobs()

    def test_cancel_refused_returns_false(self, sge):
        with patch_seam(lambda argv: (1, "", "denied")):
            assert sge.cancel("1") is False

    def test_cancel_missing_qdel_raises(self, sge):
        def respond(argv):
            raise SchedulerUnavailableError("qdel not found on PATH")

        with patch_seam(respond):
            with pytest.raises(SchedulerUnavailableError):
                sge.cancel("1")


def test_unknown_scheduler_is_config_error():
    with pytest.raises(ConfigError, match="unknown scheduler 'nope'; available: .*sge"):
        get_scheduler("nope", config=HPCConfig())
