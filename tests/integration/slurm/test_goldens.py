"""Every Slurm batch golden script through ``sbatch --test-only`` on the real scheduler.

The goldens (tests/unit/golden/slurm_batch_*.sh) pin what hpc-runner renders;
this pins that Slurm *reads* it. ``--test-only`` reads the ``#SBATCH`` lines,
asks the controller whether and when the job could run, and submits nothing.
``sbatch`` reads every line before it asks, so the controller's answer, yes
or no, means every line was read. The goldens ask for site things the
testbed does not have (four CPUs and 16 GB on two-CPU, 2 GB nodes, parents
100, 200 and 300), so each has the verdict it must get; what must never
appear is a line ``sbatch`` itself cannot read, which exits 255.
"""

from __future__ import annotations

import shutil

import pytest

from tests.integration.cluster import SRC_ROOT, Cluster

GOLDEN_DIR = SRC_ROOT / "tests" / "unit" / "golden"
#: ``sbatch --test-only`` of a job that could run: "sbatch: Job <id> to start at ...".
WOULD_START = "to start at"
TOO_BIG = "allocation failure: Requested node configuration is not available"
#: Parents the controller does not know. 24.11 says so when the ids are
#: unknown to it; otherwise the request is refused for its size.
NO_PARENT = "allocation failure: Job dependency problem"
#: How ``sbatch`` exits when it cannot read an option.
UNREADABLE = 255

#: golden -> (exit status, the texts one of which the output must contain).
EXPECTED: dict[str, tuple[int, tuple[str, ...]]] = {
    "slurm_batch_minimal.sh": (0, (WOULD_START,)),
    # --cpus-per-task=4 on two-CPU nodes.
    "slurm_batch_full_environment.sh": (1, (TOO_BIG,)),
    # 2 x --mem-per-cpu=4096 on 2000 MB nodes, after parents 100, 200 and 300.
    "slurm_batch_array_with_dependencies.sh": (1, (TOO_BIG, NO_PARENT)),
    # The golden's scheduler_args are SGE's (-l scratch=20G ...), passed on
    # untouched: sbatch reads every hpc-runner line, then stops at -l.
    "slurm_batch_passthrough_resources_project.sh": (
        UNREADABLE,
        ("sbatch: invalid option -- 'l'",),
    ),
}


def test_every_slurm_batch_golden_is_covered() -> None:
    assert sorted(p.name for p in GOLDEN_DIR.glob("slurm_batch_*.sh")) == sorted(EXPECTED)


@pytest.mark.parametrize("golden", sorted(EXPECTED))
def test_sbatch_reads_the_golden(cluster: Cluster, golden: str) -> None:
    script = cluster.workdir / golden
    shutil.copy(GOLDEN_DIR / golden, script)
    proc = cluster.run(["sbatch", "--test-only", str(script)], check=False)
    output = proc.stdout + proc.stderr
    returncode, texts = EXPECTED[golden]
    assert proc.returncode == returncode, output
    assert any(text in output for text in texts), output
    # --test-only submits nothing.
    assert cluster.run(["squeue", "-h", "-o", "%i"]).stdout == ""
