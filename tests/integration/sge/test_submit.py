"""Submission where SGE's own arguments and words show."""

from __future__ import annotations

import pytest

from hpc_runner.core.exceptions import SubmissionError
from hpc_runner.core.job import Job
from hpc_runner.core.resolve import resolve
from hpc_runner.core.result import JobResult
from hpc_runner.core.spec import JobSpec
from tests.integration.cluster import Cluster


def _job(cluster: Cluster, spec: JobSpec) -> Job:
    return resolve(spec, cluster.config(), cluster.host())


def _submit(cluster: Cluster, spec: JobSpec) -> JobResult:
    return cluster.scheduler().submit(_job(cluster, spec))


def _job_id(result: JobResult) -> str:
    assert result.job_id is not None
    return result.job_id


def test_passthrough(cluster: Cluster) -> None:
    """B1: scheduler_args land as one ``#$`` line and qstat -j shows the hard resource."""
    spec = JobSpec("sleep 10", name="passthrough", scheduler_args=["-l", "gpu=1"])
    job = _job(cluster, spec)
    directives = [line for line in cluster.scheduler().preview(job).splitlines() if "gpu" in line]
    assert directives == ["#$ -l gpu=1"]

    result = cluster.scheduler().submit(job)
    job_id = _job_id(result)
    _, details = cluster.scheduler().get_job_details(job_id)
    assert details.get("resources") == {"gpu": "1"}
    raw = cluster.run(["qstat", "-j", job_id]).stdout
    assert "gpu=1" in raw


def test_rejected_queue_carries_qsubs_words(cluster: Cluster) -> None:
    """D13: the SubmissionError for an unknown queue carries qsub's reason text."""
    with pytest.raises(SubmissionError) as excinfo:
        _submit(cluster, JobSpec("true", name="badq", queue="nope.q"))
    assert 'unknown queue "nope.q"' in str(excinfo.value)


def test_rejected_resource(cluster: Cluster) -> None:
    """D13: an unknown resource is refused by qsub and surfaces as SubmissionError.

    Note: an oversubscribed consumable (``-l gpu=2`` against complex_values
    gpu=1) is *accepted* at submit and queues forever; SGE only verifies
    satisfiability with ``qsub -w e``, which hpc-runner does not pass
    (docs/dev/testbed/TESTBED_REVIEW.md D-T27).
    """
    with pytest.raises(SubmissionError) as excinfo:
        _submit(cluster, JobSpec("true", name="badres", scheduler_args=["-l", "nosuch=1"]))
    assert "nosuch" in excinfo.value.stderr
