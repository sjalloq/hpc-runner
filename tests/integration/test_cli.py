"""The ``hpc`` CLI against the live scheduler: status, history, details, config.

The API paths are covered elsewhere; these run the console script itself
(``cluster.hpc_cli``) so the output a user reads is what is checked.
"""

from __future__ import annotations

import re
import shlex
from typing import Any

from hpc_runner.core.result import JobStatus
from tests.integration.cluster import Cluster
from tests.integration.helpers import history_entry, hpc, hpc_json, run_batch


def _hold(cluster: Cluster) -> tuple[str, str]:
    """``hpc run`` options that keep the job pending (-S carries raw flags, D-T43)."""
    return ("-S", shlex.join(cluster.hold_args))


def _assert_times(entry: dict[str, Any]) -> None:
    """Submit, start and end all read from the scheduler, in order
    ("YYYY-mm-dd HH:MM:SS", so they compare as text)."""
    times = [entry["submit_time"], entry["start_time"], entry["end_time"]]
    assert all(times) and times == sorted(times), entry


def test_status_active_history_and_details(cluster: Cluster) -> None:
    """`hpc status` lists a held job, `--history` and `<id>` report it once done."""
    job_id = run_batch(cluster, "--name", "clistat", *_hold(cluster), "sh", "-c", "exit 3")
    assert cluster.wait_for(job_id, JobStatus.PENDING, timeout=5) is JobStatus.PENDING

    active = {j["job_id"]: j for j in hpc_json(cluster, "status")}
    assert active[job_id]["status"] == "PENDING" and active[job_id]["name"] == "clistat"
    assert active[job_id]["submit_time"], active[job_id]
    assert job_id in hpc(cluster, "status")  # the table, too

    cluster.release_job(job_id)
    assert cluster.wait_for(job_id, JobStatus.FAILED) is JobStatus.FAILED
    done = history_entry(cluster, job_id)
    assert (done["status"], done["exit_code"]) == ("FAILED", 3)
    assert done["queue"] == cluster.default_queue and done["node"] in {"exec1", "exec2"}
    _assert_times(done)

    details = hpc_json(cluster, "status", job_id)
    assert (details["job_id"], details["status"], details["exit_code"]) == (job_id, "FAILED", 3)
    _assert_times(details)
    assert job_id not in hpc(cluster, "status")  # gone from the active table


def test_cancel_by_filter_by_id_and_a_job_that_is_gone(cluster: Cluster) -> None:
    """`hpc cancel` removes jobs chosen by name and by id; an id the scheduler
    no longer lists is shown as UNKNOWN and left alone."""
    hold = _hold(cluster)  # pending until cancelled
    by_name = [run_batch(cluster, "--name", "clican_a", *hold, "true") for _ in range(2)]
    by_id = run_batch(cluster, "--name", "clikeep", *hold, "true")
    for job_id in (*by_name, by_id):
        assert cluster.wait_for(job_id, JobStatus.PENDING, timeout=5) is JobStatus.PENDING

    out = hpc(cluster, "cancel", "--force", "-N", "clican_.*", "--pending")
    assert "Cancelled 2 job(s)" in out, out
    active = {j["job_id"] for j in hpc_json(cluster, "status")}
    assert by_id in active and not active & set(by_name)

    assert "Cancelled 1 job(s)" in hpc(cluster, "cancel", "--force", by_id)
    gone = hpc(cluster, "cancel", by_id)  # no --force: nothing to confirm
    assert "UNKNOWN" in gone and "0 job(s) will be cancelled" in gone, gone


def test_run_exits_with_the_jobs_exit_code(cluster: Cluster) -> None:
    """`hpc run` of a command that exits 3 leaves 3 in `$?`: interactive, and --batch --wait."""
    run = [str(cluster.hpc_cli), "run", "--directory", str(cluster.workdir)]
    interactive = cluster.run([*run, "sh", "-c", "exit 3"], check=False, timeout=120)
    assert interactive.returncode == 3, interactive.stdout + interactive.stderr
    waited = cluster.run(
        [*run, "--batch", "--wait", "sh", "-c", "exit 3"], check=False, timeout=120
    )
    assert waited.returncode == 3, waited.stdout + waited.stderr
    assert "status: FAILED" in waited.stdout
    submitted = cluster.run([*run, "--batch", "sh", "-c", "exit 3"], check=False, timeout=120)
    assert submitted.returncode == 0, submitted.stdout + submitted.stderr


def test_tool_alias_and_config_resolve(cluster: Cluster) -> None:
    """[tools.sort] commands = ["sort", "uniq"]: `uniq` gets the entry's queue, live."""
    table = hpc(cluster, "config", "resolve", "uniq", "/etc/hostname")
    row = next(line for line in table.splitlines() if re.search(r"\bqueue\b", line))
    assert cluster.other_queue in row and "tools.sort" in row, table

    job_id = run_batch(cluster, "--name", "alias", "uniq", "/etc/hostname")
    assert cluster.wait_for(job_id, JobStatus.COMPLETED) is JobStatus.COMPLETED
    assert history_entry(cluster, job_id)["queue"] == cluster.other_queue
    other = run_batch(cluster, "--name", "noalias", "cat", "/etc/hostname")
    assert cluster.wait_for(other, JobStatus.COMPLETED) is JobStatus.COMPLETED
    assert history_entry(cluster, other)["queue"] == cluster.default_queue
