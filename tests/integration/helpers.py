"""What the generic and the scheduler-specific test modules both need of the ``hpc`` CLI."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from tests.integration.cluster import ACCOUNTING_TIMEOUT, Cluster

HPC_KEYS = ("HPC_RUNNER_CONFIG", "HPC_SCRIPT_DIR", "HPC_EXIT_DIR")


def hpc(cluster: Cluster, *args: str, check: bool = True) -> str:
    """Run the ``hpc`` console script as the test user; its stdout."""
    return cluster.run([str(cluster.hpc_cli), *args], timeout=90, check=check).stdout


def hpc_json(cluster: Cluster, *args: str) -> Any:
    return json.loads(hpc(cluster, *args, "--json"))


def submitted_id(output: str) -> str:
    """The job id in what ``hpc run --batch`` printed."""
    match = re.search(r"Submitted job (\d+)", output)
    assert match is not None, output
    return match.group(1)


def run_batch(cluster: Cluster, *args: str) -> str:
    """``hpc run --batch`` in the test's directory; the job id."""
    return submitted_id(hpc(cluster, "run", "--batch", "--directory", str(cluster.workdir), *args))


def history_entry(cluster: Cluster, job_id: str) -> dict[str, Any]:
    """Poll ``hpc status --history --json`` until it has *job_id*; return its entry."""
    deadline = time.monotonic() + ACCOUNTING_TIMEOUT
    while True:
        entries = {j["job_id"]: j for j in hpc_json(cluster, "status", "--history")}
        if job_id in entries:
            return entries[job_id]
        assert time.monotonic() < deadline, f"{job_id} not in `hpc status --history`"
        time.sleep(0.5)


def submit_as(cluster: Cluster, user: str, name: str, command: list[str]) -> str:
    """Submit a batch job as *user* through the CLI; return its id.

    D-T31: the API submits as the test process (tester), so other users go
    through ``sudo -u <user> hpc run --batch ...`` with the testbed's HPC_*
    exported.
    """
    env = cluster.host().env
    argv = [
        "env",
        *(f"{k}={env[k]}" for k in HPC_KEYS),
        f"HOME=/home/{user}",
        str(cluster.hpc_cli),
        "run",
        "--batch",
        "--name",
        name,
        "--directory",
        str(cluster.workdir),
        *command,
    ]
    return submitted_id(cluster.run(argv, user=user).stdout)


def trace_records(trace: Path) -> tuple[list[dict], list[dict]]:
    """``(command-line records, scheduler-command records)`` under *trace*."""
    records = [json.loads(p.read_text()) for p in sorted(trace.rglob("*.json"))]
    return (
        [r for r in records if r.get("kind") == "invocation"],
        [r for r in records if r.get("kind") != "invocation"],
    )
