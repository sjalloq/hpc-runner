"""``hpc trace compare`` over traces built from the captured SGE fixtures (D-TR9..D-TR11)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from hpc_runner.cli.main import cli
from hpc_runner.trace.compare import Record, check, compare, fixture_shapes, load_records, shape


class TestShape:
    @pytest.mark.parametrize(
        ("argv", "expected"),
        [
            (["qstat", "-xml", "-u", "*"], "qstat -xml -u <arg>"),
            (["qstat", "-xml", "-u", "tester"], "qstat -xml -u <arg>"),
            (["qstat", "-j", "104", "-xml"], "qstat -j <arg> -xml"),
            (["qacct", "-j", "95"], "qacct -j <arg>"),
            (["qacct", "-j", "97", "-t", "2"], "qacct -j <arg> -t <arg>"),
            (["qacct", "-j", "-o", "bob", "-b", "202610311200.00"], "qacct -j -o <arg> -b <arg>"),
            (["/opt/sge/bin/qsub", "/tmp/hpc/job.sh"], "qsub <arg>"),
            (["qdel", "101"], "qdel <arg>"),
            (
                ["qrsh", "-now", "no", "-cwd", "-V", "--", "xterm", "-fg", "red"],
                "qrsh -now <arg> -cwd -V -- ...",
            ),
            (["qsub", "-t", "1-10:2", "x.sh"], "qsub -t <arg> <arg>"),
            ([], ""),
        ],
    )
    def test_shapes(self, argv, expected):
        assert shape(argv) == expected


def _write(root: Path, name: str, **data) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.json"
    base = {"returncode": 0, "stdout": "", "stderr": "", "error": None}
    path.write_text(json.dumps({**base, **data}))
    return path


def _trace_from_cli(tmp_path, sge_fixtures, monkeypatch) -> Path:
    """A trace of hpc status in its three modes plus a batch submit, over the fake seam."""
    monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path / "exit"))
    monkeypatch.setenv("USER", "tester")
    monkeypatch.chdir(tmp_path)
    trace = tmp_path / "trace"
    runner = CliRunner()
    for args in (
        ["status"],
        ["status", "--history"],
        ["status", sge_fixtures.job("running")],
        ["status", sge_fixtures.job("ok")],
        ["run", "--batch", "echo", "hi"],
    ):
        result = runner.invoke(cli, ["--trace", str(trace), "--scheduler", "sge", *args])
        assert result.exit_code == 0, (args, result.output)
    return trace


class TestCompareCleanTrace:
    def test_everything_parses(self, tmp_path, mock_sge_commands, sge_fixtures, monkeypatch):
        trace = _trace_from_cli(tmp_path, sge_fixtures, monkeypatch)
        report = compare(trace)
        assert report.findings == []
        assert set(report.shapes) >= {
            "qstat -xml -u <arg>",
            "qacct -j -o <arg> -b <arg>",
            "qstat -j <arg> -xml",
            "qacct -j <arg>",
            "qsub <arg>",
        }
        assert report.uncovered == [] and report.covered is None

    def test_json_fixtures_are_records(self, sge_fixtures):
        """The fixture root's own JSON files (master down) replay clean."""
        report = compare(sge_fixtures.root)
        assert [r.argv for r in report.records] == [["qstat", "-xml", "-u", "*"]]
        assert report.findings == []

    def test_coverage_against_the_captured_fixtures(
        self, tmp_path, mock_sge_commands, sge_fixtures, monkeypatch
    ):
        """Every shape hpc status and a batch submit produce has a captured fixture."""
        trace = _trace_from_cli(tmp_path, sge_fixtures, monkeypatch)
        report = compare(trace, fixtures=sge_fixtures.root)
        assert report.covered is not None
        assert report.uncovered == [], report.uncovered

    def test_fixture_shapes_from_manifest_and_json(self, tmp_path):
        root = tmp_path / "fx"
        root.mkdir()
        (root / "manifest.json").write_text(
            json.dumps(
                {"files": {"a.txt": {"what": "x", "argv": ["qacct", "-j", "5"]}, "old.txt": "desc"}}
            )
        )
        _write(root, "down", argv=["qstat", "-xml", "-u", "*"], returncode=1)
        assert fixture_shapes(root) == {"qacct -j <arg>", "qstat -xml -u <arg>"}


