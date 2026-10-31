"""Array jobs: per-task statuses and per-task output files.

See docs/dev/testbed/TESTBED_PLAN.md section 5.
"""

from __future__ import annotations

from hpc_runner.core.resolve import resolve
from hpc_runner.core.result import ArrayJobResult, JobStatus
from hpc_runner.core.spec import JobSpec
from tests.integration.cluster import Cluster

#: Prints its task id, then fails only as task 3.
TASK3_FAILS = 'echo "task $SGE_TASK_ID"; [ "$SGE_TASK_ID" != 3 ]'


def test_array_1_to_5(cluster: Cluster) -> None:
    """D9: wait() gives per-task statuses, each task owns an output file, only task 3 FAILED."""
    job = resolve(JobSpec(TASK3_FAILS, name="arr", array="1-5"), cluster.config(), cluster.host())
    result = cluster.scheduler().submit(job)
    assert isinstance(result, ArrayJobResult)
    job_id = result.job_id
    assert job_id is not None and job_id.isdigit()
    assert result.task_ids() == [f"{job_id}.{n}" for n in range(1, 6)]

    statuses = result.wait(poll_interval=0.5, timeout=60)
    assert statuses == {
        1: JobStatus.COMPLETED,
        2: JobStatus.COMPLETED,
        3: JobStatus.FAILED,
        4: JobStatus.COMPLETED,
        5: JobStatus.COMPLETED,
    }
    for index in range(1, 6):
        assert result.task_status(index) is statuses[index]
        path = result.task_stdout_path(index)
        assert path is not None and path.exists(), index
        assert path.parent == cluster.workdir
        assert path.read_text().strip() == f"task {index}"
        assert result.read_task_stdout(index) == path.read_text()
    assert cluster.scheduler().get_exit_code(result.task_id(3)) == 1
    assert cluster.scheduler().get_exit_code(result.task_id(1)) == 0
    assert len({result.task_stdout_path(n) for n in range(1, 6)}) == 5


def test_array_concurrency_limit_is_accepted(cluster: Cluster) -> None:
    """``%MAX`` renders as SGE's ``-tc``; qsub accepts it and every task runs."""
    spec = JobSpec('echo "task $SGE_TASK_ID"', name="arrtc", array="1-6%2")
    job = resolve(spec, cluster.config(), cluster.host())
    script = cluster.scheduler().preview(job)
    assert "#$ -t 1-6\n" in script
    assert "#$ -tc 2\n" in script
    result = cluster.scheduler().submit(job)
    assert isinstance(result, ArrayJobResult)
    statuses = result.wait(poll_interval=0.5, timeout=90)
    assert statuses == dict.fromkeys(range(1, 7), JobStatus.COMPLETED)
