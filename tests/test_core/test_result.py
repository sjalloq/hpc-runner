"""Tests for JobResult.result(), wait() and output tails."""

import time
from unittest.mock import MagicMock

import pytest

from hpc_runner.core.exceptions import JobFailedError
from hpc_runner.core.result import JobResult, JobStatus


def _result(status: JobStatus, exit_code: int | None) -> JobResult:
    scheduler = MagicMock()
    scheduler.get_status.return_value = status
    scheduler.get_exit_code.return_value = exit_code
    return JobResult(job_id="42", scheduler=scheduler, job=MagicMock())


class TestResult:
    def test_completed_returns_exit_code(self):
        assert _result(JobStatus.COMPLETED, 0).result(poll_interval=0) == 0

    def test_failed_raises_job_failed(self):
        with pytest.raises(JobFailedError) as info:
            _result(JobStatus.FAILED, 3).result(poll_interval=0)
        exc = info.value
        assert exc.status is JobStatus.FAILED
        assert exc.job_id == "42"
        assert exc.exit_code == 3

    def test_cancelled_raises_job_failed(self):
        with pytest.raises(JobFailedError) as info:
            _result(JobStatus.CANCELLED, None).result(poll_interval=0)
        assert info.value.exit_code is None


class TestWait:
    def test_timeout_zero_raises_immediately(self):
        res = _result(JobStatus.RUNNING, None)
        start = time.monotonic()
        with pytest.raises(TimeoutError):
            res.wait(poll_interval=10, timeout=0)
        assert time.monotonic() - start < 1

    def test_timeout_zero_returns_if_already_done(self):
        assert _result(JobStatus.COMPLETED, 0).wait(timeout=0) is JobStatus.COMPLETED


class TestReadTail:
    def test_tail_zero_is_empty(self, tmp_path):
        out = tmp_path / "out.txt"
        out.write_text("a\nb\nc\n")
        res = _result(JobStatus.COMPLETED, 0)
        res.scheduler.get_output_path.return_value = out
        assert res.read_stdout(tail=0) == ""
        assert res.read_stdout(tail=2) == "b\nc"
        assert res.read_stdout() == "a\nb\nc\n"
