"""The record of one ``hpc`` command line (``hpc_runner.trace.Invocation``).

``main()`` is the entry point that writes it, so these tests call ``main()``
with ``sys.argv`` set, over each scheduler's captured fixtures (``fixtures``).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from hpc_runner.cli import main as cli_main
from hpc_runner.trace import Invocation, activate, deactivate
from hpc_runner.trace.recorder import OUTPUT_LIMIT


def _run(monkeypatch, *args: str) -> int:
    monkeypatch.setattr(sys, "argv", ["hpc", *args])
    with pytest.raises(SystemExit) as exit_info:
        cli_main.main()
    return int(exit_info.value.code or 0)


def _split(trace: Path) -> tuple[list[dict], list[dict]]:
    """``(invocation records, scheduler-call records)`` under *trace*."""
    records = [json.loads(p.read_text()) for p in sorted(trace.rglob("*.json"))]
    return (
        [r for r in records if r.get("kind") == "invocation"],
        [r for r in records if r.get("kind") != "invocation"],
    )


@pytest.fixture
def env(tmp_path, monkeypatch, mock_commands):
    """A config file, with the scheduler of ``fixtures`` answering from its captures."""
    monkeypatch.setenv("USER", "tester")
    config = tmp_path / "hpc-runner.toml"
    config.write_text('config_version = 1\n[defaults]\nqueue = "all.q"\n')
    return config


class TestInvocationRecord:
    def test_status(self, tmp_path, env, fixtures, monkeypatch, capsys):
        trace = tmp_path / "trace"
        monkeypatch.setenv("SBATCH_PARTITION", "debug")
        code = _run(monkeypatch, "--config", str(env), "--trace", str(trace), "status")
        printed, err = capsys.readouterr()
        assert code == 0
        # stdout stays clean (--json); the trace says so once, on stderr.
        assert err == f"trace: 2 records written to {next(trace.iterdir())}\n"
        invocations, calls = _split(trace)
        assert len(invocations) == 1 and len(calls) == 1
        record = invocations[0]
        assert record["argv"] == ["hpc", "--config", str(env), "--trace", str(trace), "status"]
        assert record["exit_code"] == 0
        assert record["error"] is None
        # What hpc printed, as printed: the table is in the record.
        assert record["stdout"] == printed and printed.strip()
        assert record["config"] == [{"path": str(env), "text": env.read_text()}]
        assert record["env"]["HPC_SCHEDULER"] == fixtures.scheduler
        # The whole environment, not only HPC_*: a scheduler's own variables too.
        assert record["env"]["SBATCH_PARTITION"] == "debug"
        assert list(record["env"]) == sorted(record["env"])
        assert record["pid"] == calls[0]["pid"]
        assert record["output_truncated"] is False

    def test_submit_records_the_script(self, tmp_path, env, fixtures, monkeypatch, capsys):
        trace = tmp_path / "trace"
        code = _run(
            monkeypatch,
            "--config", str(env), "--trace", str(trace),
            "run", "--batch", "--directory", str(tmp_path), "echo", "hello",
        )  # fmt: skip
        assert code == 0, capsys.readouterr()
        invocations, calls = _split(trace)
        assert invocations[0]["argv"][-2:] == ["echo", "hello"]
        (submit,) = [c for c in calls if c["argv"][0] == fixtures.submit_command]
        script = submit["script"]
        assert script["path"] == submit["argv"][1]
        assert script["text"].startswith("#!/bin/bash")
        assert fixtures.queue_directive in script["text"]
        assert "echo hello" in script["text"]
        # The file itself is gone; the trace is the only copy.
        assert not Path(script["path"]).exists()
        assert all("script" not in c for c in calls if c is not submit)

    def test_error_is_recorded(self, tmp_path, monkeypatch, capsys):
        monkeypatch.delenv("HPC_SCHEDULER", raising=False)
        config = tmp_path / "hpc-runner.toml"
        config.write_text("config_version = 1\n")
        trace = tmp_path / "trace"
        code = _run(monkeypatch, "--config", str(config), "--trace", str(trace), "status")
        assert code == 1
        (record,), calls = _split(trace)
        assert calls == []
        assert record["exit_code"] == 1
        assert record["error"]["type"] == "ConfigError"
        assert "no scheduler configured" in record["error"]["message"]
        assert "traceback" not in record["error"]
        assert "no scheduler configured" in record["stderr"]
        # The terminal got the same, then the closing trace line (not in the record).
        day = next(trace.iterdir())
        assert capsys.readouterr().err == (record["stderr"] + f"trace: 1 record written to {day}\n")

    def test_usage_error(self, tmp_path, monkeypatch, capsys):
        trace = tmp_path / "trace"
        code = _run(monkeypatch, "--trace", str(trace), "status", "--no-such-option")
        assert code == 2
        (record,), _ = _split(trace)
        assert record["exit_code"] == 2
        assert "no-such-option" in record["stderr"]

    def test_config_enabled(self, tmp_path, env, monkeypatch, capsys):
        trace = tmp_path / "cfgtrace"
        env.write_text(env.read_text() + f'[trace]\nenabled = true\ndir = "{trace}"\n')
        assert _run(monkeypatch, "--config", str(env), "status") == 0
        assert _run(monkeypatch, "--config", str(env), "status") == 0
        invocations, calls = _split(trace)
        # Every command line has its own scheduler call beside it, identical or not.
        assert len(invocations) == 2 and len(calls) == 2

    def test_nothing_without_a_trace(self, tmp_path, env, monkeypatch, capsys):
        out, err = sys.stdout, sys.stderr
        assert _run(monkeypatch, "--config", str(env), "status") == 0
        assert "trace:" not in capsys.readouterr().err
        assert (sys.stdout, sys.stderr) == (out, err)
        assert list(tmp_path.rglob("*-hpc.json")) == []

    def test_a_bug_keeps_its_traceback(self, tmp_path, env, monkeypatch, capsys):
        def boom(*args, **kwargs):
            raise RuntimeError("parser fell over")

        monkeypatch.setattr("hpc_runner.schedulers.get_scheduler", boom)
        trace = tmp_path / "trace"
        monkeypatch.setattr(
            sys, "argv", ["hpc", "--config", str(env), "--trace", str(trace), "status"]
        )
        with pytest.raises(RuntimeError):
            cli_main.main()
        (record,), _ = _split(trace)
        assert record["error"]["type"] == "RuntimeError"
        assert "parser fell over" in record["error"]["traceback"]
        assert record["exit_code"] == 1


class TestMonitorIsNotTraced:
    @pytest.fixture
    def fake_monitor(self, monkeypatch):
        """A monitor that asks the scheduler for jobs once and returns."""
        ran = []

        class FakeApp:
            def __init__(self, config, refresh_interval=10):
                self.config = config

            def run(self):
                from hpc_runner.schedulers import get_scheduler

                get_scheduler(config=self.config).list_active_jobs()
                ran.append(True)

        monkeypatch.setattr("hpc_runner.tui.HpcMonitorApp", FakeApp)
        return ran

    def test_config_enabled_records_nothing(self, tmp_path, env, monkeypatch, capsys, fake_monitor):
        trace = tmp_path / "cfgtrace"
        env.write_text(env.read_text() + f'[trace]\nenabled = true\ndir = "{trace}"\n')
        assert _run(monkeypatch, "--config", str(env), "monitor") == 0
        assert fake_monitor == [True]
        assert list(trace.rglob("*.json")) == []
        # The same config still traces any other command (a new process).
        deactivate()
        assert _run(monkeypatch, "--config", str(env), "status") == 0
        invocations, calls = _split(trace)
        assert len(invocations) == 1 and len(calls) == 1

    def test_flag_is_a_usage_error(self, tmp_path, env, monkeypatch, capsys, fake_monitor):
        trace = tmp_path / "trace"
        code = _run(monkeypatch, "--config", str(env), "--trace", str(trace), "monitor")
        assert code == 2
        assert "--trace does not apply to 'hpc monitor'" in capsys.readouterr().err
        assert fake_monitor == []
        assert not trace.exists()


class TestCapture:
    def test_escape_codes_are_removed(self, tmp_path, capsys):
        activate(tmp_path)
        invocation = Invocation(["hpc", "status"])
        invocation.capture()
        sys.stdout.write("\x1b[1;32mok\x1b[0m \x1b]8;;http://x\x1b\\link\x1b]8;;\x1b\\\n")
        path = invocation.finish(exit_code=0)
        assert path is not None
        assert json.loads(path.read_text())["stdout"] == "ok link\n"
        # The terminal still gets them.
        assert "\x1b[1;32m" in capsys.readouterr().out

    def test_long_output_is_cut(self, tmp_path, capsys):
        activate(tmp_path)
        invocation = Invocation(["hpc"])
        invocation.capture()
        sys.stdout.write("x" * (OUTPUT_LIMIT + 10))
        data = json.loads(invocation.finish(exit_code=0).read_text())
        assert len(data["stdout"]) == OUTPUT_LIMIT
        assert data["output_truncated"] is True
