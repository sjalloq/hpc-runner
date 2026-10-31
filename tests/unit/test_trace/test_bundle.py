"""``hpc trace bundle``: the scrubber over the captured SGE fixtures (D-TR8)."""

from __future__ import annotations

import json
import tarfile

import pytest
from click.testing import CliRunner

from hpc_runner.cli.main import cli
from hpc_runner.trace.bundle import Scrubber, bundle
from hpc_runner.trace.compare import compare, load_records
from tests.unit.test_trace.test_compare import _trace_from_cli


@pytest.fixture
def scrubber(monkeypatch) -> Scrubber:
    monkeypatch.setenv("USER", "alice")
    monkeypatch.setenv("HOME", "/home/alice")
    monkeypatch.setattr("hpc_runner.trace.bundle.socket.gethostname", lambda: "login.example.net")
    return Scrubber()


class TestCollect:
    def test_process_facts(self, scrubber):
        assert scrubber.tokens["user"] == {"alice": "user1"}
        assert scrubber.tokens["host"] == {"login.example.net": "host1", "login": "host2"}

    def test_qacct(self, scrubber, sge_fixtures):
        scrubber.collect(load_records(sge_fixtures.root))  # the master-down JSON
        scrubber._collect_text(sge_fixtures.text("qacct_ok.txt"))
        assert scrubber.tokens["user"]["tester"] == "user2"
        assert "exec1" in scrubber.tokens["host"] or "exec2" in scrubber.tokens["host"]
        assert scrubber.tokens["host"]["master"]  # from `on host "master"`
        names = scrubber.tokens["name"]
        assert "ok" in names and "all.q" in names
        assert "NONE" not in names and "defaultdepartment" not in names and "sge" not in names

    def test_qstat_xml_and_argv(self, scrubber, sge_fixtures):
        scrubber._collect_text(sge_fixtures.text("qstat_mixed.xml"))
        scrubber._collect_argv(["qacct", "-j", "-o", "bob"])
        scrubber._collect_argv(["qsub", "-N", "secret-chip", "-l", "hostname=node7", "x.sh"])
        assert {"tester", "bob"} <= set(scrubber.tokens["user"])
        assert {"running", "array", "all.q", "secret-chip"} <= set(scrubber.tokens["name"])
        assert "node7" in scrubber.tokens["host"]

    def test_refuses_short_and_placeholder_values(self, scrubber):
        assert scrubber.add("user", "x") is None
        assert scrubber.add("name", "NONE") is None
        assert scrubber.add("name", "has space") is None
        assert scrubber.add("user", "bob") == "user2"
        assert scrubber.add("user", "bob") == "user2"  # stable


class TestReplace:
    def test_users_and_hosts_everywhere(self, scrubber):
        scrubber.add("user", "tester")
        scrubber.add("host", "exec1")
        text = "owner tester\n/home/tester/x all.q@exec1 tester's job, exec10 stays, exec1.\n"
        out = scrubber.text(text)
        assert "tester" not in out and "exec1 " not in out
        assert out == "owner user2\n/home/user2/x all.q@host3 user2's job, exec10 stays, host3.\n"

    def test_names_only_where_written(self, scrubber, sge_fixtures):
        """A job called ``array`` must not rewrite ``job-array`` or ``state="running"``."""
        xml = sge_fixtures.text("qstat_mixed.xml")
        scrubber._collect_text(xml)
        out = scrubber.text(xml)
        assert "<JB_name>array</JB_name>" not in out and "<JB_name>running</JB_name>" not in out
        assert 'state="running"' in out and 'state="pending"' in out
        assert "<JB_owner>tester</JB_owner>" not in out
        qsub = scrubber.text('Your job-array 97.1-4:1 ("array") has been submitted\n')
        assert qsub.startswith('Your job-array 97.1-4:1 ("name')

    def test_environment_values(self, scrubber, sge_fixtures):
        xml = sge_fixtures.text("qstat_j_running.xml")
        out = scrubber.text(xml)
        assert "<VA_value>[scrubbed]</VA_value>" in out
        assert "/usr/local/sbin" not in out
        assert "<VA_variable>TERM</VA_variable>" in out  # variable names stay

    def test_longest_first(self, scrubber):
        scrubber.add("host", "node")
        scrubber.add("host", "node.site.example")
        assert scrubber.text("node.site.example and node") == "host4 and host3"

    def test_argv(self, scrubber):
        scrubber.add("name", "secret")
        scrubber.add("user", "bob")
        assert scrubber.argv(["qsub", "-N", "secret", "/home/bob/j.sh"]) == [
            "qsub",
            "-N",
            "name1",
            "/home/user2/j.sh",
        ]


