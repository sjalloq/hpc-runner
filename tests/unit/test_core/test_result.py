"""Tests for JobResult.result(), wait() and output tails."""

import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from hpc_runner.core.array import ArrayRange
from hpc_runner.core.exceptions import JobFailedError, NotSupportedError
from hpc_runner.core.result import (
    HPC_EXIT_DEP_FAILED,
    ArrayJobResult,
    JobResult,
    JobStatus,
    reduce_statuses,
    status_from_exit_code,
)
from tests.fakes import make_job


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


class TestStatus:
    @pytest.mark.parametrize(
        "status,terminal",
        [
            (JobStatus.PENDING, False),
            (JobStatus.RUNNING, False),
            (JobStatus.UNKNOWN, False),
            (JobStatus.COMPLETED, True),
            (JobStatus.FAILED, True),
            (JobStatus.CANCELLED, True),
            (JobStatus.TIMEOUT, True),
            (JobStatus.SKIPPED, True),
        ],
    )
    def test_is_terminal(self, status, terminal):
        assert status.is_terminal is terminal

    def test_status_from_exit_code(self):
        assert status_from_exit_code(0) is JobStatus.COMPLETED
        assert status_from_exit_code(1) is JobStatus.FAILED
        assert status_from_exit_code(HPC_EXIT_DEP_FAILED) is JobStatus.SKIPPED
        assert HPC_EXIT_DEP_FAILED == 113

    def test_skipped_result_raises_with_status(self):
        with pytest.raises(JobFailedError) as info:
            _result(JobStatus.SKIPPED, 113).result(poll_interval=0)
        assert info.value.status is JobStatus.SKIPPED
        assert info.value.exit_code == 113


class _Sequence:
    """A scheduler whose get_status walks through *statuses*, then repeats the last."""

    def __init__(self, statuses: list[JobStatus]):
        self.statuses = statuses
        self.calls = 0

    def get_status(self, job_id: str) -> JobStatus:
        status = self.statuses[min(self.calls, len(self.statuses) - 1)]
        self.calls += 1
        return status


class TestWait:
    def test_unknown_after_returns_unknown(self):
        sched = _Sequence([JobStatus.UNKNOWN])
        res = JobResult(scheduler=sched, job_id="1")  # type: ignore[arg-type]
        assert res.wait(poll_interval=0.001, unknown_after=0.01) is JobStatus.UNKNOWN
        assert sched.calls > 1

    def test_unknown_clock_resets_on_running(self, monkeypatch):
        """UNKNOWN, RUNNING, UNKNOWN...: only a continuous UNKNOWN run counts."""
        import hpc_runner.core.result as result_module

        clock = [0.0]

        class FakeTime:
            @staticmethod
            def monotonic() -> float:
                return clock[0]

            @staticmethod
            def sleep(seconds: float) -> None:
                clock[0] += seconds

        monkeypatch.setattr(result_module, "time", FakeTime)
        sched = _Sequence([JobStatus.UNKNOWN, JobStatus.RUNNING, JobStatus.UNKNOWN])
        res = JobResult(scheduler=sched, job_id="1")  # type: ignore[arg-type]
        # t=0 UNKNOWN, t=1 RUNNING (reset), t=2,3,4 UNKNOWN: returns at t=4.
        assert res.wait(poll_interval=1, unknown_after=2) is JobStatus.UNKNOWN
        assert sched.calls == 5
        assert clock[0] == 4

    def test_unknown_then_terminal(self):
        sched = _Sequence([JobStatus.UNKNOWN, JobStatus.UNKNOWN, JobStatus.FAILED])
        res = JobResult(scheduler=sched, job_id="1")  # type: ignore[arg-type]
        assert res.wait(poll_interval=0, unknown_after=60) is JobStatus.FAILED

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
        res.stdout_path = out
        assert res.read_stdout(tail=0) == ""
        assert res.read_stdout(tail=2) == "b\nc"
        assert res.read_stdout() == "a\nb\nc\n"


