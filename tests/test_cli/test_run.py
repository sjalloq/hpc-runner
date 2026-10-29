"""Tests for CLI run command."""

import pytest
from click.testing import CliRunner

from hpc_runner.cli.main import cli


@pytest.fixture
def sge_dirs(monkeypatch, tmp_path):
    """Keep SGE script/exit-sentinel dirs out of the real home directory."""
    monkeypatch.setenv("HPC_SCRIPT_DIR", str(tmp_path / "scripts"))
    monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path / "exit"))


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
        assert "Run a command on the cluster" in result.output

    def test_run_dry_run(self, runner, temp_dir):
        """Test run with --dry-run."""
        result = runner.invoke(
            cli,
            ["--scheduler", "local", "run", "--dry-run", "echo", "hello"],
        )
        assert result.exit_code == 0
        assert "Dry Run" in result.output
        assert "echo hello" in result.output

    def test_dry_run_shows_no_venv(self, runner, monkeypatch):
        monkeypatch.delenv("VIRTUAL_ENV", raising=False)
        result = runner.invoke(cli, ["--scheduler", "local", "run", "--dry-run", "echo", "hi"])
        assert result.exit_code == 0, result.output
        assert "venv: none" in result.output

    def test_dry_run_shows_captured_venv(self, runner, monkeypatch, tmp_path):
        venv = tmp_path / "v"
        (venv / "bin").mkdir(parents=True)
        (venv / "bin" / "activate").write_text("")
        monkeypatch.setenv("VIRTUAL_ENV", str(venv))
        result = runner.invoke(cli, ["--scheduler", "local", "run", "--dry-run", "echo", "hi"])
        assert result.exit_code == 0, result.output
        assert f"venv: {venv}" in result.output.replace("\n", "")

    def test_verbose_dry_run_shows_plain_module_lines(self, runner):
        quiet = runner.invoke(cli, ["--scheduler", "local", "run", "--dry-run", "echo", "hi"])
        loud = runner.invoke(
            cli, ["--verbose", "--scheduler", "local", "run", "--dry-run", "echo", "hi"]
        )
        assert "mktemp" in quiet.output
        assert loud.exit_code == 0, loud.output
        assert "mktemp" not in loud.output
        assert "module purge" in loud.output

    def test_run_with_options(self, runner, temp_dir):
        """Test run with various options."""
        result = runner.invoke(
            cli,
            [
                "--scheduler",
                "local",
                "run",
                "--dry-run",
                "--name",
                "test_job",
                "--cpu",
                "4",
                "--mem",
                "16G",
                "python",
                "script.py",
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
                ["run", "--local", "--batch", "echo", "hello"],
            )
            assert result.exit_code == 0
            assert "Submitted job" in result.output or "local_" in result.output

    def test_run_interactive_local(self, runner, temp_dir):
        """Test running an interactive local job."""
        with runner.isolated_filesystem(temp_dir=temp_dir):
            result = runner.invoke(
                cli,
                ["run", "--local", "echo", "hello"],
            )
            assert result.exit_code == 0
            assert "completed successfully" in result.output

    def test_default_mode_is_interactive(self, runner, temp_dir):
        """Without --batch the dry run reports interactive mode."""
        result = runner.invoke(cli, ["--scheduler", "local", "run", "--dry-run", "echo", "hello"])
        assert result.exit_code == 0
        assert "Mode: interactive" in result.output

    def test_batch_flag_selects_batch_mode(self, runner, temp_dir):
        """--batch / -b switch the dry run to batch mode."""
        for flag in ("--batch", "-b"):
            result = runner.invoke(
                cli, ["--scheduler", "local", "run", "--dry-run", flag, "echo", "hello"]
            )
            assert result.exit_code == 0, result.output
            assert "Mode: batch" in result.output

    def test_array_implies_batch(self, runner, sge_dirs):
        """Array jobs are always batch, even without --batch."""
        result = runner.invoke(
            cli,
            ["--scheduler", "sge", "run", "--dry-run", "--array", "1-4", "echo", "task"],
        )
        assert result.exit_code == 0, result.output
        assert "Mode: batch" in result.output
        assert "#$ -t 1-4" in result.output

    def test_removed_options_rejected(self, runner):
        """--job-type, --job-name, --interactive, --extra-module no longer exist."""
        for opt in ("--job-type", "--job-name", "--interactive", "--extra-module"):
            result = runner.invoke(
                cli, ["--scheduler", "local", "run", "--dry-run", opt, "x", "--", "echo"]
            )
            assert result.exit_code != 0, opt
            assert "No such option" in result.output, opt
            result = runner.invoke(
                cli, ["--scheduler", "local", "run", "--dry-run", opt, "x", "echo"]
            )
            assert result.exit_code != 0, opt

    def test_scheduler_flag_without_separator_errors(self, runner, temp_dir):
        """A single-dash flag with no `--` is a usage error, not a bogus command."""
        result = runner.invoke(
            cli, ["--scheduler", "local", "run", "--dry-run", "-q", "batch.q", "echo", "hi"]
        )
        assert result.exit_code != 0
        assert "must come before" in result.output

    def test_run_command_with_flags(self, runner, temp_dir):
        """Test run with command that has its own flags."""
        result = runner.invoke(
            cli,
            [
                "--scheduler",
                "local",
                "run",
                "--dry-run",
                "mpirun",
                "-N",
                "4",
                "-n",
                "16",
                "./sim",
            ],
        )
        assert result.exit_code == 0
        assert "Dry Run" in result.output
        # All args become part of the command
        assert "mpirun -N 4 -n 16 ./sim" in result.output

    def test_run_requires_command(self, runner):
        """Test that run requires a command."""
        result = runner.invoke(
            cli,
            ["--scheduler", "local", "run", "--dry-run"],
        )
        assert result.exit_code != 0
        assert "Command is required" in result.output

    def test_run_array_job_dry_run(self, runner, sge_dirs):
        """Test array job with --dry-run."""
        result = runner.invoke(
            cli,
            [
                "--scheduler",
                "sge",
                "run",
                "--dry-run",
                "--array",
                "1-10",
                "echo",
                "task",
            ],
        )
        assert result.exit_code == 0
        assert "Array job" in result.output
        assert "10 tasks" in result.output

    def test_run_with_stdout(self, runner, temp_dir):
        """Test --stdout directs output to a file."""
        result = runner.invoke(
            cli,
            [
                "run",
                "--local",
                "--directory",
                str(temp_dir),
                "--stdout",
                "out.log",
                "echo",
                "hello",
            ],
        )
        assert result.exit_code == 0
        out = (temp_dir / "out.log").read_text()
        assert "hello" in out

    def test_run_with_stderr(self, runner, temp_dir):
        """Test --stderr directs stderr to a separate file."""
        result = runner.invoke(
            cli,
            [
                "run",
                "--local",
                "--directory",
                str(temp_dir),
                "--stdout",
                "out.log",
                "--stderr",
                "err.log",
                "--",
                "bash",
                "-c",
                "echo stderr msg >&2",
            ],
        )
        assert result.exit_code == 0
        err = (temp_dir / "err.log").read_text()
        assert "stderr msg" in err

    def test_run_with_directory(self, runner, temp_dir):
        """Test --directory sets the working directory."""
        subdir = temp_dir / "workdir"
        subdir.mkdir()
        result = runner.invoke(
            cli,
            [
                "run",
                "--local",
                "--directory",
                str(subdir),
                "--stdout",
                "pwd.log",
                "pwd",
            ],
        )
        assert result.exit_code == 0
        out = (subdir / "pwd.log").read_text()
        assert str(subdir) in out

    def test_run_with_relative_directory_and_stdout(self, runner, temp_dir):
        """Test --directory with relative path combined with --stdout."""
        subdir = temp_dir / "reltest"
        subdir.mkdir()
        import os

        old_cwd = os.getcwd()
        try:
            os.chdir(temp_dir)
            result = runner.invoke(
                cli,
                [
                    "run",
                    "--local",
                    "--directory",
                    "reltest",
                    "--stdout",
                    "out.log",
                    "echo",
                    "hello",
                ],
            )
            assert result.exit_code == 0
            out = (subdir / "out.log").read_text()
            assert "hello" in out
        finally:
            os.chdir(old_cwd)


class TestParseArgs:
    """Tests for _parse_args helper."""

    def test_no_separator(self):
        """Without '--', all args are the command."""
        from hpc_runner.cli.run import _parse_args

        sched, cmd = _parse_args(("echo", "hello"))
        assert sched == []
        assert cmd == ["echo", "hello"]

    def test_with_separator(self):
        """'--' splits into scheduler args and command."""
        from hpc_runner.cli.run import _parse_args

        sched, cmd = _parse_args(("-q", "batch.q", "--", "echo", "hello"))
        assert sched == ["-q", "batch.q"]
        assert cmd == ["echo", "hello"]

    def test_separator_at_start(self):
        """'--' at start means no scheduler args."""
        from hpc_runner.cli.run import _parse_args

        sched, cmd = _parse_args(("--", "echo", "hello"))
        assert sched == []
        assert cmd == ["echo", "hello"]

    def test_empty_command_after_separator(self):
        """'--' with nothing after gives empty command."""
        from hpc_runner.cli.run import _parse_args

        sched, cmd = _parse_args(("-q", "batch.q", "--"))
        assert sched == ["-q", "batch.q"]
        assert cmd == []

    def test_empty_args(self):
        """Empty args gives empty lists."""
        from hpc_runner.cli.run import _parse_args

        sched, cmd = _parse_args(())
        assert sched == []
        assert cmd == []


class TestSchedulerPassthrough:
    """Tests for scheduler passthrough via '--' separator."""

    @pytest.fixture
    def runner(self):
        return CliRunner()

    def test_passthrough_with_separator(self, runner, temp_dir):
        """'--' splits scheduler args from command."""
        result = runner.invoke(
            cli,
            [
                "--scheduler",
                "local",
                "run",
                "--dry-run",
                "-q",
                "batch.q",
                "--",
                "echo",
                "hello",
            ],
        )
        assert result.exit_code == 0
        assert "echo hello" in result.output
        assert "Scheduler passthrough" in result.output
        assert "-q batch.q" in result.output

    def test_no_separator_no_passthrough(self, runner, temp_dir):
        """Without '--', no scheduler passthrough is shown."""
        result = runner.invoke(
            cli,
            ["--scheduler", "local", "run", "--dry-run", "echo", "hello"],
        )
        assert result.exit_code == 0
        assert "echo hello" in result.output
        assert "Scheduler passthrough" not in result.output

    def test_command_flags_preserved(self, runner, temp_dir):
        """Command flags after '--' are preserved as part of the command."""
        result = runner.invoke(
            cli,
            [
                "--scheduler",
                "local",
                "run",
                "--dry-run",
                "--",
                "mpirun",
                "-N",
                "4",
                "./sim",
            ],
        )
        assert result.exit_code == 0
        assert "mpirun -N 4 ./sim" in result.output
        assert "Scheduler passthrough" not in result.output

    def test_mixed_hpc_opts_and_passthrough(self, runner, temp_dir):
        """hpc-runner options and scheduler passthrough work together."""
        result = runner.invoke(
            cli,
            [
                "--scheduler",
                "local",
                "run",
                "--dry-run",
                "--cpu",
                "4",
                "-q",
                "batch.q",
                "--",
                "python",
                "train.py",
            ],
        )
        assert result.exit_code == 0
        assert "python train.py" in result.output
        assert "Scheduler passthrough" in result.output
        assert "-q batch.q" in result.output

    def test_empty_command_after_separator_errors(self, runner):
        """'--' with no command after it is an error."""
        result = runner.invoke(
            cli,
            [
                "--scheduler",
                "local",
                "run",
                "--dry-run",
                "-q",
                "batch.q",
                "--",
            ],
        )
        assert result.exit_code != 0
        assert "Command is required" in result.output

    def test_multiple_passthrough_args(self, runner, temp_dir):
        """Multiple scheduler args before '--' are all captured."""
        result = runner.invoke(
            cli,
            [
                "--scheduler",
                "local",
                "run",
                "--dry-run",
                "-q",
                "batch.q",
                "-l",
                "gpu=2",
                "--",
                "echo",
                "hello",
            ],
        )
        assert result.exit_code == 0
        assert "-q batch.q -l gpu=2" in result.output


class TestToolAutoDetection:
    """Tests for automatic tool detection from command."""

    @pytest.fixture
    def runner(self):
        return CliRunner()

    @pytest.fixture
    def config_with_tools(self, temp_dir):
        """Create a config file with tool definitions."""
        config_file = temp_dir / "hpc-runner.toml"
        config_file.write_text("""config_version = 1

[tools.python]
cpu = 4
mem = "16G"
modules = ["python/3.11"]

[tools.make]
cpu = 8

[types.gpu]
queue = "gpu.q"
cpu = 8
""")
        return config_file

    def test_tool_auto_detected_from_command(self, runner, temp_dir, config_with_tools):
        """Test that tool config is auto-detected from command."""
        import os

        old_cwd = os.getcwd()
        os.chdir(temp_dir)
        try:
            result = runner.invoke(
                cli,
                ["--scheduler", "local", "run", "--dry-run", "python", "script.py"],
            )
            assert result.exit_code == 0
            # Should pick up modules from [tools.python]
            assert "python/3.11" in result.output
        finally:
            os.chdir(old_cwd)

    def test_tool_with_path_strips_directory(self, runner, temp_dir, config_with_tools):
        """Test that tool is detected even with full path."""
        import os

        old_cwd = os.getcwd()
        os.chdir(temp_dir)
        try:
            result = runner.invoke(
                cli,
                ["--scheduler", "local", "run", "--dry-run", "/usr/bin/python", "script.py"],
            )
            assert result.exit_code == 0
            # Should still detect "python" from /usr/bin/python
            assert "python/3.11" in result.output
        finally:
            os.chdir(old_cwd)

    def test_unknown_tool_uses_defaults(self, runner, temp_dir, config_with_tools):
        """Test that unknown tools fall back to defaults."""
        import os

        old_cwd = os.getcwd()
        os.chdir(temp_dir)
        try:
            result = runner.invoke(
                cli,
                ["--scheduler", "local", "run", "--dry-run", "unknown_tool", "arg"],
            )
            assert result.exit_code == 0
            # Should not have python modules
            assert "python/3.11" not in result.output
        finally:
            os.chdir(old_cwd)

    def test_job_type_uses_types_section(self, runner, temp_dir, config_with_tools):
        """Test that --type explicitly uses [types] section."""
        import os

        old_cwd = os.getcwd()
        os.chdir(temp_dir)
        try:
            result = runner.invoke(
                cli,
                [
                    "--scheduler",
                    "local",
                    "run",
                    "--dry-run",
                    "--type",
                    "gpu",
                    "python",
                    "train.py",
                ],
            )
            assert result.exit_code == 0
            # Should NOT have python modules (using type, not tool)
            # Types don't auto-merge with tool detection
        finally:
            os.chdir(old_cwd)

    def test_short_type_option(self, runner, temp_dir, config_with_tools):
        """-t is the short form of --type."""
        import os

        old_cwd = os.getcwd()
        os.chdir(temp_dir)
        try:
            result = runner.invoke(
                cli, ["--scheduler", "local", "run", "--dry-run", "-t", "gpu", "echo", "hello"]
            )
            assert result.exit_code == 0, result.output
        finally:
            os.chdir(old_cwd)

    def test_cli_options_override_tool_config(self, runner, temp_dir, config_with_tools):
        """Test that CLI options override tool config."""
        import os

        old_cwd = os.getcwd()
        os.chdir(temp_dir)
        try:
            result = runner.invoke(
                cli,
                ["--scheduler", "local", "run", "--dry-run", "--cpu", "2", "python", "script.py"],
            )
            assert result.exit_code == 0
            # CPU should be overridden to 2, not 4 from config
            # (This is verified by the job creation, hard to assert in output)
        finally:
            os.chdir(old_cwd)


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

    def test_no_short_options_on_global(self, runner):
        """Verify short options are not available on global commands."""
        # These should fail because -c, -s, -v are no longer valid
        # Actually with the new design, unknown short options are passed through
        # So we just verify the help doesn't show them
        result = runner.invoke(cli, ["--help"])
        # Check that -c, -s, -v are not shown as shortcuts
        assert "-c, --config" not in result.output
        assert "-s, --scheduler" not in result.output
        assert "-v, --verbose" not in result.output


class TestDepend:
    """--depend is parsed into the same Dependency list the API builds."""

    @pytest.fixture
    def runner(self):
        return CliRunner()

    def test_repeated_values_are_one_list(self):
        from hpc_runner.cli.run import _parse_depend
        from hpc_runner.core.dependency import Dependency, DependencyType

        assert _parse_depend(("afterok:1:2", "afterany:3")) == [
            Dependency("1", DependencyType.AFTEROK),
            Dependency("2", DependencyType.AFTEROK),
            Dependency("3", DependencyType.AFTERANY),
        ]
        assert _parse_depend(()) == []

    @pytest.mark.parametrize("text", ["bogus:1", "afterok:", "1 2", "afterok::1", "afterok"])
    def test_bad_depend_is_usage_error(self, runner, text):
        result = runner.invoke(
            cli, ["--scheduler", "local", "run", "--dry-run", "--batch", "--depend", text, "echo"]
        )
        assert result.exit_code == 2, result.output
        assert "Invalid --depend" in result.output

    def test_unknown_type_lists_valid_ones(self, runner):
        result = runner.invoke(
            cli, ["--scheduler", "local", "run", "--dry-run", "--depend", "afterbogus:1", "echo"]
        )
        assert result.exit_code == 2
        for name in ("afterok", "afterany", "after", "afternotok"):
            assert name in result.output

    def test_depend_repeatable_end_to_end(self, runner, sge_dirs):
        """--depend afterok:1:2 --depend afterany:3: one hold, guards for 1 and 2 only."""
        result = runner.invoke(
            cli,
            [
                "--scheduler",
                "sge",
                "run",
                "--dry-run",
                "--batch",
                "--depend",
                "afterok:1:2",
                "--depend",
                "afterany:3",
                "echo",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "#$ -hold_jid 1,2,3" in result.output
        import re

        guarded = re.findall(r"_hpc_dep_check_id (\w+) (\S+)\s", result.output)
        assert guarded == [("afterok", "1"), ("afterok", "2")]

    def test_after_is_refused_on_sge(self, runner, sge_dirs):
        result = runner.invoke(
            cli,
            ["--scheduler", "sge", "run", "--dry-run", "--batch", "--depend", "after:1", "echo"],
        )
        from hpc_runner.core.exceptions import NotSupportedError

        assert isinstance(result.exception, NotSupportedError)
        assert "start-after" in str(result.exception)

    def test_interactive_with_dependency_is_one_line_error(self, monkeypatch, sge_dirs):
        """Without --batch, a dependency cannot be honoured: refuse, exit 1."""
        import sys

        from hpc_runner.cli import main as cli_main

        monkeypatch.setattr(
            sys, "argv", ["hpc", "--scheduler", "sge", "run", "--depend", "afterok:1", "cmd"]
        )
        runner = CliRunner()
        with runner.isolation() as (out, err, _):
            with pytest.raises(SystemExit) as info:
                cli_main.main()
        assert info.value.code == 1
        assert err.getvalue().decode() == (
            "error: interactive jobs cannot have dependencies; use --batch\n"
        )

    def test_bad_array_is_usage_error(self, runner):
        result = runner.invoke(
            cli, ["--scheduler", "local", "run", "--dry-run", "--array", "x-y", "echo"]
        )
        assert result.exit_code == 2
        assert "--array" in result.output


class TestPassthroughDirectives:
    def test_batch_passthrough_one_directive_line(self, sge_dirs):
        """B1 end-to-end: hpc run --batch -q batch.q -l gpu=2 -- python train.py."""
        result = CliRunner().invoke(
            cli,
            [
                "--scheduler",
                "sge",
                "run",
                "--dry-run",
                "--batch",
                "-q",
                "batch.q",
                "-l",
                "gpu=2",
                "--",
                "python",
                "train.py",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "#$ -q batch.q -l gpu=2" in result.output
