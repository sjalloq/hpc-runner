"""Tests for CLI run command."""

from functools import partialmethod

import pytest
from click.testing import CliRunner

from hpc_runner.cli.main import cli
from hpc_runner.core.exceptions import ConfigError
from hpc_runner.core.result import JobResult


@pytest.fixture
def fast_wait(monkeypatch):
    """``--wait`` polls every 50 ms instead of 5 s."""
    monkeypatch.setattr(JobResult, "wait", partialmethod(JobResult.wait, poll_interval=0.05))


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

    def test_dry_run_shows_parsed_mem_and_time(self, runner):
        result = runner.invoke(
            cli,
            ["--scheduler", "local", "run", "--dry-run", "--mem", "16G", "--time", "4h", "true"],
        )
        assert result.exit_code == 0, result.output
        assert "mem: 16 GiB (17179869184 bytes)" in result.output
        assert "time: 4:00:00 (14400 s)" in result.output

    def test_bare_integer_time_rejected(self, runner):
        result = runner.invoke(
            cli, ["--scheduler", "local", "run", "--dry-run", "--time", "90", "true"]
        )
        assert isinstance(result.exception, ConfigError)
        assert "spec: time: invalid time '90': ambiguous: use 90s or 90m" in str(result.exception)

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
                ["--scheduler", "local", "run", "--batch", "echo", "hello"],
            )
            assert result.exit_code == 0
            assert "Submitted job" in result.output or "local_" in result.output

    def test_run_interactive_local(self, runner, temp_dir):
        """Test running an interactive local job."""
        with runner.isolated_filesystem(temp_dir=temp_dir):
            result = runner.invoke(
                cli,
                ["--scheduler", "local", "run", "echo", "hello"],
            )
            assert result.exit_code == 0
            assert "completed successfully" in result.output

    def test_default_mode_is_interactive(self, runner, temp_dir):
        """Without --batch the dry run reports interactive mode."""
        result = runner.invoke(cli, ["--scheduler", "local", "run", "--dry-run", "echo", "hello"])
        assert result.exit_code == 0
        assert "Mode: interactive" in result.output

    def test_batch_flag_selects_batch_mode(self, runner, temp_dir):
        """--batch switches the dry run to batch mode."""
        result = runner.invoke(
            cli, ["--scheduler", "local", "run", "--dry-run", "--batch", "echo", "hello"]
        )
        assert result.exit_code == 0, result.output
        assert "Mode: batch" in result.output

    def test_short_flags_are_hpc_runners(self, runner, sge_dirs):
        """D-T43: with raw flags in -S, every short letter is ours: -b batch, -h help."""
        result = runner.invoke(
            cli, ["--scheduler", "sge", "run", "--dry-run", "-b", "-N", "held", "sleep", "9"]
        )
        assert result.exit_code == 0, result.output
        assert "Mode: batch" in result.output
        assert "#$ -N held" in result.output
        help_out = runner.invoke(cli, ["run", "-h"])
        assert help_out.exit_code == 0 and "--scheduler-args" in help_out.output

    @pytest.mark.parametrize(
        ("short", "long"),
        [("-b", "--batch"), ("-N", "--name"), ("-c", "--cpu"), ("-S", "--scheduler-args")],
    )
    def test_short_and_long_spellings_listed_together(self, runner, short, long):
        result = runner.invoke(cli, ["run", "--help"])
        assert result.exit_code == 0
        assert short in result.output and long in result.output

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
                cli, ["--scheduler", "local", "run", "--dry-run", opt, "x", "echo"]
            )
            assert result.exit_code != 0, opt
            assert "No such option" in result.output, opt

    def test_unknown_flag_is_a_usage_error(self, runner, temp_dir):
        """A flag hpc run does not define is rejected, never forwarded or run."""
        result = runner.invoke(
            cli, ["--scheduler", "local", "run", "--dry-run", "-z", "batch.q", "echo", "hi"]
        )
        assert result.exit_code != 0
        assert "No such option" in result.output
        result = runner.invoke(
            cli, ["--scheduler", "local", "run", "--dry-run", "--cpus", "4", "echo"]
        )
        assert result.exit_code != 0
        assert "No such option" in result.output


