"""Resource requests as Slurm sees them, and the limits it enforces.

Each option is checked where Slurm records it (``scontrol show job``,
``sacct``) and, where the testbed can show it, by what Slurm then does:
``nproc`` inside the job, a job that waits for the licence, device or
feature another job holds, a kill at the time or memory limit.
"""

from __future__ import annotations

import dataclasses

from hpc_runner.core.resolve import resolve
from hpc_runner.core.result import JobStatus
from hpc_runner.core.spec import JobSpec
from hpc_runner.schedulers import get_scheduler
from tests.integration.cluster import Cluster
from tests.integration.helpers import history_entry, job_output
from tests.integration.slurm.slurm import (
    ALLOC_200M,
    ALLOCATED,
    TESTER_UID,
    TIME_LIMIT_KILL_TIMEOUT,
    exit_code_once_known,
    job_id,
    scontrol_job,
    submit,
    wait_rows,
    wait_sacct,
    wait_squeue_state,
)

#: How each version prints ``--gres=gpu:1`` in ``scontrol show job`` (24.11, 22.05).
ONE_GPU = {"gres/gpu:1", "gres:gpu:1"}


def test_cpu_mem_time_queue_and_project(cluster: Cluster) -> None:
    """cpu, mem, time, queue and project become ``--cpus-per-task`` with one task,
    ``--mem`` in MB, ``--time``, ``--partition`` and ``--account``; Slurm records
    each as asked and the job sees two CPUs."""
    spec = JobSpec("nproc", name="res", cpu=2, mem="300M", time="7m", queue="short", project="proj")
    resolved = resolve(spec, cluster.config(), cluster.host())
    script = cluster.scheduler().preview(resolved).splitlines()
    for line in (
        "#SBATCH --cpus-per-task=2 --ntasks=1",
        "#SBATCH --mem=300",
        "#SBATCH --time=0-00:07:00",
        "#SBATCH --partition=short",
        "#SBATCH --account=proj",
    ):
        assert line in script, line

    result = cluster.scheduler().submit(resolved)
    job = job_id(result)
    assert cluster.wait_for(job, JobStatus.COMPLETED) is JobStatus.COMPLETED
    fields = scontrol_job(cluster, job)
    assert (fields["NumCPUs"], fields["CPUs/Task"], fields["NumTasks"]) == ("2", "2", "1")
    assert fields["MinMemoryNode"] == "300M" and "MinMemoryCPU" not in fields
    # `short` allows ten minutes; seven is the job's own limit.
    assert fields["TimeLimit"] == "00:07:00"
    assert (fields["Partition"], fields["Account"]) == ("short", "proj")
    wait_sacct(
        cluster,
        job,
        "ReqCPUS,AllocCPUS,ReqMem,Timelimit,Partition,Account",
        ["2|2|300M|00:07:00|short|proj"],
        job_row_only=True,
    )
    info, extra = cluster.scheduler().get_job_details(job)
    assert (info.cpu, info.queue, extra["project"]) == (2, "short", "proj")
    # The testbed runs jobs in cgroups: the job has the CPUs it asked for and no more.
    assert job_output(cluster, result.read_stdout()) == "2\n"


def test_memory_per_cpu_divides_mem_by_the_cpus(cluster: Cluster) -> None:
    """``memory_per = "cpu"``: ``mem`` is still the whole job's. 300M on two CPUs is
    asked as ``--mem-per-cpu=150`` and Slurm books 300M."""
    slurm = {**cluster.config().schedulers.get("slurm", {}), "memory_per": "cpu"}
    config = dataclasses.replace(
        cluster.config(), schedulers={**cluster.config().schedulers, "slurm": slurm}
    )
    scheduler = get_scheduler("slurm", config=config)
    resolved = resolve(JobSpec("true", name="percpu", cpu=2, mem="300M"), config, cluster.host())
    script = scheduler.preview(resolved).splitlines()
    assert "#SBATCH --mem-per-cpu=150" in script
    assert not [line for line in script if "--mem=" in line]

    job = job_id(scheduler.submit(resolved))
    assert cluster.wait_for(job, JobStatus.COMPLETED) is JobStatus.COMPLETED
    fields = scontrol_job(cluster, job)
    assert fields["MinMemoryCPU"] == "150M" and "MinMemoryNode" not in fields
    wait_sacct(cluster, job, "ReqCPUS,ReqMem", ["2|300M"], job_row_only=True)


