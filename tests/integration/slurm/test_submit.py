"""Submission where Slurm's own arguments and words show."""

from __future__ import annotations

import pytest

from hpc_runner.core.exceptions import SubmissionError
from hpc_runner.core.resolve import resolve
from hpc_runner.core.spec import JobSpec
from tests.integration.cluster import Cluster
from tests.integration.slurm.slurm import job_id, scontrol_job, submit


def test_passthrough(cluster: Cluster) -> None:
    """scheduler_args land together on one ``#SBATCH`` line, untouched, and
    ``scontrol`` shows both took effect."""
    spec = JobSpec(
        "sleep 10", name="passthrough", scheduler_args=["--nice=500", "--comment=fromtest"]
    )
    job = resolve(spec, cluster.config(), cluster.host())
    script = cluster.scheduler().preview(job).splitlines()
    assert [line for line in script if "nice" in line or "comment" in line] == [
        "#SBATCH --nice=500 --comment=fromtest"
    ]

    fields = scontrol_job(cluster, job_id(cluster.scheduler().submit(job)))
    assert (fields["Nice"], fields["Comment"]) == ("500", "fromtest")


def test_rejected_queue_carries_sbatchs_words(cluster: Cluster) -> None:
    """The SubmissionError for an unknown queue carries sbatch's own reason."""
    with pytest.raises(SubmissionError) as excinfo:
        submit(cluster, JobSpec("true", name="badq", queue="nope.q"))
    assert "sbatch: error: invalid partition specified: nope.q" in str(excinfo.value)
    assert "Invalid partition name specified" in excinfo.value.stderr


#: A request the testbed cannot meet -> what sbatch says. `other` is an account
#: with no user; the cluster has two `lic`, one GPU, two CPUs and 2000 MB a node.
REJECTED: dict[str, tuple[JobSpec, str]] = {
    "project not the user's": (
        JobSpec("true", project="other"),
        "Invalid account or account/partition combination specified",
    ),
    "licence unknown": (JobSpec("true", licenses={"nosuch": 1}), "Invalid license specification"),
    "licences, more than exist": (
        JobSpec("true", licenses={"lic": 3}),
        "Invalid license specification",
    ),
    "device unknown": (
        JobSpec("true", devices={"nosuch": 1}),
        "Invalid generic resource (gres) specification",
    ),
    "devices, more than a node has": (
        JobSpec("true", devices={"gpu": 2}),
        "Requested node configuration is not available",
    ),
    "feature unknown": (JobSpec("true", features=["nosuch"]), "Invalid feature specification"),
    "cpu, more than a node has": (
        JobSpec("true", cpu=4),
        "CPU count per node can not be satisfied",
    ),
    "mem, more than a node has": (
        JobSpec("true", mem="4G"),
        "Memory specification can not be satisfied",
    ),
}


@pytest.mark.parametrize("request_", sorted(REJECTED))
def test_rejected_request(cluster: Cluster, request_: str) -> None:
    """Each option reaches Slurm as that kind of request: one Slurm cannot meet
    is refused at submission, in the words Slurm has for that kind, and nothing
    is queued."""
    spec, words = REJECTED[request_]
    with pytest.raises(SubmissionError) as excinfo:
        submit(cluster, spec)
    assert words in excinfo.value.stderr
    assert cluster.scheduler().list_active_jobs() == []