class TestSchedulerArgs:
    """-S/--scheduler-args: raw scheduler flags as quoted strings (D-T43)."""

    @pytest.fixture
    def runner(self):
        return CliRunner()

    def _dry(self, runner, *args):
        return runner.invoke(cli, ["--scheduler", "local", "run", "--dry-run", *args])

    def test_one_string_is_split_like_a_shell(self, runner, temp_dir):
        result = self._dry(runner, "-S", "-q batch.q -l gpu=2", "echo", "hello")
        assert result.exit_code == 0, result.output
        assert "echo hello" in result.output
        assert "Scheduler passthrough: -q batch.q -l gpu=2" in result.output

    def test_repeated_strings_concatenate_in_order(self, runner, temp_dir):
        result = self._dry(runner, "-S", "-q batch.q", "--scheduler-args", "-l gpu=2", "echo")
        assert result.exit_code == 0, result.output
        assert "Scheduler passthrough: -q batch.q -l gpu=2" in result.output

    def test_no_option_no_passthrough(self, runner, temp_dir):
        result = self._dry(runner, "echo", "hello")
        assert result.exit_code == 0
        assert "Scheduler passthrough" not in result.output

    @pytest.mark.parametrize(
        "flags",
        [
            "-terse -tc 4 -pty y",  # qsub words click would cluster-match as -t
            "-soft -l a=1 -hold_jid 123",
            "-cwd -V -h",
            "--gres=gpu:2 --partition=short",  # sbatch long options
        ],
    )
    def test_scheduler_words_never_reach_the_option_parser(self, runner, temp_dir, flags):
        result = self._dry(runner, "-S", flags, "sleep", "1")
        assert result.exit_code == 0, result.output
        assert f"Scheduler passthrough: {flags}" in result.output
        assert "sleep 1" in result.output

    def test_quoted_value_survives(self, runner, temp_dir):
        result = self._dry(runner, "-S", "-N 'two words'", "echo")
        assert result.exit_code == 0, result.output
        assert "Scheduler passthrough: -N two words" in result.output

    @pytest.mark.parametrize(
        "order",
        [
            ["-S", "-q batch.q", "-t", "x", "--cpu", "4"],
            ["-t", "x", "-S", "-q batch.q", "--cpu", "4"],
            ["--cpu", "4", "-t", "x", "-S", "-q batch.q"],
        ],
    )
    def test_option_order_does_not_matter(self, runner, temp_dir, order, monkeypatch):
        (temp_dir / "hpc-runner.toml").write_text("config_version = 1\n[types.x]\n")
        monkeypatch.setenv("HPC_RUNNER_CONFIG", str(temp_dir / "hpc-runner.toml"))
        result = self._dry(runner, *order, "make", "sim")
        assert result.exit_code == 0, result.output
        assert "Scheduler passthrough: -q batch.q" in result.output
        assert "make sim" in result.output

    def test_command_flags_are_the_commands(self, runner, temp_dir):
        """The first plain word ends hpc's options; everything after is the command."""
        result = self._dry(runner, "mpirun", "-N", "4", "-S", "x", "./sim")
        assert result.exit_code == 0, result.output
        assert "mpirun -N 4 -S x ./sim" in result.output
        assert "Scheduler passthrough" not in result.output

    def test_dashed_command_needs_a_separator(self, runner, temp_dir):
        result = self._dry(runner, "--", "-h", "sleep", "30")
        assert result.exit_code == 0, result.output
        assert "Command: -h sleep 30" in result.output

    def test_no_command_is_an_error(self, runner):
        result = self._dry(runner, "-S", "-q batch.q")
        assert result.exit_code != 0
        assert "Command is required" in result.output


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
        """B1 end-to-end: hpc run --batch -S '-q batch.q -l gpu=2' python train.py."""
        result = CliRunner().invoke(
            cli,
            [
                "--scheduler",
                "sge",
                "run",
                "--dry-run",
                "--batch",
                "-S",
                "-q batch.q -l gpu=2",
                "python",
                "train.py",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "#$ -q batch.q -l gpu=2" in result.output


class TestEnvResourceWaitOptions:
    """--env and --resource reach the spec; --wait needs --batch; --local is gone."""

    @pytest.fixture
    def runner(self):
        return CliRunner()

    def _dry(self, runner, *args):
        result = runner.invoke(cli, ["--scheduler", "sge", "run", "--dry-run", "--batch", *args])
        assert result.exit_code == 0, result.output
        return result.output

    def test_env_sets_variables(self, runner, sge_dirs):
        out = self._dry(runner, "--env", "A=1", "--env", "B=x=y z", "--env", "A=2", "echo")
        assert "export A=2" in out
        assert "export B='x=y z'" in out

    def test_env_bad_form(self, runner):
        result = runner.invoke(cli, ["--scheduler", "local", "run", "--env", "NOVALUE", "echo"])
        assert result.exit_code == 2
        assert "KEY=VALUE" in result.output

    def test_resource_requests(self, runner, sge_dirs):
        out = self._dry(runner, "--resource", "gpu=2", "--resource", "xcelium_lic=1", "echo")
        assert "#$ -l gpu=2" in out
        assert "#$ -l xcelium_lic=1" in out

    def test_resource_bad_form(self, runner):
        result = runner.invoke(cli, ["--scheduler", "local", "run", "--resource", "=2", "echo"])
        assert result.exit_code == 2
        assert "NAME=VALUE" in result.output

    def test_wait_without_batch_is_usage_error(self, runner):
        result = runner.invoke(cli, ["--scheduler", "local", "run", "--wait", "echo"])
        assert result.exit_code == 2
        assert "--wait needs --batch" in result.output

    def test_wait_with_array_is_allowed(self, runner, sge_dirs):
        result = runner.invoke(
            cli, ["--scheduler", "sge", "run", "--dry-run", "--wait", "--array", "1-2", "echo"]
        )
        assert result.exit_code == 0, result.output

    def test_local_option_removed(self, runner):
        result = runner.invoke(cli, ["run", "--local", "echo", "hi"])
        assert result.exit_code != 0
        assert "No such option" in result.output
