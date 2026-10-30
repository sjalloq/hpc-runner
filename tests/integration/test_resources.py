"""Resource requests as SGE sees them, and the limits it enforces.

See docs/dev/testbed/TESTBED_PLAN.md section 5.
"""

from __future__ import annotations

import pytest

from hpc_runner.core.resolve import resolve
from hpc_runner.core.result import JobResult, JobStatus
from hpc_runner.core.spec import JobSpec
from tests.integration.cluster import Cluster

#: ``short.q`` has ``h_rt 00:01:00``; the kill arrives about 60 s after start.
H_RT_KILL_TIMEOUT = 150.0
#: Allocate 200 MB under a 50 MB ``h_vmem`` limit.
ALLOC_200M = 'python3 -c "b = bytearray(200 * 1024 * 1024); print(len(b))"'


def _submit(cluster: Cluster, spec: JobSpec) -> JobResult:
    return cluster.scheduler().submit(resolve(spec, cluster.config(), cluster.host()))


def _job_id(result: JobResult) -> str:
    assert result.job_id is not None
    return result.job_id


def test_pe_mem_time(cluster: Cluster) -> None:
    """D10: cpu/mem/time become -pe smp 2, mem_free (per job, as given) and h_rt."""
    spec = JobSpec("true", name="res", cpu=2, mem="2G", time="10m", scheduler_args={"sge": ["-h"]})
    job = resolve(spec, cluster.config(), cluster.host())
    preview = cluster.scheduler().preview(job)
    assert "#$ -pe smp 2" in preview.splitlines()
    assert "#$ -l mem_free=2G" in preview.splitlines()
    assert "#$ -l h_rt=0:10:00" in preview.splitlines()

    job_id = _job_id(cluster.scheduler().submit(job))
    assert cluster.wait_for(job_id, JobStatus.PENDING, timeout=5) is JobStatus.PENDING
    info, details = cluster.scheduler().get_job_details(job_id)
    assert info.job_id == job_id
    assert details.get("pe_name") == "smp"
    assert details.get("pe_range") == "2"
    # SGE canonicalises a TIME request to seconds, so 0:10:00 reads back as 600.
    assert details.get("resources") == {"mem_free": "2G", "h_rt": "600"}
    raw = cluster.run(["qstat", "-j", job_id]).stdout
    assert "mem_free=2G" in raw and "h_rt=600" in raw and "smp" in raw


def test_h_rt_kill(cluster: Cluster) -> None:
    """D-T30: past short.q's h_rt qmaster kills the job: TIMEOUT, exit 137, qacct ``failed 37``."""
    job_id = _job_id(_submit(cluster, JobSpec("sleep 120", name="hrt", queue="short.q")))
    assert cluster.wait_for(job_id, JobStatus.RUNNING) is JobStatus.RUNNING
    status = cluster.wait_for(job_id, {JobStatus.TIMEOUT}, timeout=H_RT_KILL_TIMEOUT)
    assert status is JobStatus.TIMEOUT
    record = cluster.run(["qacct", "-j", job_id]).stdout
    failed = [line.split() for line in record.splitlines() if line.startswith("failed")]
    assert failed and failed[0][1] == "37", record
    # The exit code of a signal death is pinned by test_signal_exit_code (BUG-T5).


@pytest.mark.xfail(
    strict=True,
    reason="BUG-T5: qacct writes 'exit_status 137 (Killed)'; get_exit_code int()s it and gets None",
)
def test_signal_exit_code(cluster: Cluster) -> None:
    """A signal-killed job (here qdel, same as an h_rt kill) reports exit code 137."""
    result = _submit(cluster, JobSpec("sleep 60", name="sigexit"))
    job_id = _job_id(result)
    assert cluster.wait_for(job_id, JobStatus.RUNNING) is JobStatus.RUNNING
    assert result.cancel()
    status = cluster.wait_for(job_id, {JobStatus.CANCELLED, JobStatus.FAILED}, timeout=30)
    assert status in {JobStatus.CANCELLED, JobStatus.FAILED}
    assert cluster.scheduler().get_exit_code(job_id) == 137


def test_h_vmem(cluster: Cluster) -> None:
    """A 200 MB allocation under ``-l h_vmem=50M`` fails (MemoryError or a kill): FAILED."""
    spec = JobSpec(ALLOC_200M, name="vmem", resources=[{"name": "h_vmem", "value": "50M"}])
    job = resolve(spec, cluster.config(), cluster.host())
    assert "#$ -l h_vmem=50M" in cluster.scheduler().preview(job).splitlines()
    result = cluster.scheduler().submit(job)
    job_id = _job_id(result)
    assert cluster.wait_for(job_id, JobStatus.FAILED) is JobStatus.FAILED
    exit_code = cluster.scheduler().get_exit_code(job_id)
    assert exit_code is not None and exit_code != 0
    assert (result.read_stdout() or "").strip() != str(200 * 1024 * 1024)


def test_queue_selection(cluster: Cluster) -> None:
    """queue="short.q" runs in short.q on an exec node; list_active_jobs reports queue and node."""
    job_id = _job_id(_submit(cluster, JobSpec("sleep 30", name="shortq", queue="short.q")))
    assert cluster.wait_for(job_id, JobStatus.RUNNING) is JobStatus.RUNNING
    jobs = {j.job_id: j for j in cluster.scheduler().list_active_jobs()}
    assert job_id in jobs, jobs
    info = jobs[job_id]
    # qstat -xml reports the instance "short.q@execN"; JobInfo splits it into queue and node.
    assert info.queue == "short.q", info
    assert info.node is not None and info.node.startswith("exec"), info
    assert info.status is JobStatus.RUNNING
    assert info.user == "tester"
