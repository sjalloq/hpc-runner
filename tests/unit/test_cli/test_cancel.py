"""Tests for `hpc cancel`: selection by id or filter, one confirmation, exit code."""

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from hpc_runner.cli.main import cli
from hpc_runner.core.job_info import JobInfo
from hpc_runner.core.result import JobStatus


def _job(job_id: str, name: str, status: JobStatus) -> JobInfo:
    return JobInfo(job_id=job_id, name=name, user="me", status=status, queue="all.q")


JOBS = [
    _job("11", "tc_a", JobStatus.RUNNING),
    _job("12", "tc_b", JobStatus.PENDING),
    _job("13", "sim", JobStatus.SUSPENDED),
]


@pytest.fixture
def runner():
    return CliRunner(env={"COLUMNS": "200"})


@pytest.fixture
def scheduler():
    sched = MagicMock()
    sched.name = "sge"
    sched.list_active_jobs.return_value = list(JOBS)
    sched.cancel.return_value = True
    with patch("hpc_runner.schedulers.get_scheduler", return_value=sched):
        yield sched


def _cancelled(scheduler) -> list[str]:
    return [call.args[0] for call in scheduler.cancel.call_args_list]


def test_kill_is_gone(runner):
    result = runner.invoke(cli, ["kill", "--all"])
    assert result.exit_code == 2
    assert "No such command" in result.output


class TestSelection:
    def test_ids(self, runner, scheduler):
        result = runner.invoke(cli, ["cancel", "--force", "11", "12"])
        assert result.exit_code == 0, result.output
        assert _cancelled(scheduler) == ["11", "12"]
        assert "tc_a" in result.output and "Cancelled 2 job(s)" in result.output

    def test_a_repeated_id_is_cancelled_once(self, runner, scheduler):
        runner.invoke(cli, ["cancel", "--force", "11", "11"])
        assert _cancelled(scheduler) == ["11"]

    def test_name_pattern(self, runner, scheduler):
        result = runner.invoke(cli, ["cancel", "--force", "-N", "tc_.*"])
        assert result.exit_code == 0, result.output
        assert _cancelled(scheduler) == ["11", "12"]

    @pytest.mark.parametrize(
        ("flags", "expected"),
        [
            ([], ["11", "12", "13"]),
            (["--pending"], ["12"]),
            (["--running"], ["11", "13"]),
            (["--pending", "--running"], ["11", "12", "13"]),
        ],
    )
    def test_all_narrowed_by_state(self, runner, scheduler, flags, expected):
        result = runner.invoke(cli, ["cancel", "--force", "--all", *flags])
        assert result.exit_code == 0, result.output
        assert _cancelled(scheduler) == expected

    def test_a_filter_lists_only_the_callers_jobs(self, runner, scheduler):
        with patch("hpc_runner.cli.cancel.current_user", return_value="me"):
            runner.invoke(cli, ["cancel", "--force", "--all"])
        scheduler.list_active_jobs.assert_called_once_with(user="me")

    def test_no_match_cancels_nothing(self, runner, scheduler):
        result = runner.invoke(cli, ["cancel", "--force", "-N", "nothing"])
        assert result.exit_code == 0
        assert "No active jobs match" in result.output
        assert _cancelled(scheduler) == []


class TestUsage:
    @pytest.mark.parametrize(
        ("args", "message"),
        [
            ([], "No jobs selected"),
            (["11", "--all"], "not both"),
            (["11", "-N", "x"], "not both"),
            (["11", "--pending"], "apply only with -N or --all"),
            (["-N", "("], "Invalid regex"),
        ],
    )
    def test_rejected(self, runner, scheduler, args, message):
        result = runner.invoke(cli, ["cancel", *args])
        assert result.exit_code == 2
        assert message in result.output
        assert _cancelled(scheduler) == []


class TestConfirmation:
    @pytest.mark.parametrize("args", [["11", "12"], ["--all"]])
    def test_asks_once_and_yes_cancels(self, runner, scheduler, args):
        result = runner.invoke(cli, ["cancel", *args], input="y\n")
        assert result.exit_code == 0, result.output
        assert result.output.count("Proceed?") == 1
        assert "Jobs to cancel" in result.output
        assert len(_cancelled(scheduler)) == (3 if args == ["--all"] else len(args))

    @pytest.mark.parametrize("args", [["11"], ["--all"]])
    def test_no_exits_0_and_cancels_nothing(self, runner, scheduler, args):
        result = runner.invoke(cli, ["cancel", *args], input="n\n")
        assert result.exit_code == 0
        assert "Aborted" in result.output
        assert _cancelled(scheduler) == []

    def test_force_does_not_ask(self, runner, scheduler):
        result = runner.invoke(cli, ["cancel", "--force", "11"])
        assert "Proceed?" not in result.output


class TestFailure:
    def test_a_refused_job_exits_1_after_trying_the_rest(self, runner, scheduler):
        scheduler.cancel.side_effect = lambda job_id: job_id != "11"
        result = runner.invoke(cli, ["cancel", "--force", "11", "12"])
        assert result.exit_code == 1
        assert _cancelled(scheduler) == ["11", "12"]
        assert "Failed to cancel job 11 (tc_a)" in result.output
        assert "Cancelled 1 job(s)" in result.output

    def test_an_id_that_is_not_active_is_shown_tried_and_reported(self, runner, scheduler):
        scheduler.cancel.return_value = False
        result = runner.invoke(cli, ["cancel", "--force", "999"])
        assert result.exit_code == 1
        assert "not active" in result.output
        assert _cancelled(scheduler) == ["999"]
        assert "Failed to cancel job 999" in result.output
