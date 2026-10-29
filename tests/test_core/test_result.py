"""Tests for JobResult.result(), wait() and output tails."""

import time
from unittest.mock import MagicMock

import pytest

from hpc_runner.core.array import ArrayRange
from hpc_runner.core.exceptions import JobFailedError, NotSupportedError
from hpc_runner.core.result import ArrayJobResult, JobResult, JobStatus


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


class TestInteractiveHandle:
    """A completed interactive run: no job id, exit code known."""

    def _interactive(self, exit_code: int) -> JobResult:
        return JobResult(scheduler=MagicMock(), job_id=None, exit_code=exit_code)

    def test_status_from_exit_code(self):
        assert self._interactive(0).status is JobStatus.COMPLETED
        assert self._interactive(2).status is JobStatus.FAILED
        assert self._interactive(2).returncode == 2

    def test_wait_returns_immediately(self):
        res = self._interactive(0)
        assert res.wait(poll_interval=10) is JobStatus.COMPLETED
        res.scheduler.get_status.assert_not_called()

    def test_cancel_is_false_without_calling_scheduler(self):
        res = self._interactive(0)
        assert res.cancel() is False
        res.scheduler.cancel.assert_not_called()

    def test_result_raises_on_failure(self):
        with pytest.raises(JobFailedError) as info:
            self._interactive(5).result()
        assert info.value.job_id is None
        assert info.value.exit_code == 5

    def test_no_output_paths(self):
        assert self._interactive(0).stdout_path() is None
        assert self._interactive(0).read_stdout() == ""


class TestHandleWithoutJob:
    def test_job_is_optional(self):
        res = _result(JobStatus.RUNNING, None)
        res.job = None
        assert res.status is JobStatus.RUNNING


class TestArrayJobResult:
    def _array(self, statuses: dict[str, JobStatus]) -> ArrayJobResult:
        scheduler = MagicMock()
        scheduler.get_status.side_effect = lambda job_id: statuses[job_id]
        return ArrayJobResult(scheduler=scheduler, job_id="7", array=ArrayRange(1, 3))

    def test_is_a_job_result(self):
        assert isinstance(self._array({}), JobResult)

    def test_task_id(self):
        assert self._array({}).task_id(2) == "7.2"

    def test_wait_returns_per_task_statuses(self):
        res = self._array(
            {"7.1": JobStatus.COMPLETED, "7.2": JobStatus.FAILED, "7.3": JobStatus.COMPLETED}
        )
        assert res.wait(poll_interval=0) == {
            1: JobStatus.COMPLETED,
            2: JobStatus.FAILED,
            3: JobStatus.COMPLETED,
        }

    def test_wait_timeout(self):
        res = self._array({f"7.{i}": JobStatus.RUNNING for i in (1, 2, 3)})
        with pytest.raises(TimeoutError):
            res.wait(poll_interval=0, timeout=0)

    def test_cancel_uses_base_id(self):
        res = self._array({})
        res.cancel()
        res.scheduler.cancel.assert_called_once_with("7")

    def test_result_not_supported(self):
        with pytest.raises(NotSupportedError):
            self._array({}).result()
