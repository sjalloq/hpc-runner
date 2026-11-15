"""The trace recorder at the real subprocess seam (docs/dev/trace/DESIGN.md D-TR1..D-TR7)."""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

import pytest

from hpc_runner.core.exceptions import (
    SchedulerCommandError,
    SchedulerTimeoutError,
    SchedulerUnavailableError,
)
from hpc_runner.schedulers._subprocess import run_command
from hpc_runner.trace import (
    DEFAULT_TRACE_DIR,
    Tracer,
    activate,
    activate_from_config,
    current,
    deactivate,
    disable,
)

PY = sys.executable


def _records(root: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(root.rglob("*.json"))]


class TestRecordAtTheSeam:
    def test_nothing_written_without_a_tracer(self, tmp_path):
        assert current() is None
        run_command([PY, "-c", "print('hi')"], timeout=10)
        assert list(tmp_path.rglob("*.json")) == []

    def test_success_record(self, tmp_path):
        activate(tmp_path)
        run_command(
            [PY, "-c", "import sys; print('out'); print('err', file=sys.stderr)"], timeout=10
        )
        (rec,) = _records(tmp_path)
        assert rec["argv"][0] == PY
        assert rec["returncode"] == 0
        assert rec["stdout"] == "out\n"
        assert rec["stderr"] == "err\n"
        assert rec["error"] is None
        assert rec["pid"] == os.getpid()
        assert rec["cwd"] == os.getcwd()
        assert rec["elapsed"] >= 0
        assert isinstance(rec["version"], str)
        # Layout: <dir>/<YYYY-MM-DD>/<HHMMSS.mmm>-<pid>-<cmd>.json (D-TR4)
        (path,) = tmp_path.rglob("*.json")
        assert path.parent.name == date.today().isoformat()
        assert path.name.split("-")[1] == str(os.getpid())
        assert path.name.endswith(f"-{Path(PY).name}.json")

    def test_non_zero_exit_is_recorded_before_the_error_is_raised(self, tmp_path):
        activate(tmp_path)
        with pytest.raises(SchedulerCommandError):
            run_command([PY, "-c", "raise SystemExit(3)"], timeout=10)
        (rec,) = _records(tmp_path)
        assert rec["returncode"] == 3

    def test_check_false_records_too(self, tmp_path):
        activate(tmp_path)
        run_command([PY, "-c", "raise SystemExit(2)"], timeout=10, check=False)
        assert _records(tmp_path)[0]["returncode"] == 2

    def test_missing_binary(self, tmp_path):
        activate(tmp_path)
        with pytest.raises(SchedulerUnavailableError):
            run_command(["no-such-scheduler-binary", "-help"], timeout=10)
        (rec,) = _records(tmp_path)
        assert rec["returncode"] is None
        assert rec["error"] == "no-such-scheduler-binary not found on PATH"

    def test_timeout(self, tmp_path):
        activate(tmp_path)
        with pytest.raises(SchedulerTimeoutError):
            run_command([PY, "-c", "import time; time.sleep(5)"], timeout=0.2)
        (rec,) = _records(tmp_path)
        assert rec["returncode"] is None
        assert rec["error"] == "timed out after 0.2s"

    def test_interactive_call_records_null_output(self, tmp_path):
        activate(tmp_path)
        run_command([PY, "-c", "pass"], timeout=10, capture=False)
        (rec,) = _records(tmp_path)
        assert rec["returncode"] == 0
        assert rec["stdout"] is None and rec["stderr"] is None


class TestEveryCallIsWritten:
    def test_identical_repeat_is_kept(self, tmp_path):
        activate(tmp_path)
        for _ in range(3):
            run_command([PY, "-c", "print('same')"], timeout=10)
        assert len(_records(tmp_path)) == 3

    def test_repeat_in_a_new_process_is_kept(self, tmp_path):
        activate(tmp_path)
        run_command([PY, "-c", "print('same')"], timeout=10)
        deactivate()
        # A new process: a fresh tracer over the same directory.
        activate(tmp_path)
        run_command([PY, "-c", "print('same')"], timeout=10)
        assert len(_records(tmp_path)) == 2

    def test_same_millisecond_never_overwrites(self, tmp_path, monkeypatch):
        import hpc_runner.trace.recorder as mod

        class Frozen:
            @staticmethod
            def now():
                from datetime import datetime

                return datetime(2026, 10, 31, 12, 0, 0, 123000)

        monkeypatch.setattr(mod, "datetime", Frozen)
        tracer = Tracer(tmp_path)
        tracer.record(["qstat"], returncode=0, stdout="a", stderr="", elapsed=0)
        tracer.record(["qstat"], returncode=0, stdout="b", stderr="", elapsed=0)
        names = sorted(p.name for p in tmp_path.rglob("*.json"))
        assert names == [
            f"120000.123-{os.getpid()}-qstat-1.json",
            f"120000.123-{os.getpid()}-qstat.json",
        ]


class TestActivation:
    def test_activate_expands_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        tracer = activate("~/trace")
        assert tracer.root == tmp_path / "trace"

    def test_default_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        tracer = activate_from_config({"enabled": True})
        assert tracer is not None
        assert (
            tracer.root
            == Path(DEFAULT_TRACE_DIR).expanduser()
            == tmp_path / ".cache/hpc-runner/trace"
        )

    def test_disabled_or_empty_table_does_nothing(self):
        assert activate_from_config({}) is None
        assert activate_from_config({"enabled": False, "dir": "/x"}) is None
        assert current() is None

    def test_config_values_used(self, tmp_path):
        tracer = activate_from_config({"enabled": True, "dir": str(tmp_path)})
        assert tracer is not None and tracer.root == tmp_path

    def test_flag_beats_config(self, tmp_path):
        flag = activate(tmp_path / "flag")
        assert activate_from_config({"enabled": True, "dir": str(tmp_path / "cfg")}) is flag
        assert current() is flag

    def test_deactivate(self, tmp_path):
        activate(tmp_path)
        deactivate()
        assert current() is None

    def test_unwritable_dir_warns_and_continues(self, tmp_path, caplog):
        blocker = tmp_path / "file"
        blocker.write_text("not a directory")
        tracer = Tracer(blocker)
        with caplog.at_level("WARNING", logger="hpc_runner.trace"):
            assert tracer.record(["qstat"], returncode=0, stdout="", stderr="", elapsed=0) is None
        assert "trace: cannot write" in caplog.text


class TestDisable:
    def test_no_trace_starts_once_disabled(self, tmp_path):
        activate(tmp_path)
        disable()
        assert current() is None
        assert activate_from_config({"enabled": True, "dir": str(tmp_path)}) is None
        run_command([PY, "-c", "print('x')"], timeout=10)
        assert _records(tmp_path) == []

    def test_deactivate_clears_it(self, tmp_path):
        disable()
        deactivate()
        assert activate_from_config({"enabled": True, "dir": str(tmp_path)}) is not None
