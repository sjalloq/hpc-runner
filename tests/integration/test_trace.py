"""``hpc --trace`` and ``[trace]`` against the live scheduler, replayed by ``hpc trace compare``.

The unit suite proves the recorder over the captured fixtures; this proves
that a trace of real ``hpc`` invocations parses clean and that every call
shape they produce has a captured fixture (docs/dev/trace/DESIGN.md D-TR10).
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from tests.integration.cluster import SRC_ROOT, Cluster

FIXTURES = SRC_ROOT / "tests" / "unit" / "fixtures" / "sge"
QACCT_TIMEOUT = 30.0


def _hpc(cluster: Cluster, *args: str, check: bool = True) -> str:
    return cluster.run([str(cluster.hpc_cli), *args], timeout=90, check=check).stdout


def _records(trace: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(trace.rglob("*.json"))]


def test_flag_records_and_compares_clean(cluster: Cluster, workdir: Path) -> None:
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

    records = _records(trace)
    commands = {r["argv"][0] for r in records}
    assert {"qsub", "qstat", "qacct"} <= commands, commands
    submit = next(r for r in records if r["argv"][0] == "qsub")
    assert submit["returncode"] == 0 and f"Your job {job_id}" in submit["stdout"]
    assert all(r["error"] is None for r in records), records

    proc = cluster.run(
        [str(cluster.hpc_cli), "trace", "compare", str(trace), "--fixtures", str(FIXTURES)],
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Every record parsed" in proc.stdout
    assert "no captured fixture" not in proc.stdout


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
    _hpc(cluster, "--config", str(config), "status")  # identical: deduplicated
    records = _records(trace)
    assert len(records) == 1, [r["argv"] for r in records]
    assert records[0]["argv"][:2] == ["qstat", "-xml"]
    assert "<job_info" in records[0]["stdout"]


def test_bundle_is_scrubbed_and_replays(cluster: Cluster, workdir: Path) -> None:
    """A bundle of a live trace names no user or host and still passes compare."""
    import tarfile

    trace = workdir / "trace"
    _hpc(cluster, "--trace", str(trace), "status")
    _hpc(cluster, "--trace", str(trace), "status", "--history")
    out = workdir / "issue.tar.gz"
    proc = cluster.run([str(cluster.hpc_cli), "trace", "bundle", str(trace), "-o", str(out)])
    assert proc.stdout.strip() == str(out), proc.stdout
    assert "tester -> user" in proc.stderr, proc.stderr

    extracted = workdir / "x"
    with tarfile.open(out) as tar:
        tar.extractall(extracted, filter="data")
    (top,) = extracted.iterdir()
    texts = [p.read_text() for p in (top / "trace").rglob("*.json")]
    assert texts
    for text in texts:
        for word in ("tester", "exec1", "exec2", "login", "/home/"):
            assert word not in text, (word, text[:300])
    meta = json.loads((top / "meta.json").read_text())
    assert meta["scrubbed"] is True and meta["scheduler"].startswith("SGE")
    proc = cluster.run([str(cluster.hpc_cli), "trace", "compare", str(top / "trace")], check=False)
    assert proc.returncode == 0, proc.stdout