def test_licences_devices_and_features(cluster: Cluster) -> None:
    """licenses, devices and features become ``-L``, ``--gres`` and ``-C``, and Slurm
    acts on each: a job holding both licences, the one GPU and the one ``fast``
    node keeps a job that asks for any of them waiting, while a job that asks
    for none runs."""
    spec = JobSpec(
        "sleep 120",
        name="holder",
        cpu=2,
        licenses={"lic": 2},
        devices={"gpu": 1},
        features=["fast"],
    )
    resolved = resolve(spec, cluster.config(), cluster.host())
    script = cluster.scheduler().preview(resolved).splitlines()
    for line in ("#SBATCH -L lic:2", "#SBATCH --gres=gpu:1", "#SBATCH -C fast"):
        assert line in script, line
    holder = job_id(cluster.scheduler().submit(resolved))
    assert cluster.wait_for(holder, JobStatus.RUNNING) is JobStatus.RUNNING
    fields = scontrol_job(cluster, holder)
    assert fields["Licenses"] == "lic:2"
    assert fields["TresPerNode"] in ONE_GPU
    assert fields["Features"] == "fast"
    # exec1 is the only node with the feature and the GPU.
    assert fields["NodeList"] == "exec1"
    licences = cluster.run(["scontrol", "show", "licenses"]).stdout
    assert "LicenseName=lic" in licences and "Total=2 Used=2 Free=0" in licences

    # (the request, why squeue says it waits, where scontrol records it, as what)
    waiting: list[tuple[JobSpec, str, str, set[str]]] = [
        (JobSpec("true", name="wantlic", licenses={"lic": 1}), "Licenses", "Licenses", {"lic:1"}),
        (JobSpec("true", name="wantgpu", devices={"gpu": 1}), "Resources", "TresPerNode", ONE_GPU),
        (JobSpec("true", name="wantfast", features=["fast"]), "Resources", "Features", {"fast"}),
    ]
    for spec, reason, field, recorded in waiting:
        result = submit(cluster, spec)
        wait_squeue_state(cluster, job_id(result), f"PENDING|{reason}")
        assert scontrol_job(cluster, job_id(result))[field] in recorded, spec.name
        # 22.05's accounting has a new job up to a second after squeue does.
        assert cluster.wait_for(job_id(result), JobStatus.PENDING, timeout=10) is JobStatus.PENDING
        assert result.cancel()

    # exec2 was free all along: a job that asks for none of the three runs there.
    plain = job_id(submit(cluster, JobSpec("sleep 30", name="plain")))
    assert cluster.wait_for(plain, JobStatus.RUNNING) is JobStatus.RUNNING
    assert scontrol_job(cluster, plain)["NodeList"] == "exec2"


