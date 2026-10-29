"""Tests for the CLI's top-level HPCToolsError handling."""

import logging
import sys

import pytest
from click.testing import CliRunner

from hpc_runner.cli import main as cli_main
from tests.fakes import patch_seam

QSUB_REJECT = 'Unable to run job: unknown queue "nope.q".\nExiting.\n'


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("HPC_SCRIPT_DIR", str(tmp_path / "scripts"))
    monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path / "exit"))
    monkeypatch.chdir(tmp_path)
    yield
    # Undo any --verbose logging so later tests see a quiet package logger.
    cli_main._configure_logging(False)
    logging.getLogger("hpc_runner").setLevel(logging.NOTSET)


def _run_main(monkeypatch, args):
    """Run main() as the console script would; return (exit code, stdout, stderr)."""
    monkeypatch.setattr(sys, "argv", ["hpc", *args])
    runner = CliRunner()
    with runner.isolation() as (out, err, _):
        with pytest.raises(SystemExit) as info:
            cli_main.main()
        return info.value.code, out.getvalue().decode(), err.getvalue().decode()


def test_submission_error_prints_one_line(monkeypatch):
    with patch_seam(lambda argv: (1, "", QSUB_REJECT)):
        code, out, err = _run_main(
            monkeypatch, ["--scheduler", "sge", "run", "--batch", "echo", "hi"]
        )
    assert code == 1
    assert err == 'error: Unable to run job: unknown queue "nope.q".\n'
    assert "Traceback" not in out + err


def test_verbose_adds_argv_and_full_stderr(monkeypatch):
    with patch_seam(lambda argv: (1, "", QSUB_REJECT)):
        code, _, err = _run_main(
            monkeypatch, ["--verbose", "--scheduler", "sge", "run", "--batch", "echo", "hi"]
        )
    assert code == 1
    lines = err.splitlines()
    assert lines[0] == 'error: Unable to run job: unknown queue "nope.q".'
    assert any(line.startswith("command: qsub ") for line in lines)
    assert "Exiting." in lines


def test_unknown_scheduler_is_one_line(monkeypatch):
    code, _, err = _run_main(monkeypatch, ["--scheduler", "nope", "run", "--batch", "echo", "hi"])
    assert code == 1
    assert err.startswith("error: unknown scheduler 'nope'")
    assert err.count("\n") == 1


def test_verbose_logs_scheduler_argv(monkeypatch, tmp_path):
    """--verbose shows every scheduler argv; a missing binary is one error line."""
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    code, _, err = _run_main(monkeypatch, ["--verbose", "--scheduler", "sge", "status", "123"])
    assert code == 1
    assert "hpc_runner.schedulers: run: qstat -j 123 -xml" in err
    assert err.splitlines()[-1] == "error: qstat not found on PATH"


def test_quiet_by_default(monkeypatch, tmp_path):
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    code, _, err = _run_main(monkeypatch, ["--scheduler", "sge", "status", "123"])
    assert code == 1
    assert err == "error: qstat not found on PATH\n"