class TestBundle:
    def test_archive_is_clean_and_still_parses(
        self, tmp_path, mock_sge_commands, sge_fixtures, monkeypatch
    ):
        trace = _trace_from_cli(tmp_path, sge_fixtures, monkeypatch)
        out, scrubber = bundle(
            trace, tmp_path / "b.tar.gz", scheduler_config={"sge": {"owner": "tester", "pe": "smp"}}
        )
        assert scrubber is not None and scrubber.tokens["user"]["tester"]
        extracted = tmp_path / "x"
        with tarfile.open(out) as tar:
            tar.extractall(extracted, filter="data")
        (top,) = extracted.iterdir()
        meta = json.loads((top / "meta.json").read_text())
        assert meta["records"] == len(load_records(trace)) and meta["scrubbed"] is True
        assert meta["replacements"]["user"] >= 1
        # The config travels with users and hosts scrubbed (names are positional, D-TR8).
        assert meta["schedulers"] == {
            "sge": {"owner": scrubber.tokens["user"]["tester"], "pe": "smp"}
        }
        texts = [p.read_text() for p in (top / "trace").rglob("*.json")]
        assert texts
        for text in texts:
            for value in list(scrubber.tokens["user"]) + list(scrubber.tokens["host"]):
                assert value not in text, (value, text[:200])
            assert "VA_value>/" not in text
        # The scrubbed records are still a trace the parsers accept.
        report = compare(top / "trace")
        assert report.findings == [], report.findings
        assert report.shapes == compare(trace).shapes

    def test_raw(self, tmp_path, mock_sge_commands, sge_fixtures, monkeypatch):
        trace = _trace_from_cli(tmp_path, sge_fixtures, monkeypatch)
        out, scrubber = bundle(trace, tmp_path / "raw.tar.gz", raw=True)
        assert scrubber is None
        with tarfile.open(out) as tar:
            names = tar.getnames()
            meta = json.loads(
                tar.extractfile(next(n for n in names if n.endswith("meta.json"))).read()
            )
            any_record = next(n for n in names if "/trace/" in n)
            text = tar.extractfile(any_record).read().decode()
        assert meta["scrubbed"] is False and meta["replacements"] is None
        assert "tester" in text


class TestCli:
    def test_bundle_prints_map_and_path(
        self, tmp_path, mock_sge_commands, sge_fixtures, monkeypatch
    ):
        trace = _trace_from_cli(tmp_path, sge_fixtures, monkeypatch)
        out = tmp_path / "issue.tar.gz"
        result = CliRunner().invoke(
            cli, ["trace", "bundle", str(trace), "-o", str(out), "--scrub", "someone"]
        )
        assert result.exit_code == 0, result.output
        assert result.stdout.strip() == str(out)
        assert "scrubbed (check before attaching):" in result.stderr
        assert "tester -> user" in result.stderr
        assert "someone -> user" in result.stderr
        assert out.is_file()

    def test_raw_warns(self, tmp_path, mock_sge_commands, sge_fixtures, monkeypatch):
        trace = _trace_from_cli(tmp_path, sge_fixtures, monkeypatch)
        result = CliRunner().invoke(
            cli, ["trace", "bundle", str(trace), "-o", str(tmp_path / "r.tar.gz"), "--raw"]
        )
        assert result.exit_code == 0, result.output
        assert "not scrubbed" in result.stderr

    def test_default_output_name(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "t" / "2026-10-31").mkdir(parents=True)
        result = CliRunner().invoke(cli, ["trace", "bundle", str(tmp_path / "t")])
        assert result.exit_code == 0, result.output
        (archive,) = tmp_path.glob("hpc-trace-*.tar.gz")
        assert result.stdout.strip().endswith(archive.name)