class TestFindings:
    def _rec(self, argv, returncode=0, stderr="", error=None) -> Record:
        return Record(
            path=Path("x.json"),
            argv=argv,
            returncode=returncode,
            stdout="",
            stderr=stderr,
            error=error,
        )

    def test_call_that_never_completed(self):
        rec = self._rec(["qstat", "-xml", "-u", "*"], error="timed out after 60s")
        assert check(rec) == ["call did not complete: timed out after 60s"]

    def test_qsub_without_an_id(self):
        rec = self._rec(["qsub", "job.sh"])
        rec.stdout = "Unable to run job: something\n"
        assert check(rec) == ["qsub succeeded but no job id was parsed from its stdout"]
        rec.returncode = 1
        assert check(rec) == []

    def test_qstat_unknown_state(self, sge_fixtures):
        rec = self._rec(["qstat", "-xml", "-u", "*"])
        rec.stdout = sge_fixtures.text("qstat_mixed.xml").replace(
            "<state>r</state>", "<state>zz</state>", 1
        )
        (finding,) = check(rec)
        assert "state 'zz' maps to UNKNOWN" in finding

    def test_qstat_broken_xml(self, sge_fixtures):
        rec = self._rec(["qstat", "-xml", "-u", "*"])
        rec.stdout = sge_fixtures.text("qstat_mixed.xml")[:-40]
        assert check(rec) == ["qstat -xml lists jobs but none were parsed"]

    def test_qstat_unrecognised_failure(self):
        rec = self._rec(["qstat", "-xml", "-u", "*"], returncode=1, stderr="error: disk on fire\n")
        assert check(rec) == [
            "qstat failed and its stderr is not recognised as an unreachable qmaster"
        ]
        rec.stderr = "error: unable to contact qmaster using port 6444\n"
        assert check(rec) == []

    def test_qstat_j_without_details(self):
        rec = self._rec(["qstat", "-j", "7", "-xml"])
        rec.stdout = "<detailed_job_info><djob_info></djob_info></detailed_job_info>"
        assert check(rec) == ["qstat -j -xml held a job but no details were parsed"]
        rec.stdout = "<unknown_jobs><element><ST_name>7</ST_name></element></unknown_jobs>"
        assert check(rec) == []

    def test_qacct_fields_not_parsed(self, sge_fixtures):
        text = sge_fixtures.text("qacct_ok.txt")
        rec = self._rec(["qacct", "-j", sge_fixtures.job("ok")])
        rec.stdout = text
        assert check(rec) == []
        rec.stdout = text.replace("exit_status  0", "exit_status  n/a")
        assert any("exit_status 'n/a' not parsed" in m for m in check(rec))
        rec.stdout = re.sub(r"ru_wallclock +\S+", "ru_wallclock 7 seconds", text)
        assert any("ru_wallclock '7 seconds' not parsed" in m for m in check(rec)), check(rec)

    def test_qacct_wallclock_7s_is_fine(self, sge_fixtures):
        """The bug the trace exists for: gridengine 8.1.9 writes ``7s``; it must parse."""
        for name in ("qacct_ok.txt", "qacct_exit1.txt", "qacct_signal.txt", "qacct_lost_node.txt"):
            rec = self._rec(["qacct", "-j", "1"])
            rec.stdout = sge_fixtures.text(name)
            assert check(rec) == [], name

    def test_qacct_not_found_is_fine(self, sge_fixtures):
        rc, out, err = sge_fixtures.result("qacct_missing.txt")
        rec = self._rec(["qacct", "-j", "999999"], returncode=rc, stderr=err)
        rec.stdout = out
        assert check(rec) == []
        rec.stderr = "error: accounting file unreadable\n"
        assert check(rec) == ["qacct failed and its stderr is not a recognised 'not found'"]

    def test_parser_exception_is_a_finding(self, monkeypatch):
        from hpc_runner.trace import compare as mod

        def boom(rec):
            raise ValueError("bad day")

        monkeypatch.setitem(mod.CHECKS, "qstat", boom)
        assert check(self._rec(["qstat", "-xml"])) == ["parser raised ValueError: bad day"]

    def test_other_commands_have_no_checks(self):
        assert check(self._rec(["qdel", "5"])) == []
        assert check(self._rec(["qrsh", "-now", "no", "--", "xterm"])) == []


class TestLoad:
    def test_skips_non_records(self, tmp_path):
        _write(tmp_path / "2026-10-31", "a", argv=["qdel", "1"])
        (tmp_path / "2026-10-31" / "junk.json").write_text("[1, 2]")
        (tmp_path / "2026-10-31" / "bad.json").write_text("{not json")
        (tmp_path / "manifest.json").write_text(json.dumps({"argv": ["x"]}))
        assert [r.argv for r in load_records(tmp_path)] == [["qdel", "1"]]


class TestCli:
    def test_empty(self, tmp_path):
        result = CliRunner().invoke(cli, ["trace", "compare", str(tmp_path)])
        assert result.exit_code == 0
        assert "No trace records" in result.output

    def test_clean_table(self, tmp_path, mock_sge_commands, sge_fixtures, monkeypatch):
        trace = _trace_from_cli(tmp_path, sge_fixtures, monkeypatch)
        result = CliRunner(env={"COLUMNS": "200"}).invoke(
            cli, ["trace", "compare", str(trace), "--fixtures", str(sge_fixtures.root)]
        )
        assert result.exit_code == 0, result.output
        assert "Every record parsed" in result.output
        assert "qacct -j <arg>" in result.output and "yes" in result.output

    def test_findings_and_uncovered_exit_1(self, tmp_path):
        day = tmp_path / "trace" / "2026-10-31"
        _write(day, "a", argv=["qstat", "-xml", "-u", "*"], error="timed out after 60s")
        _write(day, "b", argv=["qhost", "-xml"])
        fx = tmp_path / "fx"
        fx.mkdir()
        (fx / "manifest.json").write_text(json.dumps({"files": {}}))
        result = CliRunner(env={"COLUMNS": "200"}).invoke(
            cli, ["trace", "compare", str(tmp_path / "trace"), "--fixtures", str(fx)]
        )
        assert result.exit_code == 1
        assert "1 finding(s)" in result.output
        assert "call did not complete" in result.output
        assert "2 shape(s) with no captured fixture" in result.output

    def test_json(self, tmp_path):
        day = tmp_path / "2026-10-31"
        _write(day, "a", argv=["qdel", "1"])
        result = CliRunner().invoke(cli, ["trace", "compare", str(tmp_path), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["records"] == 1
        assert data["shapes"] == [
            {"shape": "qdel <arg>", "calls": 1, "findings": 0, "fixture": None}
        ]
        assert data["findings"] == [] and data["uncovered"] == []
