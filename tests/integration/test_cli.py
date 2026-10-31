"""The ``hpc`` CLI against the live scheduler: status, history, details, config.

The API paths are covered elsewhere; these run the console script itself
(``cluster.hpc_cli``) so the output a user reads is what is checked.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from hpc_runner.core.result import JobStatus
from tests.integration.cluster import Cluster

QACCT_TIMEOUT = 30.0


def _hpc(cluster: Cluster, *args: str) -> str:
    return cluster.run([str(cluster.hpc_cli), *args], timeout=90).stdout


def _hpc_json(cluster: Cluster, *args: str) -> Any:
    return json.loads(_hpc(cluster, *args, "--json"))


def _run_batch(cluster: Cluster, *args: str) -> str:
    out = _hpc(cluster, "run", "--batch", "--directory", str(cluster.workdir), *args)
    match = re.search(r"Submitted job (\d+)", out)
    assert match, out
    return match.group(1)


def _history_entry(cluster: Cluster, job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + QACCT_TIMEOUT
    while True:
        entries = {j["job_id"]: j for j in _hpc_json(cluster, "status", "--history")}
        if job_id in entries:
            return entries[job_id]
        assert time.monotonic() < deadline, f"{job_id} not in `hpc status --history`"
        time.sleep(0.5)


def test_status_active_history_and_details(cluster: Cluster) -> None:
    """`hpc status` lists a held job, `--history` and `<id>` report it once done."""
    # A far-future qsub -a start time keeps the job pending (-S carries raw flags, D-T43).
    job_id = _run_batch(cluster, "--name", "clistat", "-S", "-a 203001010000", "sh", "-c", "exit 3")
    assert cluster.wait_for(job_id, JobStatus.PENDING, timeout=5) is JobStatus.PENDING

    active = {j["job_id"]: j for j in _hpc_json(cluster, "status")}
    assert active[job_id]["status"] == "PENDING" and active[job_id]["name"] == "clistat"
    assert job_id in _hpc(cluster, "status")  # the table, too

    cluster.run(["qalter", "-a", "200001010000", job_id])  # start time in the past: eligible
    assert cluster.wait_for(job_id, JobStatus.FAILED) is JobStatus.FAILED
    done = _history_entry(cluster, job_id)
    assert (done["status"], done["exit_code"], done["failed_code"]) == ("FAILED", 3, 0)
    assert done["queue"] == "all.q" and done["node"] in {"exec1", "exec2"}

    details = _hpc_json(cluster, "status", job_id)
    assert (details["job_id"], details["status"], details["exit_code"]) == (job_id, "FAILED", 3)
    assert job_id not in _hpc(cluster, "status")  # gone from the active table


def test_status_shows_the_schedulers_failure_reason(cluster: Cluster) -> None:
    """A job killed by qdel: exit 137, failed 100 and SGE's words, in `hpc status <id>`."""
    job_id = _run_batch(cluster, "--name", "clikill", "sleep", "300")
    assert cluster.wait_for(job_id, JobStatus.RUNNING) is JobStatus.RUNNING
    _hpc(cluster, "cancel", "--force", job_id)
    done = _history_entry(cluster, job_id)
    assert (done["status"], done["exit_code"], done["failed_code"]) == ("FAILED", 137, 100)
    assert done["failed_reason"] == "assumedly after job"
    table = _hpc(cluster, "status", job_id)
    assert "137" in table and "100: assumedly after job" in table


def test_tool_alias_and_config_resolve(cluster: Cluster) -> None:
    """[tools.sort] commands = ["sort", "uniq"]: `uniq` gets the entry's queue, live."""
    table = _hpc(cluster, "config", "resolve", "uniq", "/etc/hostname")
    row = next(line for line in table.splitlines() if re.search(r"\bqueue\b", line))
    assert "short.q" in row and "tools.sort" in row, table

    job_id = _run_batch(cluster, "--name", "alias", "uniq", "/etc/hostname")
    assert cluster.wait_for(job_id, JobStatus.COMPLETED) is JobStatus.COMPLETED
    assert _history_entry(cluster, job_id)["queue"] == "short.q"
    other = _run_batch(cluster, "--name", "noalias", "cat", "/etc/hostname")
    assert cluster.wait_for(other, JobStatus.COMPLETED) is JobStatus.COMPLETED
    assert _history_entry(cluster, other)["queue"] == "all.q"


def test_hold_flag_forwards(cluster: Cluster) -> None:
    """D-T43: qsub's user hold reaches the job through ``-S '-h'``: it queues ``hqw``."""
    out = _hpc(
        cluster, "run", "--batch", "--directory", str(cluster.workdir), "-S", "-h", "sleep", "30"
    )
    match = re.search(r"Submitted job (\d+)", out)
    assert match, out
    job_id = match.group(1)
    assert "Usage:" not in out
    states = {
        line.split()[0]: line.split()[4]
        for line in cluster.run(["qstat", "-u", "tester"]).stdout.splitlines()[2:]
        if line.split()
    }
    assert states.get(job_id) == "hqw", states
    assert _hpc_json(cluster, "status")[0]["status"] == "PENDING"
    cluster.run(["qrls", job_id])
    assert cluster.wait_for(job_id, JobStatus.RUNNING) is JobStatus.RUNNING