class TestOutputPaths:
    def test_missing_file_is_none(self, tmp_path):
        res = _result(JobStatus.COMPLETED, 0)
        res.stdout_path = tmp_path / "never-written.out"
        assert res.read_stdout() is None

    def test_host_dependent_path_raises(self):
        res = _result(JobStatus.COMPLETED, 0)
        res.job = make_job(stdout="out.${hpc.host}")
        with pytest.raises(ValueError, match=r"depends on \$\{hpc.host\}"):
            res.read_stdout()

    def test_handle_from_id_is_none(self):
        res = _result(JobStatus.COMPLETED, 0)
        res.job = None
        assert res.read_stdout() is None

    def test_array_whole_job_read_refused(self):
        res = ArrayJobResult(scheduler=MagicMock(), job_id="9", array=ArrayRange.parse("1-2"))
        with pytest.raises(NotSupportedError, match=r"array job: use read_task_stdout\(n\)"):
            res.read_stdout()
        with pytest.raises(NotSupportedError, match="array job"):
            res.read_stderr()

    def test_array_read_task(self, tmp_path):
        res = ArrayJobResult(
            scheduler=MagicMock(),
            job_id="9",
            array=ArrayRange.parse("1-2"),
            stdout_path=tmp_path / "a.9.${hpc.task_id}",
        )
        (tmp_path / "a.9.2").write_text("x\ny\nz\n")
        assert res.read_task_stdout(2) == "x\ny\nz\n"
        assert res.read_task_stdout(2, tail=1) == "z"
        assert res.read_task_stdout(1) is None  # not written
        assert res.read_task_stderr(2) is None  # merged

    def test_array_task_paths(self):
        res = ArrayJobResult(
            scheduler=MagicMock(),
            job_id="9",
            array=ArrayRange.parse("1-4"),
            stdout_path=Path("/w/a.9.${hpc.task_id}"),
        )
        assert res.task_stdout_path(3) == Path("/w/a.9.3")
        assert res.task_stderr_path(3) is None


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
        assert self._interactive(0).stdout_path is None
        assert self._interactive(0).read_stdout() is None
        assert self._interactive(0).read_stderr() is None


class TestHandleWithoutJob:
    def test_job_is_optional(self):
        res = _result(JobStatus.RUNNING, None)
        res.job = None
        assert res.status is JobStatus.RUNNING


