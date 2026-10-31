"""The trace recorder at the real subprocess seam (docs/dev/trace/DESIGN.md D-TR1..D-TR7)."""

from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from hpc_runner.core.exceptions import (
    ConfigError,
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


class TestDedupe:
    def test_identical_repeat_is_dropped(self, tmp_path):
        activate(tmp_path)
        for _ in range(3):
            run_command([PY, "-c", "print('same')"], timeout=10)
        assert len(_records(tmp_path)) == 1

    def test_change_is_kept(self, tmp_path):
        activate(tmp_path)
        run_command([PY, "-c", "print('a')"], timeout=10)
        run_command([PY, "-c", "print('a')"], timeout=10)
        run_command([PY, "-c", "print('b')"], timeout=10)
        assert [r["argv"][-1] for r in _records(tmp_path)] == ["print('a')", "print('b')"]

    def test_different_argv_are_independent(self, tmp_path):
        tracer = activate(tmp_path)
        tracer.record(["qstat", "-xml"], returncode=0, stdout="x", stderr="", elapsed=0)
        tracer.record(["qacct", "-j", "1"], returncode=0, stdout="x", stderr="", elapsed=0)
        assert len(_records(tmp_path)) == 2

    def test_dedupe_off(self, tmp_path):
        activate(tmp_path, dedupe=False)
        run_command([PY, "-c", "print('same')"], timeout=10)
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
        tracer = Tracer(tmp_path, dedupe=False)
        tracer.record(["qstat"], returncode=0, stdout="a", stderr="", elapsed=0)
        tracer.record(["qstat"], returncode=0, stdout="b", stderr="", elapsed=0)
        names = sorted(p.name for p in tmp_path.rglob("*.json"))
        assert names == [
            f"120000.123-{os.getpid()}-qstat-1.json",
            f"120000.123-{os.getpid()}-qstat.json",
        ]


class TestSweep:
    def test_old_day_directories_removed(self, tmp_path):
        today = date.today()
        old = tmp_path / (today - timedelta(days=20)).isoformat()
        recent = tmp_path / (today - timedelta(days=3)).isoformat()
        other = tmp_path / "notes"
        for d in (old, recent, other):
            d.mkdir()
            (d / "x.json").write_text("{}")
        removed = Tracer(tmp_path).sweep(max_days=14)
        assert removed == [old]
        assert not old.exists() and recent.exists() and other.exists()

    def test_boundary_day_is_kept(self, tmp_path):
        edge = tmp_path / (date.today() - timedelta(days=14)).isoformat()
        edge.mkdir()
        assert Tracer(tmp_path).sweep(max_days=14) == []

    def test_missing_root_is_fine(self, tmp_path):
        assert Tracer(tmp_path / "nope").sweep(14) == []

    def test_activate_sweeps(self, tmp_path):
        old = tmp_path / (date.today() - timedelta(days=40)).isoformat()
        old.mkdir()
        activate(tmp_path, max_days=14)
        assert not old.exists()


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
        assert tracer.dedupe is True

    def test_disabled_or_empty_table_does_nothing(self):
        assert activate_from_config({}) is None
        assert activate_from_config({"enabled": False, "dir": "/x"}) is None
        assert current() is None

    def test_config_values_used(self, tmp_path):
        tracer = activate_from_config({"enabled": True, "dir": str(tmp_path), "dedupe": False})
        assert tracer is not None and tracer.root == tmp_path and tracer.dedupe is False

    def test_flag_beats_config(self, tmp_path):
        flag = activate(tmp_path / "flag")
        assert activate_from_config({"enabled": True, "dir": str(tmp_path / "cfg")}) is flag
        assert current() is flag

    def test_max_days_below_one(self, tmp_path):
        with pytest.raises(ConfigError, match=r"\[trace\] max_days"):
            activate_from_config({"enabled": True, "dir": str(tmp_path), "max_days": 0})

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
