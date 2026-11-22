"""``hpc --trace`` on Slurm: what is recorded is the script's ``#SBATCH`` lines
and squeue's rows."""

from __future__ import annotations

from pathlib import Path

from tests.integration.cluster import Cluster
from tests.integration.helpers import hpc, submitted_id, trace_records


def test_trace_holds_the_slurm_script_and_squeue_rows(cluster: Cluster, workdir: Path) -> None:
    trace = workdir / "trace"
    out = hpc(
        cluster,
        *("--trace", str(trace), "run", "--batch", "--directory", str(workdir)),
        *("--name", "traced", "-S", "--hold", "true"),
    )
    job_id = submitted_id(out)
    hpc(cluster, "--trace", str(trace), "status")
    _, records = trace_records(trace)
    submit = next(r for r in records if r["argv"][0] == "sbatch")
    assert submit["argv"] == ["sbatch", submit["script"]["path"]]
    script = submit["script"]["text"].splitlines()
    assert "#SBATCH --job-name=traced" in script and "#SBATCH --hold" in script
    listing = next(r for r in records if r["argv"][0] == "squeue")
    assert listing["argv"][:4] == ["squeue", "-h", "-u", "tester"]
    # One pipe-delimited row for the held job: its id first, its name last.
    (row,) = listing["stdout"].splitlines()
    fields = row.split("|")
    assert (fields[0], fields[-1]) == (job_id, "traced")
    assert "PENDING" in fields