class TestArrayJobResult:
    def _array(self, statuses: dict[str, JobStatus]) -> ArrayJobResult:
        scheduler = MagicMock()
        scheduler.get_statuses.side_effect = lambda ids: {i: statuses[i] for i in ids}
        scheduler.get_status.side_effect = lambda job_id: statuses[job_id]
        scheduler.task_job_id.side_effect = lambda base, index: f"{base}.{index}"
        return ArrayJobResult(scheduler=scheduler, job_id="7", array=ArrayRange(1, 3))

    def test_is_a_job_result(self):
        assert isinstance(self._array({}), JobResult)

    def test_task_id_delegates_to_scheduler(self):
        res = self._array({})
        assert res.task_id(2) == "7.2"
        res.scheduler.task_job_id.assert_called_once_with("7", 2)

    def test_wait_returns_the_array_status_one_failed_task_fails_the_array(self):
        res = self._array(
            {"7.1": JobStatus.COMPLETED, "7.2": JobStatus.FAILED, "7.3": JobStatus.COMPLETED}
        )
        assert res.wait(poll_interval=0) is JobStatus.FAILED
        tasks = res.task_statuses()
        assert tasks.tasks == {1: JobStatus.COMPLETED, 2: JobStatus.FAILED, 3: JobStatus.COMPLETED}
        assert tasks.overall is JobStatus.FAILED
        assert tasks.failed == [2] and tasks.unfinished == []
        assert tasks.counts == {JobStatus.COMPLETED: 2, JobStatus.FAILED: 1}
        assert tasks.is_terminal

    def test_status_is_the_reduction_and_active_while_any_task_is(self):
        res = self._array(
            {"7.1": JobStatus.COMPLETED, "7.2": JobStatus.RUNNING, "7.3": JobStatus.PENDING}
        )
        assert res.status is JobStatus.RUNNING
        assert not res.is_complete
        tasks = res.task_statuses()
        assert tasks.unfinished == [2, 3] and not tasks.is_terminal
        res.scheduler.get_statuses.assert_called_with(["7.1", "7.2", "7.3"])

    def test_all_completed_is_completed(self):
        res = self._array({f"7.{i}": JobStatus.COMPLETED for i in (1, 2, 3)})
        assert res.status is JobStatus.COMPLETED
        assert res.task_statuses().failed == []

    def test_wait_timeout(self):
        res = self._array({f"7.{i}": JobStatus.RUNNING for i in (1, 2, 3)})
        with pytest.raises(TimeoutError):
            res.wait(poll_interval=0, timeout=0)

    def test_wait_one_bulk_query_per_tick(self):
        res = self._array({f"7.{i}": JobStatus.COMPLETED for i in (1, 2, 3)})
        res.wait(poll_interval=0)
        res.scheduler.get_statuses.assert_called_once_with(["7.1", "7.2", "7.3"])
        res.scheduler.get_status.assert_not_called()

    def test_wait_unknown_after(self):
        res = self._array(
            {"7.1": JobStatus.COMPLETED, "7.2": JobStatus.UNKNOWN, "7.3": JobStatus.SKIPPED}
        )
        assert res.wait(poll_interval=0, unknown_after=0) is JobStatus.UNKNOWN
        assert res.task_statuses().tasks == {
            1: JobStatus.COMPLETED,
            2: JobStatus.UNKNOWN,
            3: JobStatus.SKIPPED,
        }

    def test_task_status_is_per_task(self):
        res = self._array({"7.1": JobStatus.COMPLETED, "7.2": JobStatus.RUNNING})
        assert res.task_status(1) is JobStatus.COMPLETED
        assert res.task_status(2) is JobStatus.RUNNING

    def test_cancel_uses_base_id(self):
        res = self._array({})
        res.cancel()
        res.scheduler.cancel.assert_called_once_with("7")

    def test_result_raises_naming_the_failed_tasks(self):
        res = self._array(
            {"7.1": JobStatus.FAILED, "7.2": JobStatus.COMPLETED, "7.3": JobStatus.TIMEOUT}
        )
        with pytest.raises(JobFailedError) as info:
            res.result(poll_interval=0)
        assert info.value.status is JobStatus.FAILED
        assert info.value.failed_tasks == [1, 3]
        assert "tasks 1, 3" in str(info.value)

    def test_result_returns_the_exit_code_when_every_task_completed(self):
        res = self._array({f"7.{i}": JobStatus.COMPLETED for i in (1, 2, 3)})
        res.scheduler.get_exit_code.return_value = 0
        assert res.result(poll_interval=0) == 0
        res.scheduler.get_exit_code.assert_called_once_with("7")

    def test_task_exit_code_is_per_task(self):
        res = self._array({})
        res.scheduler.get_exit_code.return_value = 3
        assert res.task_exit_code(2) == 3
        res.scheduler.get_exit_code.assert_called_once_with("7.2")


class TestReduceStatuses:
    def test_empty_is_an_error(self):
        with pytest.raises(ValueError):
            reduce_statuses([])

    @pytest.mark.parametrize(
        ("statuses", "want"),
        [
            ([JobStatus.COMPLETED], JobStatus.COMPLETED),
            ([JobStatus.COMPLETED, JobStatus.COMPLETED], JobStatus.COMPLETED),
            ([JobStatus.COMPLETED, JobStatus.FAILED], JobStatus.FAILED),
            ([JobStatus.SKIPPED, JobStatus.COMPLETED], JobStatus.SKIPPED),
            ([JobStatus.SKIPPED, JobStatus.TIMEOUT], JobStatus.TIMEOUT),
            ([JobStatus.CANCELLED, JobStatus.FAILED], JobStatus.FAILED),
            ([JobStatus.TIMEOUT, JobStatus.FAILED], JobStatus.FAILED),
            # One task the scheduler cannot report on: not a result.
            ([JobStatus.COMPLETED, JobStatus.UNKNOWN], JobStatus.UNKNOWN),
            ([JobStatus.FAILED, JobStatus.UNKNOWN], JobStatus.UNKNOWN),
            # Anything active keeps the whole active, running first.
            ([JobStatus.FAILED, JobStatus.PENDING], JobStatus.PENDING),
            ([JobStatus.PENDING, JobStatus.SUSPENDED], JobStatus.SUSPENDED),
            ([JobStatus.SUSPENDED, JobStatus.RUNNING, JobStatus.UNKNOWN], JobStatus.RUNNING),
        ],
    )
    def test_order(self, statuses, want):
        assert reduce_statuses(statuses) is want
        assert reduce_statuses(reversed(statuses)) is want
