"""``[trace]`` in the config, ``hpc --trace`` and ``get_scheduler`` activation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from hpc_runner.cli.main import cli
from hpc_runner.core.config import HPCConfig, load_config
from hpc_runner.core.exceptions import ConfigError
from hpc_runner.schedulers import get_scheduler
from hpc_runner.trace import current


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "hpc-runner.toml"
    path.write_text("config_version = 1\n" + body)
    return path


class TestTraceSection:
    def test_loaded(self, tmp_path):
        cfg = load_config(
            _write(tmp_path, '[trace]\nenabled = true\ndir = "~/t"\nmax_days = 3\ndedupe = false\n')
        )
        assert cfg.trace == {"enabled": True, "dir": "~/t", "max_days": 3, "dedupe": False}

    def test_absent_is_empty(self, tmp_path):
        assert load_config(_write(tmp_path, "")).trace == {}
        assert HPCConfig().trace == {}

    def test_unknown_key(self, tmp_path):
        with pytest.raises(ConfigError) as err:
            load_config(_write(tmp_path, "[trace]\nenable = true\n"))
        assert "[trace] enable: unknown key; did you mean 'enabled'?" in str(err.value)

    def test_wrong_type(self, tmp_path):
        with pytest.raises(ConfigError) as err:
            load_config(_write(tmp_path, '[trace]\nenabled = "yes"\n'))
        assert "[trace] enabled: expected bool, got string" in str(err.value)

    def test_not_a_table(self, tmp_path):
        with pytest.raises(ConfigError) as err:
            load_config(_write(tmp_path, "trace = true\n"))
        assert "trace: expected a table" in str(err.value)

    def test_later_file_wins(self, tmp_path):
        (tmp_path / "base").mkdir()
        base = _write(tmp_path / "base", '[trace]\nenabled = true\ndir = "/a"\n')
        child = tmp_path / "hpc-runner.toml"
        child.write_text(f'config_version = 1\nextends = "{base}"\n[trace]\ndir = "/b"\n')
        assert load_config(child).trace == {"enabled": True, "dir": "/b"}

    def test_config_show_prints_it(self, tmp_path):
        path = _write(tmp_path, '[trace]\nenabled = true\ndir = "/t"\n')
        result = CliRunner().invoke(cli, ["--config", str(path), "config", "show"])
        assert result.exit_code == 0, result.output
        assert "[trace]" in result.output
        assert 'dir = "/t"' in result.output


class TestGetSchedulerActivates:
    def test_enabled_in_config(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path / "exit"))
        cfg = HPCConfig(trace={"enabled": True, "dir": str(tmp_path / "t")})
        get_scheduler("sge", config=cfg)
        tracer = current()
        assert tracer is not None and tracer.root == tmp_path / "t"

    def test_disabled_in_config(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path / "exit"))
        get_scheduler("sge", config=HPCConfig())
        assert current() is None

    def test_bad_max_days_is_a_config_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path / "exit"))
        cfg = HPCConfig(trace={"enabled": True, "dir": str(tmp_path), "max_days": 0})
        with pytest.raises(ConfigError, match="max_days"):
            get_scheduler("sge", config=cfg)


class TestTraceFlag:
    """``hpc --trace DIR`` over the captured SGE fixtures (the fake seam records too)."""

    def test_status_is_recorded(self, tmp_path, mock_sge_commands, sge_fixtures, monkeypatch):
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path / "exit"))
        monkeypatch.setenv("USER", "tester")
        trace = tmp_path / "trace"
        result = CliRunner().invoke(cli, ["--trace", str(trace), "--scheduler", "sge", "status"])
        assert result.exit_code == 0, result.output
        records = [json.loads(p.read_text()) for p in trace.rglob("*.json")]
        assert len(records) == 1
        assert records[0]["argv"][:2] == ["qstat", "-xml"]
        assert records[0]["stdout"] == sge_fixtures.text("qstat_mixed.xml")

    def test_flag_beats_config(self, tmp_path, mock_sge_commands, monkeypatch):
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path / "exit"))
        cfg = _write(tmp_path, f'[trace]\nenabled = true\ndir = "{tmp_path / "cfg"}"\n')
        result = CliRunner().invoke(
            cli,
            [
                "--config",
                str(cfg),
                "--trace",
                str(tmp_path / "flag"),
                "--scheduler",
                "sge",
                "status",
            ],
        )
        assert result.exit_code == 0, result.output
        assert list((tmp_path / "flag").rglob("*.json"))
        assert not (tmp_path / "cfg").exists()

    def test_help(self):
        result = CliRunner().invoke(cli, ["--help"])
        assert "--trace" in result.output
        assert "trace" in result.output.split("Commands")[-1]
