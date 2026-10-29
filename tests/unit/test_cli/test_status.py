"""Tests for CLI status command."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from hpc_runner.cli.main import cli
from hpc_runner.core.job_info import JobInfo
from hpc_runner.core.result import JobStatus


@pytest.fixture
def runner():
    return CliRunner(env={"COLUMNS": "200"})


@pytest.fixture
def mock_scheduler():
    """Return a mock scheduler with sensible defaults."""
    sched = MagicMock()
    sched.name = "sge"
    sched.has_accounting.return_value = True
    sched.list_active_jobs.return_value = []
    sched.list_completed_jobs.return_value = []
    return sched


def _patch_scheduler(mock_sched):
    """Context manager that patches get_scheduler to return *mock_sched*."""
    return patch(
        "hpc_runner.schedulers.get_scheduler",
        return_value=mock_sched,
    )


# ------------------------------------------------------------------ #
# Help
# ------------------------------------------------------------------ #


class TestStatusHelp:
    def test_help_output(self, runner):
        result = runner.invoke(cli, ["status", "--help"])
        assert result.exit_code == 0
        assert "--history" in result.output
        assert "--since" in result.output
        assert "--json" in result.output
        assert "--verbose" in result.output
        assert "--all" in result.output


# ------------------------------------------------------------------ #
# Validation
# ------------------------------------------------------------------ #


class TestStatusValidation:
    def test_history_with_job_id_errors(self, runner, mock_scheduler):
        with _patch_scheduler(mock_scheduler):
            result = runner.invoke(cli, ["status", "--history", "12345"])
        assert result.exit_code != 0
        assert "Cannot combine" in result.output

    def test_since_implies_history(self, runner, mock_scheduler):
        """--since without --history should implicitly enable history mode."""
        with _patch_scheduler(mock_scheduler):
            result = runner.invoke(cli, ["status", "--since", "30m"])
        assert result.exit_code == 0
        mock_scheduler.list_completed_jobs.assert_called_once()


# ------------------------------------------------------------------ #
# Mode 1: Active jobs
# ------------------------------------------------------------------ #


class TestActiveJobs:
    def test_no_active_jobs(self, runner, mock_scheduler):
        mock_scheduler.list_active_jobs.return_value = []
        with _patch_scheduler(mock_scheduler):
            result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0
        assert "No active jobs" in result.output

    def test_active_jobs_table(self, runner, mock_scheduler):
        mock_scheduler.list_active_jobs.return_value = [
            JobInfo(
                job_id="100",
                name="my_sim",
                user="alice",
                status=JobStatus.RUNNING,
                queue="batch.q",
                submit_time=datetime(2026, 2, 23, 10, 0, 0),
            ),
        ]
        with _patch_scheduler(mock_scheduler):
            result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0
        assert "100" in result.output
        assert "my_sim" in result.output
        assert "RUNNING" in result.output

    def test_active_jobs_shows_all_columns(self, runner, mock_scheduler):
        """Default output includes queue, node, cpu, runtime columns."""
        mock_scheduler.list_active_jobs.return_value = [
            JobInfo(
                job_id="100",
                name="my_sim",
                user="alice",
                status=JobStatus.RUNNING,
                queue="batch.q",
                node="node1",
                cpu=4,
                runtime=timedelta(minutes=10),
            ),
        ]
        with _patch_scheduler(mock_scheduler):
            result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0
        assert "batch.q" in result.output
        assert "node1" in result.output

    def test_active_jobs_all_users(self, runner, mock_scheduler):
        mock_scheduler.list_active_jobs.return_value = []
        with _patch_scheduler(mock_scheduler):
            result = runner.invoke(cli, ["status", "--all"])
        assert result.exit_code == 0
        mock_scheduler.list_active_jobs.assert_called_once_with(user=None)

    def test_active_jobs_json(self, runner, mock_scheduler):
        mock_scheduler.list_active_jobs.return_value = [
            JobInfo(
                job_id="100",
                name="my_sim",
                user="alice",
                status=JobStatus.RUNNING,
            ),
        ]
        with _patch_scheduler(mock_scheduler):
            result = runner.invoke(cli, ["status", "--json"])
        assert result.exit_code == 0
        assert '"job_id"' in result.output
        assert '"100"' in result.output


# ------------------------------------------------------------------ #
# Mode 2: History
# ------------------------------------------------------------------ #


class TestHistory:
    def test_no_completed_jobs(self, runner, mock_scheduler):
        mock_scheduler.list_completed_jobs.return_value = []
        with _patch_scheduler(mock_scheduler):
            result = runner.invoke(cli, ["status", "--history"])
        assert result.exit_code == 0
        assert "No completed jobs" in result.output

    def test_completed_jobs_table(self, runner, mock_scheduler):
        mock_scheduler.list_completed_jobs.return_value = [
            JobInfo(
                job_id="200",
                name="done_sim",
                user="alice",
                status=JobStatus.COMPLETED,
                exit_code=0,
                end_time=datetime(2026, 2, 23, 12, 0, 0),
            ),
        ]
        with _patch_scheduler(mock_scheduler):
            result = runner.invoke(cli, ["status", "--history"])
        assert result.exit_code == 0
        assert "200" in result.output
        assert "done_sim" in result.output
        assert "COMPLETED" in result.output

    def test_history_with_since(self, runner, mock_scheduler):
        mock_scheduler.list_completed_jobs.return_value = []
        with _patch_scheduler(mock_scheduler):
            result = runner.invoke(cli, ["status", "--history", "--since", "2h"])
        assert result.exit_code == 0

    def test_history_json(self, runner, mock_scheduler):
        mock_scheduler.list_completed_jobs.return_value = [
            JobInfo(
                job_id="200",
                name="done_sim",
                user="alice",
                status=JobStatus.FAILED,
                exit_code=1,
            ),
        ]
        with _patch_scheduler(mock_scheduler):
            result = runner.invoke(cli, ["status", "--history", "--json"])
        assert result.exit_code == 0
        assert '"exit_code"' in result.output

    def test_history_accounting_not_available(self, runner, mock_scheduler):
        mock_scheduler.has_accounting.return_value = False
        with _patch_scheduler(mock_scheduler):
            result = runner.invoke(cli, ["status", "--history"])
        assert result.exit_code != 0


# ------------------------------------------------------------------ #
# Mode 3: Single job
# ------------------------------------------------------------------ #


class TestSingleJob:
    def test_single_job_detail(self, runner, mock_scheduler):
        mock_scheduler.get_job_details.return_value = (
            JobInfo(
                job_id="300",
                name="my_job",
                user="alice",
                status=JobStatus.RUNNING,
                queue="batch.q",
                cpu=8,
            ),
            {},
        )
        with _patch_scheduler(mock_scheduler):
            result = runner.invoke(cli, ["status", "300"])
        assert result.exit_code == 0
        assert "300" in result.output
        assert "my_job" in result.output
        assert "RUNNING" in result.output

    def test_single_job_fallback(self, runner, mock_scheduler):
        """Falls back to get_status + get_exit_code when get_job_details raises."""
        mock_scheduler.get_job_details.side_effect = NotImplementedError
        mock_scheduler.get_status.return_value = JobStatus.COMPLETED
        mock_scheduler.get_exit_code.return_value = 0
        with _patch_scheduler(mock_scheduler):
            result = runner.invoke(cli, ["status", "300"])
        assert result.exit_code == 0
        assert "COMPLETED" in result.output

    def test_single_job_json(self, runner, mock_scheduler):
        mock_scheduler.get_job_details.return_value = (
            JobInfo(
                job_id="300",
                name="my_job",
                user="alice",
                status=JobStatus.FAILED,
                exit_code=42,
            ),
            {"cwd": "/home/alice/work"},
        )
        with _patch_scheduler(mock_scheduler):
            result = runner.invoke(cli, ["status", "300", "--json"])
        assert result.exit_code == 0
        assert '"300"' in result.output
        assert '"cwd"' in result.output

    def test_single_job_verbose_with_extra(self, runner, mock_scheduler):
        """--verbose on single job shows extra scheduler details."""
        mock_scheduler.get_job_details.return_value = (
            JobInfo(
                job_id="300",
                name="my_job",
                user="alice",
                status=JobStatus.RUNNING,
            ),
            {"pe_name": "smp", "pe_range": "4"},
        )
        with _patch_scheduler(mock_scheduler):
            result = runner.invoke(cli, ["status", "300", "--verbose"])
        assert result.exit_code == 0
        assert "smp" in result.output


# ------------------------------------------------------------------ #
# --watch
# ------------------------------------------------------------------ #


class TestWatch:
    def test_watch_not_yet_implemented(self, runner, mock_scheduler):
        with _patch_scheduler(mock_scheduler):
            result = runner.invoke(cli, ["status", "--watch"])
        assert result.exit_code == 0
        assert "not yet implemented" in result.output


def test_skipped_has_a_style():
    from hpc_runner.cli.status import _status_style

    assert _status_style("SKIPPED") == "[magenta]SKIPPED[/magenta]"
