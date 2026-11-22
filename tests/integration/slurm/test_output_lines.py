"""The lines a cluster adds to a job's output are exactly the ones the cluster class lists.

The generic tests drop ``cluster.extra_output_lines`` from the end of a
job's output before comparing it (``job_output`` in tests/integration/helpers.py).
On the cluster that adds lines (the 22.05 testbed, testbed/slurm/README.md)
this checks the list against a real job, so a pattern cannot stop matching,
or a fourth line appear, unnoticed.
"""

from __future__ import annotations

import re

import pytest

from hpc_runner.core.result import JobStatus
from hpc_runner.core.spec import JobSpec
from tests.integration.cluster import Cluster
from tests.integration.slurm.slurm import job_id, submit


def test_a_batch_jobs_error_stream_ends_with_exactly_the_listed_lines(cluster: Cluster) -> None:
    patterns = cluster.extra_output_lines
    if not patterns:
        pytest.skip("this cluster adds no lines to a job's output")
    result = submit(cluster, JobSpec("echo hello", name="lines", stderr="lines.err"))
    assert cluster.wait_for(job_id(result), JobStatus.COMPLETED) is JobStatus.COMPLETED
    # They go to the error stream: with a file of its own, the output is the job's alone.
    assert result.read_stdout() == "hello\n"
    assert result.stderr_path == cluster.workdir / "lines.err"
    lines = result.stderr_path.read_text().splitlines()
    assert len(lines) == len(patterns), lines
    for pattern, line in zip(patterns, lines):
        assert re.fullmatch(pattern, line), (pattern, line)
