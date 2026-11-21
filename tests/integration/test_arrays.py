"""Array jobs: the array's status, per-task statuses and per-task output files.

See docs/dev/testbed/TESTBED_PLAN.md section 5.
"""

from __future__ import annotations

from hpc_runner.core.resolve import resolve
from hpc_runner.core.result import ArrayJobResult, JobStatus
from hpc_runner.core.spec import JobSpec
from tests.integration.cluster import Cluster


def _task3_fails(cluster: Cluster) -> str:
    """A command that prints its task id, then fails only as task 3."""
    var = cluster.array_task_var
    return f'echo "task ${var}"; [ "${var}" != 3 ]'


def test_array_1_to_5(cluster: Cluster) -> None:
    """D9: wait() is the array's status, task_statuses() the drill-down, each task
    owns an output file, only task 3 FAILED; the base id answers for the whole."""
    spec = JobSpec(_task3_fails(cluster), name="arr", array="1-5")
    job = resolve(spec, cluster.config(), cluster.host())
    result = cluster.scheduler().submit(job)
    assert isinstance(result, ArrayJobResult)
    job_id = result.job_id
    assert job_id is not None and job_id.isdigit()
    assert result.task_ids() == [cluster.array_task_id(job_id, n) for n in range(1, 6)]

    assert result.wait(poll_interval=0.5, timeout=60) is JobStatus.FAILED
    tasks = result.task_statuses()
    assert tasks.tasks == {
        1: JobStatus.COMPLETED,
        2: JobStatus.COMPLETED,
        3: JobStatus.FAILED,
        4: JobStatus.COMPLETED,
        5: JobStatus.COMPLETED,
    }
    assert tasks.overall is JobStatus.FAILED and tasks.failed == [3] and tasks.is_terminal
    assert result.returncode == 1
    # The base id, as `hpc status <id>` or scheduler.result(id) asks: the whole
    # array, whatever order the accounting lists the task records in. With no
    # range it reduces the records the accounting has (D19), all five of them
    # since the handle above saw every task's; wait in case they are listed a
    # moment later (CI 41356547233 and 41530594728 lost this race).
    cluster.wait_accounted(job_id, records=5)
    assert cluster.scheduler().get_status(job_id) is JobStatus.FAILED
    assert cluster.scheduler().get_exit_code(job_id) == 1
    info, extra = cluster.scheduler().get_job_details(job_id)
    assert (info.status, info.exit_code) == (JobStatus.FAILED, 1)
    per_task = extra["tasks"]
    assert isinstance(per_task, list)
    assert [(t.array_task_id, t.exit_code) for t in per_task] == [
        (1, 0),
        (2, 0),
        (3, 1),
        (4, 0),
        (5, 0),
    ]
    for index in range(1, 6):
        assert result.task_status(index) is tasks.tasks[index]
        path = result.task_stdout_path(index)
        assert path is not None and path.exists(), index
        assert path.parent == cluster.workdir
        assert path.read_text().strip() == f"task {index}"
        assert result.read_task_stdout(index) == path.read_text()
    assert cluster.scheduler().get_exit_code(result.task_id(3)) == 1
    assert cluster.scheduler().get_exit_code(result.task_id(1)) == 0
    assert len({result.task_stdout_path(n) for n in range(1, 6)}) == 5


def test_array_concurrency_limit_is_accepted(cluster: Cluster) -> None:
    """An array with a ``%MAX`` concurrency limit is accepted and every task runs."""
    spec = JobSpec(f'echo "task ${cluster.array_task_var}"', name="arrtc", array="1-6%2")
    job = resolve(spec, cluster.config(), cluster.host())
    result = cluster.scheduler().submit(job)
    assert isinstance(result, ArrayJobResult)
    assert result.wait(poll_interval=0.5, timeout=90) is JobStatus.COMPLETED
    assert result.task_statuses().tasks == dict.fromkeys(range(1, 7), JobStatus.COMPLETED)
    assert cluster.scheduler().get_status(result.job_id or "") is JobStatus.COMPLETED
