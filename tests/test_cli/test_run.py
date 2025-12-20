"""Tests for CLI run command."""

from click.testing import CliRunner

import pytest

from hpc_runner.cli.main import cli


class TestRunCommand:
    """Tests for hpc run command."""

    @pytest.fixture
    def runner(self):
        """Create CLI runner."""
        return CliRunner()

    def test_run_help(self, runner):
        """Test run --help."""
        result = runner.invoke(cli, ["run", "--help"])
        assert result.exit_code == 0
        assert "Submit a job" in result.output

    def test_run_dry_run(self, runner, temp_dir):
        """Test run with --dry-run."""
        result = runner.invoke(
            cli,
            ["--scheduler", "local", "run", "--dry-run", "echo", "hello"],
        )
        assert result.exit_code == 0
        assert "Dry Run" in result.output
        assert "echo hello" in result.output

    def test_run_with_options(self, runner, temp_dir):
        """Test run with various options."""
        result = runner.invoke(
            cli,
            [
                "--scheduler", "local",
                "run",
                "--dry-run",
                "--name", "test_job",
                "--cpu", "4",
                "--mem", "16G",
                "python", "script.py",
            ],
        )
        assert result.exit_code == 0
        assert "test_job" in result.output
        assert "python script.py" in result.output

    def test_run_local_job(self, runner, temp_dir):
        """Test running a local job."""
        with runner.isolated_filesystem(temp_dir=temp_dir):
            result = runner.invoke(
                cli,
                ["run", "--local", "echo", "hello"],
            )
            assert result.exit_code == 0
            assert "Submitted job" in result.output or "local_" in result.output

    def test_run_interactive_local(self, runner, temp_dir):
        """Test running an interactive local job."""
        with runner.isolated_filesystem(temp_dir=temp_dir):
            result = runner.invoke(
                cli,
                ["run", "--local", "--interactive", "echo", "hello"],
            )
            assert result.exit_code == 0
            assert "completed successfully" in result.output


class TestMainCLI:
    """Tests for main CLI."""

    @pytest.fixture
    def runner(self):
        return CliRunner()

    def test_help(self, runner):
        """Test --help."""
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "HPC job submission tool" in result.output

    def test_version(self, runner):
        """Test --version."""
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "version" in result.output

    def test_commands_listed(self, runner):
        """Test that commands are listed."""
        result = runner.invoke(cli, ["--help"])
        assert "run" in result.output
        assert "status" in result.output
        assert "cancel" in result.output
        assert "config" in result.output
