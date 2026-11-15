"""``hpc --trace`` and ``[trace]`` against the live scheduler.

The unit suite proves the recorder over the captured fixtures; this proves
that a trace of real ``hpc`` commands holds the command line, the config,
the script, the scheduler's answers and what ``hpc`` printed
(docs/dev/trace/DESIGN.md).
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest

from tests.integration.cluster import Cluster

pytestmark = pytest.mark.scheduler("sge")

QACCT_TIMEOUT = 30.0


def _hpc(cluster: Cluster, *args: str, check: bool = True) -> str:
    return cluster.run([str(cluster.hpc_cli), *args], timeout=90, check=check).stdout


def _records(trace: Path) -> tuple[list[dict], list[dict]]:
    """``(command-line records, scheduler-command records)`` under *trace*."""
    records = [json.loads(p.read_text()) for p in sorted(trace.rglob("*.json"))]
    return (
        [r for r in records if r.get("kind") == "invocation"],
        [r for r in records if r.get("kind") != "invocation"],
    )


def test_flag_records_a_session(cluster: Cluster, workdir: Path) -> None:
    trace = workdir / "trace"
    out = _hpc(
        cluster, "--trace", str(trace), "run", "--batch", "--directory", str(workdir), "true"
    )
    match = re.search(r"Submitted job (\d+)", out)
    assert match, out
    job_id = match.group(1)
    _hpc(cluster, "--trace", str(trace), "status")
    _hpc(cluster, "--trace", str(trace), "status", job_id)

    deadline = time.monotonic() + QACCT_TIMEOUT
    while True:  # finished and accounted: status <id> then falls back to qacct
        history = json.loads(_hpc(cluster, "--trace", str(trace), "status", "--history", "--json"))
        if any(j["job_id"] == job_id for j in history):
            break
        assert time.monotonic() < deadline, history
        time.sleep(1)
    _hpc(cluster, "--trace", str(trace), "status", job_id)

    invocations, records = _records(trace)
    commands = {r["argv"][0] for r in records}
    assert {"qsub", "qstat", "qacct"} <= commands, commands
    submit = next(r for r in records if r["argv"][0] == "qsub")
    assert submit["returncode"] == 0 and f"Your job {job_id}" in submit["stdout"]
    assert all(r["error"] is None for r in records), records
    # The script qsub saw, though the file has deleted itself.
    assert submit["script"]["path"] == submit["argv"][1]
    assert submit["script"]["text"].startswith("#!/bin/bash")
    assert "#$ -N" in submit["script"]["text"]
    assert not Path(submit["script"]["path"]).exists()

    # One record per command line, each with what was typed and printed.
    run = next(r for r in invocations if "run" in r["argv"])
    assert run["argv"][-5:] == ["run", "--batch", "--directory", str(workdir), "true"]
    assert run["exit_code"] == 0 and run["error"] is None
    assert f"Submitted job {job_id}" in run["stdout"]
    assert run["pid"] == submit["pid"]
    assert [c["path"] for c in run["config"]] and all(c["text"] for c in run["config"])
    assert len(invocations) >= 5
    assert all(r["exit_code"] == 0 for r in invocations), invocations


def test_config_enabled_trace(cluster: Cluster, workdir: Path) -> None:
    """``[trace] enabled`` records without the flag (a config extending the testbed's)."""
    trace = workdir / "trace"
    config = workdir / "hpc-runner.toml"
    config.write_text(
        "config_version = 1\n"
        f'extends = "{cluster.config_path}"\n\n'
        f'[trace]\nenabled = true\ndir = "{trace}"\n'
    )
    _hpc(cluster, "--config", str(config), "status")
    _hpc(cluster, "--config", str(config), "status")  # identical: written again
    invocations, records = _records(trace)
    assert len(records) == 2, [r["argv"] for r in records]
    assert all(r["argv"][:2] == ["qstat", "-xml"] for r in records)
    assert "<job_info" in records[0]["stdout"]
    # Each command line has its own scheduler call (same pid) and names the config chain.
    assert len(invocations) == 2
    assert {r["pid"] for r in records} == {i["pid"] for i in invocations}
    assert str(config) in [c["path"] for c in invocations[0]["config"]]
