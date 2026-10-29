"""Tests for `hpc config check` and `hpc config resolve`."""

import pytest
from click.testing import CliRunner

from hpc_runner.cli.main import cli

GOOD = """config_version = 1
[defaults]
cpu = 1
modules = ["site/1"]
scheduler_args = { sge = ["-binding", "linear:1"] }

[tools.xrun]
modules = ["xcelium/23"]

[tools.slang]
modules = ["slang/0.9"]

[types.lint]
tools = ["xrun", "slang"]
cpu = 2
"""


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture(autouse=True)
def _cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("HPC_RUNNER_CONFIG", raising=False)


class TestCheck:
    def test_ok(self, runner, tmp_path):
        (tmp_path / "hpc-runner.toml").write_text(GOOD)
        result = runner.invoke(cli, ["config", "check"])
        assert result.exit_code == 0
        assert result.output.strip() == "ok: 1 file"

    def test_explicit_path_with_extends(self, runner, tmp_path):
        (tmp_path / "base.toml").write_text(GOOD)
        child = tmp_path / "child.toml"
        child.write_text('config_version = 1\nextends = "base.toml"\n')
        result = runner.invoke(cli, ["config", "check", str(child)])
        assert result.exit_code == 0
        assert "ok: 2 files" in result.output

    def test_every_error_reported(self, runner, tmp_path):
        bad = tmp_path / "bad.toml"
        bad.write_text('config_version = 1\n[defaults]\ncpu = "4"\nmodles = []\n')
        result = runner.invoke(cli, ["config", "check", str(bad)])
        assert result.exit_code == 1
        lines = result.output.splitlines()
        assert lines == [
            f'error: {bad}:3: [defaults] cpu: expected an integer, got string "4"',
            f"error: {bad}:4: [defaults] modles: unknown key; did you mean 'modules'?",
        ]

    def test_missing_extends_reported(self, runner, tmp_path):
        bad = tmp_path / "bad.toml"
        bad.write_text('config_version = 1\nextends = "gone.toml"\n')
        result = runner.invoke(cli, ["config", "check", str(bad)])
        assert result.exit_code == 1
        assert "extends: file not found" in result.output

    def test_warning_printed_but_ok(self, runner, tmp_path):
        (tmp_path / "hpc-runner.toml").write_text("config_version = 1\n[schedulers.lsf]\n")
        result = runner.invoke(cli, ["config", "check"])
        assert result.exit_code == 0
        assert "warning: " in result.output
        assert "[schedulers.lsf]" in result.output
        assert "ok: 1 file" in result.output

    def test_run_refuses_broken_config(self, runner, tmp_path):
        (tmp_path / "hpc-runner.toml").write_text("[defaults]\ncpu = 1\n")
        result = runner.invoke(cli, ["--scheduler", "local", "run", "--dry-run", "echo", "hi"])
        assert result.exit_code == 1
        assert "missing config_version" in str(result.exception)


class TestResolve:
    def test_type_sources(self, runner, tmp_path):
        (tmp_path / "hpc-runner.toml").write_text(GOOD)
        result = runner.invoke(cli, ["config", "resolve", "-t", "lint", "make", "lint", "-j4"])
        assert result.exit_code == 0, result.output
        rows = [[c.strip() for c in line.split("│")[1:-1]] for line in result.output.splitlines()]
        rows = [r for r in rows if len(r) == 3]
        assert ["command", "make lint -j4", "spec"] in rows
        assert ["cpu", "2", "types.lint"] in rows
        assert ["modules", "site/1", "defaults"] in rows
        assert ["modules", "xcelium/23", "tools.xrun"] in rows
        assert ["modules", "slang/0.9", "tools.slang"] in rows
        assert ["scheduler_args.sge", "-binding", "defaults"] in rows
        assert ["shell", "/bin/bash", "package"] in rows

    def test_tool_from_command(self, runner, tmp_path):
        (tmp_path / "hpc-runner.toml").write_text(GOOD)
        result = runner.invoke(cli, ["config", "resolve", "xrun", "-f", "top.f"])
        assert result.exit_code == 0, result.output
        assert "tools.xrun" in result.output
        assert "slang" not in result.output

    def test_unknown_type(self, runner, tmp_path):
        (tmp_path / "hpc-runner.toml").write_text(GOOD)
        result = runner.invoke(cli, ["config", "resolve", "-t", "nope", "make"])
        assert result.exit_code == 1
        assert "unknown job type 'nope'" in str(result.exception)


def test_init_writes_a_valid_config(runner, tmp_path):
    result = runner.invoke(cli, ["config", "init"])
    assert result.exit_code == 0
    text = (tmp_path / "hpc-runner.toml").read_text()
    assert text.startswith("config_version = 1\n")
    assert runner.invoke(cli, ["config", "check"]).exit_code == 0
