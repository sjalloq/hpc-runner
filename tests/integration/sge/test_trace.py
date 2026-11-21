"""``hpc --trace`` on SGE: the script's ``#$`` lines and qstat's XML are what is recorded."""

from __future__ import annotations

from pathlib import Path

from tests.integration.cluster import Cluster
from tests.integration.helpers import hpc, trace_records


def test_trace_holds_the_sge_script_and_qstat_xml(cluster: Cluster, workdir: Path) -> None:
    trace = workdir / "trace"
    hpc(cluster, "--trace", str(trace), "run", "--batch", "--directory", str(workdir), "true")
    hpc(cluster, "--trace", str(trace), "status")
    _, records = trace_records(trace)
    submit = next(r for r in records if r["argv"][0] == "qsub")
    assert "#$ -N" in submit["script"]["text"]
    listing = next(r for r in records if r["argv"][:2] == ["qstat", "-xml"])
    assert "<job_info" in listing["stdout"]
