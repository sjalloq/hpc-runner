"""Every SGE batch golden script through ``qsub -w v`` on the real scheduler.

The goldens (tests/unit/golden/sge_batch_*.sh) pin what hpc-runner renders;
this pins that SGE *reads* it. ``-w v`` parses the ``#$`` directives and
checks the requests against the cluster without submitting anything. The
goldens ask for site things the testbed does not have (a 4-slot PE on 2-slot
hosts, a ``scratch`` resource, ``batch.q``), so each has an expected verdict;
what must never appear is a directive the parser rejects ("Unable to read
script file", as ``-t 1-10:2%3`` once produced: test QA review 2.1).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.integration.cluster import SRC_ROOT, Cluster

GOLDEN_DIR = SRC_ROOT / "tests" / "unit" / "golden"
VERIFIED = "verification: found suitable queue(s)"
#: What qsub says when it cannot parse a directive; a failure whatever the golden.
PARSE_ERROR = "Unable to read script file"

#: golden -> None (must verify) or the site-dependent rejection it must get instead.
EXPECTED: dict[str, str | None] = {
    "sge_batch_minimal.sh": None,
    "sge_batch_array_with_dependencies.sh": None,
    # -pe smp 4 on 2-slot hosts, -q batch.q, -l h_rt=4:00:00 against short.q.
    "sge_batch_full_environment.sh": "verification: no suitable queues",
    "sge_batch_passthrough_resources_project.sh": 'unknown resource "scratch"',
}


def test_every_sge_batch_golden_is_covered() -> None:
    assert sorted(p.name for p in GOLDEN_DIR.glob("sge_batch_*.sh")) == sorted(EXPECTED)


@pytest.mark.parametrize("golden", sorted(EXPECTED))
def test_qsub_verifies_the_golden(cluster: Cluster, golden: str) -> None:
    script = cluster.workdir / golden
    shutil.copy(GOLDEN_DIR / golden, script)
    proc = cluster.run(["qsub", "-w", "v", str(script)], check=False)
    output = proc.stdout + proc.stderr
    assert PARSE_ERROR not in output, output
    expected = EXPECTED[golden]
    if expected is None:
        assert proc.returncode == 0 and VERIFIED in output, output
    else:
        assert proc.returncode != 0 and expected in output, output
    # Verify mode submits nothing.
    assert Path(script).name not in cluster.run(["qstat", "-u", "*"]).stdout