def test_time_limit_kill(cluster: Cluster) -> None:
    """Past its ``time`` the job is killed: TIMEOUT, exit code 143.

    ``sacct`` has two rows for it. The job's own says ``TIMEOUT`` and ``0:0``;
    only the ``.batch`` row has the signal. ``hpc status --history`` reads
    ``sacct`` alone and reports the same status and code.
    """
    spec = JobSpec("sleep 300", name="timelimit", time="1m")
    resolved = resolve(spec, cluster.config(), cluster.host())
    assert "#SBATCH --time=0-00:01:00" in cluster.scheduler().preview(resolved).splitlines()
    result = cluster.scheduler().submit(resolved)
    job = job_id(result)
    assert cluster.wait_for(job, JobStatus.RUNNING) is JobStatus.RUNNING
    status = cluster.wait_for(job, JobStatus.TIMEOUT, timeout=TIME_LIMIT_KILL_TIMEOUT)
    assert status is JobStatus.TIMEOUT
    wait_rows(cluster, job, [f"{job}|TIMEOUT|0:0", f"{job}.batch|CANCELLED|0:15"])
    assert exit_code_once_known(cluster, job) == 143
    assert result.wait(poll_interval=0.5, timeout=10) is JobStatus.TIMEOUT
    assert result.returncode == 143
    info, extra = cluster.scheduler().get_job_details(job)
    assert (info.status, info.exit_code, extra["state"]) == (JobStatus.TIMEOUT, 143, "TIMEOUT")
    done = history_entry(cluster, job)
    assert (done["status"], done["exit_code"]) == ("TIMEOUT", 143)


def test_cancelled_while_running(cluster: Cluster) -> None:
    """A running job cancelled by its owner: CANCELLED, exit code 143.

    Two ``sacct`` rows again: ``CANCELLED by <uid>`` and ``0:0`` on the job's
    own, the signal on the ``.batch`` row.
    """
    result = submit(cluster, JobSpec("sleep 120", name="cancelrun"))
    job = job_id(result)
    assert cluster.wait_for(job, JobStatus.RUNNING) is JobStatus.RUNNING
    assert result.cancel()
    assert cluster.wait_for(job, JobStatus.CANCELLED, timeout=30) is JobStatus.CANCELLED
    wait_rows(cluster, job, [f"{job}|CANCELLED by {TESTER_UID}|0:0", f"{job}.batch|CANCELLED|0:15"])
    assert exit_code_once_known(cluster, job) == 143
    assert result.wait(poll_interval=0.5, timeout=10) is JobStatus.CANCELLED
    assert result.returncode == 143
    done = history_entry(cluster, job)
    assert (done["status"], done["exit_code"]) == ("CANCELLED", 143)


def test_over_its_memory_is_killed(cluster: Cluster) -> None:
    """A 200 MB allocation under ``mem = "50M"``: Slurm kills it. KILLED, with
    Slurm's own ``OUT_OF_MEMORY`` in the details."""
    spec = JobSpec(ALLOC_200M, name="overmem", mem="50M")
    resolved = resolve(spec, cluster.config(), cluster.host())
    assert "#SBATCH --mem=50" in cluster.scheduler().preview(resolved).splitlines()
    result = cluster.scheduler().submit(resolved)
    job = job_id(result)
    assert cluster.wait_for(job, JobStatus.KILLED) is JobStatus.KILLED
    wait_rows(cluster, job, [f"{job}|OUT_OF_MEMORY|0:125", f"{job}.batch|OUT_OF_MEMORY|0:125"])
    info, extra = cluster.scheduler().get_job_details(job)
    assert (info.status, extra["state"]) == (JobStatus.KILLED, "OUT_OF_MEMORY")
    # Slurm's 0:125, read as a shell would report a death by signal 125.
    assert exit_code_once_known(cluster, job) == 253
    # The allocation never finished.
    assert ALLOCATED not in (result.read_stdout() or "").split()
    done = history_entry(cluster, job)
    assert (done["status"], done["exit_code"]) == ("KILLED", 253)


def test_within_its_memory_runs(cluster: Cluster) -> None:
    """The same allocation under ``mem = "400M"`` completes: the kill above is the limit's."""
    result = submit(cluster, JobSpec(ALLOC_200M, name="inmem", mem="400M"))
    assert cluster.wait_for(job_id(result), JobStatus.COMPLETED) is JobStatus.COMPLETED
    assert job_output(cluster, result.read_stdout()) == f"{ALLOCATED}\n"
