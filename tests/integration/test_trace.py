"""``hpc --trace`` and ``[trace]`` against the live scheduler.

The unit suite proves the recorder over the captured fixtures; this proves
that a trace of real ``hpc`` commands holds the command line, the config,
the script, the scheduler's answers and what ``hpc`` printed
(docs/dev/trace/DESIGN.md).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from tests.integration.cluster import ACCOUNTING_TIMEOUT, Cluster
from tests.integration.helpers import hpc, submitted_id, trace_records


def test_flag_records_a_session(cluster: Cluster, workdir: Path) -> None:
    trace = workdir / "trace"
    out = hpc(cluster, "--trace", str(trace), "run", "--batch", "--directory", str(workdir), "true")
    job_id = submitted_id(out)
    hpc(cluster, "--trace", str(trace), "status")
    hpc(cluster, "--trace", str(trace), "status", job_id)

    deadline = time.monotonic() + ACCOUNTING_TIMEOUT
    while True:  # finished and accounted: status <id> then reads the accounting
        history = json.loads(hpc(cluster, "--trace", str(trace), "status", "--history", "--json"))
        if any(j["job_id"] == job_id for j in history):
            break
        assert time.monotonic() < deadline, history
        time.sleep(1)
    hpc(cluster, "--trace", str(trace), "status", job_id)

    invocations, records = trace_records(trace)
    commands = {r["argv"][0] for r in records}
    expected = {cluster.submit_command, cluster.listing_command[0], cluster.accounting_command}
    assert expected <= commands, commands
    submit = next(r for r in records if r["argv"][0] == cluster.submit_command)
    assert submit["returncode"] == 0 and cluster.submit_output(job_id) in submit["stdout"]
    assert all(r["error"] is None for r in records), records
    # The script the scheduler saw, though the file has deleted itself.
    assert submit["script"]["path"] == submit["argv"][1]
    assert submit["script"]["text"].startswith("#!/bin/bash")
    assert f"\n{cluster.directive_prefix} " in submit["script"]["text"]
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
    """``[trace] enabled`` records without the flag (the testbed's config plus the section)."""
    trace = workdir / "trace"
    config = workdir / "hpc-runner.toml"
    config.write_text(
        Path(cluster.config_path).read_text() + f'\n[trace]\nenabled = true\ndir = "{trace}"\n'
    )
    hpc(cluster, "--config", str(config), "status")
    hpc(cluster, "--config", str(config), "status")  # identical: written again
    invocations, records = trace_records(trace)
    assert len(records) == 2, [r["argv"] for r in records]
    listing = list(cluster.listing_command)
    assert all(r["argv"][: len(listing)] == listing for r in records)
    # Each command line has its own scheduler call (same pid) and names the config chain.
    assert len(invocations) == 2
    assert {r["pid"] for r in records} == {i["pid"] for i in invocations}
    assert str(config) in [c["path"] for c in invocations[0]["config"]]
