"""Parsers for Slurm command output.

``sacct`` and ``squeue`` are asked for pipe-delimited text with an explicit
field list, the job name last (:data:`SACCT_FIELDS`, :data:`SQUEUE_FIELDS`);
``scontrol show job`` prints ``Key=Value`` records.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from hpc_runner.core.job_info import JobInfo
from hpc_runner.core.result import JobStatus, reduce_statuses

_SUBMITTED = re.compile(r"^Submitted batch job (\d+)", re.MULTILINE)

#: The ``sacct`` columns asked for, in order.
SACCT_FIELDS: tuple[str, ...] = (
    "JobID",
    "JobIDRaw",
    "User",
    "Account",
    "Partition",
    "State",
    "ExitCode",
    "Reason",
    "Submit",
    "Start",
    "End",
    "Elapsed",
    "Timelimit",
    "NodeList",
    "AllocCPUS",
    "ReqCPUS",
    "ReqMem",
    "MaxRSS",
    "ReqTRES",
    "AllocTRES",
    "WorkDir",
    "JobName",
)
SACCT_FORMAT = ",".join(SACCT_FIELDS)

#: The ``squeue`` columns asked for, in order: (format code, name).
SQUEUE_FIELDS: tuple[tuple[str, str], ...] = (
    ("%i", "JobID"),
    ("%F", "ArrayJobID"),
    ("%K", "ArrayTaskID"),
    ("%u", "User"),
    ("%a", "Account"),
    ("%P", "Partition"),
    ("%T", "State"),
    ("%r", "Reason"),
    ("%V", "SubmitTime"),
    ("%S", "StartTime"),
    ("%e", "EndTime"),
    ("%M", "TimeUsed"),
    ("%l", "TimeLimit"),
    ("%C", "CPUs"),
    ("%m", "MinMemory"),
    ("%N", "NodeList"),
    ("%E", "Dependency"),
    ("%b", "Gres"),
    ("%Z", "WorkDir"),
    ("%j", "JobName"),
)
SQUEUE_FORMAT = "|".join(code for code, _ in SQUEUE_FIELDS)

Row = dict[str, str]


def parse_sbatch_output(output: str) -> str | None:
    """The job id in ``sbatch``'s ``Submitted batch job <id>`` line, or None.

    An array job is submitted under one id, printed the same way.
    """
    match = _SUBMITTED.search(output)
    return match.group(1) if match else None


# =============================================================================
# States, exit codes, times
# =============================================================================

#: Slurm's job states by their long names, as ``sacct``, ``squeue -o %T``
#: and ``scontrol show job`` print them. ``CANCELLED`` is not here: see
#: :func:`state_to_status`.
_STATES: dict[str, JobStatus] = {
    "PENDING": JobStatus.PENDING,
    "REQUEUED": JobStatus.PENDING,
    "REQUEUE_HOLD": JobStatus.PENDING,
    "REQUEUE_FED": JobStatus.PENDING,
    "RESV_DEL_HOLD": JobStatus.PENDING,
    "RUNNING": JobStatus.RUNNING,
    "CONFIGURING": JobStatus.RUNNING,
    "COMPLETING": JobStatus.RUNNING,
    "SIGNALING": JobStatus.RUNNING,
    "STAGE_OUT": JobStatus.RUNNING,
    "RESIZING": JobStatus.RUNNING,
    "SUSPENDED": JobStatus.SUSPENDED,
    "STOPPED": JobStatus.SUSPENDED,
    "COMPLETED": JobStatus.COMPLETED,
    "FAILED": JobStatus.FAILED,
    "NODE_FAIL": JobStatus.FAILED,
    "BOOT_FAIL": JobStatus.FAILED,
    "SPECIAL_EXIT": JobStatus.FAILED,
    "PREEMPTED": JobStatus.CANCELLED,
    "REVOKED": JobStatus.CANCELLED,
    "TIMEOUT": JobStatus.TIMEOUT,
    "DEADLINE": JobStatus.TIMEOUT,
    "OUT_OF_MEMORY": JobStatus.KILLED,
}


def state_to_status(state: str) -> JobStatus:
    """The hpc-runner status for a ``sacct`` State.

    ``CANCELLED by <uid>`` is CANCELLED: somebody cancelled the job. A
    plain ``CANCELLED`` on a job's own row is a job Slurm cancelled because
    its dependency can never be satisfied, which is SKIPPED. A state not in
    the table is UNKNOWN.
    """
    words = state.split()
    if not words:
        return JobStatus.UNKNOWN
    if words[0] == "CANCELLED":
        return JobStatus.CANCELLED if len(words) > 1 else JobStatus.SKIPPED
    return _STATES.get(words[0], JobStatus.UNKNOWN)


def parse_exit_code(text: str) -> int | None:
    """What a shell would report for Slurm's ``ExitCode`` ``N:S``.

    ``N`` when the signal ``S`` is 0, otherwise ``128 + S``. None for an
    empty field or one that is not two numbers.
    """
    code, sep, signal = text.strip().partition(":")
    if not sep or not code.isdigit() or not signal.isdigit():
        return None
    return int(code) if int(signal) == 0 else 128 + int(signal)


def parse_time(text: str) -> datetime | None:
    """A ``YYYY-MM-DDTHH:MM:SS`` time; None for ``Unknown``, ``None``, ``N/A`` or empty."""
    try:
        return datetime.strptime(text.strip(), "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


_DURATION = re.compile(r"(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)")


def parse_duration(text: str) -> timedelta | None:
    """A ``[D-][H:]M:S`` duration (``0:04``, ``00:01:03``); None for anything else."""
    match = _DURATION.fullmatch(text.strip())
    if match is None:
        return None
    days, hours, minutes, seconds = (int(part) if part else 0 for part in match.groups())
    return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)


# =============================================================================
# Array task ids
# =============================================================================

_TASK_RANGE = re.compile(r"(\d+)(?:-(\d+)(?::(\d+))?)?")


def expand_tasks(spec: str) -> tuple[int, ...]:
    """The task indices in the task part of an array id.

    A started task has its own index (``3``). Tasks not started yet share
    one row, written as a bracketed list of indices and ranges with an
    optional step and an optional ``%<throttle>``: ``[1-4]``, ``[3-4%2]``,
    ``[1,3,7,9]``, ``[1-100:3]``.
    """
    spec = spec.strip("[]").partition("%")[0]
    indices: list[int] = []
    for part in spec.split(","):
        match = _TASK_RANGE.fullmatch(part)
        if match is None:
            continue
        start, end, step = match.groups()
        last = int(end) if end is not None else int(start)
        indices.extend(range(int(start), last + 1, int(step) if step else 1))
    return tuple(indices)


def split_job_id(job_id: str) -> tuple[str, str | None]:
    """``(base id, task part)`` of a job id; the task part is None for a plain id."""
    base, sep, task = job_id.partition("_")
    return base, (task if sep else None)


# =============================================================================
# sacct
# =============================================================================


def _rows(output: str, names: tuple[str, ...]) -> list[Row]:
    """Each pipe-delimited line of *output* as ``{name: value}``.

    The last column is the job name, so a ``|`` in a name stays in it. A
    line with fewer columns than asked for is not a row and is dropped.
    """
    rows: list[Row] = []
    for line in output.splitlines():
        values = line.split("|", len(names) - 1)
        if len(values) == len(names):
            rows.append(dict(zip(names, values)))
    return rows


@dataclass(frozen=True)
class SacctJob:
    """One job, or one array task, or the not-yet-started tasks of an array.

    ``row`` is its own ``sacct`` row and ``batch`` its ``.batch`` row, if it
    has one. ``tasks`` is empty for a job that is not an array, one index
    for a started task and several for the row the waiting tasks share.
    """

    row: Row
    batch: Row | None
    base: str
    tasks: tuple[int, ...]

    @property
    def job_id(self) -> str:
        """The id as ``sacct`` prints it (``77``, ``83_1``, ``96_[3-4%2]``)."""
        return self.row["JobID"]

    @property
    def state(self) -> str:
        """Slurm's own state, as printed (``OUT_OF_MEMORY``, ``CANCELLED by 1003``)."""
        return self.row["State"]

    @property
    def status(self) -> JobStatus:
        return state_to_status(self.state)

    @property
    def exit_code(self) -> int | None:
        """The exit code these rows give, None when they give none.

        From the job's own row; from the ``.batch`` row when the job's own
        says ``0:0`` and the job did not complete (a job that timed out or
        was cancelled while running has the signal only there). Never 0
        unless the job is COMPLETED, and None for a job that has not ended
        or in which nothing ran.

        ``sacct`` prints an exit status above 128 less 128, so a script
        that exited 137 gives 9 here.
        """
        status = self.status
        if not status.is_terminal:
            return None
        code = parse_exit_code(self.row["ExitCode"])
        if code == 0 and status is not JobStatus.COMPLETED:
            code = parse_exit_code(self.batch["ExitCode"]) if self.batch else None
        if code == 0 and status is not JobStatus.COMPLETED:
            return None
        return code


def parse_sacct(output: str) -> list[SacctJob]:
    """The jobs and array tasks in ``sacct -P -n --format=SACCT_FORMAT`` output.

    Without ``-X`` a job that started has a ``<id>.batch`` row after its
    own; rows of other steps (``<id>.0``) are not read.
    """
    rows = _rows(output, SACCT_FIELDS)
    batch = {
        row["JobID"].removesuffix(".batch"): row for row in rows if row["JobID"].endswith(".batch")
    }
    jobs: list[SacctJob] = []
    for row in rows:
        job_id = row["JobID"]
        if "." in job_id:
            continue
        base, task = split_job_id(job_id)
        tasks = expand_tasks(task) if task is not None else ()
        jobs.append(SacctJob(row=row, batch=batch.get(job_id), base=base, tasks=tasks))
    return jobs


def select_jobs(jobs: list[SacctJob], job_id: str) -> list[SacctJob]:
    """The entries of *jobs* behind *job_id*.

    A plain id is the job of that id, or every row of the array of that
    id. A task id (``<base>_<index>``) is that task's own row, or the row
    the waiting tasks share when the index is one of them.
    """
    base, task = split_job_id(job_id)
    if task is None:
        return [job for job in jobs if job.base == base]
    if not task.isdigit():
        return [job for job in jobs if job.job_id == job_id]
    return [job for job in jobs if job.base == base and int(task) in job.tasks]


def sacct_status(jobs: list[SacctJob], job_id: str) -> JobStatus:
    """The status of *job_id* from *jobs*: UNKNOWN when it has no row.

    The base id of an array is the reduction of its rows.
    """
    selected = select_jobs(jobs, job_id)
    if not selected:
        return JobStatus.UNKNOWN
    return reduce_statuses(job.status for job in selected)


def _cpus(row: Row) -> int | None:
    for name in ("AllocCPUS", "ReqCPUS"):
        if row[name].isdigit() and int(row[name]) > 0:
            return int(row[name])
    return None


def sacct_to_job_info(job: SacctJob, task: int | None = None) -> JobInfo:
    """A :class:`JobInfo` from a job's ``sacct`` rows.

    *task* names one of the tasks of a row that several waiting tasks
    share; the info is then that task's (``<base>_<task>``).

    * ``JobID`` -> ``job_id``; ``JobName`` -> ``name``; ``User`` -> ``user``
    * ``State`` -> ``status``; ``ExitCode`` -> ``exit_code`` (:attr:`SacctJob.exit_code`)
    * ``Partition`` -> ``queue``; ``NodeList`` -> ``node`` (not ``None assigned``)
    * ``AllocCPUS``, else ``ReqCPUS`` -> ``cpu`` (0 is not a count)
    * ``Submit``, ``Start``, ``End`` -> the three times; ``Elapsed`` ->
      ``runtime`` for a job that started
    * ``WorkDir`` -> ``working_dir``
    """
    row = job.row
    if task is None and len(job.tasks) == 1:
        task = job.tasks[0]
    start = parse_time(row["Start"])
    node = row["NodeList"]
    return JobInfo(
        job_id=f"{job.base}_{task}" if task is not None else job.job_id,
        name=row["JobName"],
        user=row["User"],
        status=job.status,
        queue=row["Partition"] or None,
        submit_time=parse_time(row["Submit"]),
        start_time=start,
        end_time=parse_time(row["End"]),
        runtime=parse_duration(row["Elapsed"]) if start is not None else None,
        cpu=_cpus(row),
        exit_code=job.exit_code,
        node=node if node and node != "None assigned" else None,
        working_dir=Path(row["WorkDir"]) if row["WorkDir"] else None,
        array_task_id=task,
    )


# =============================================================================
# squeue
# =============================================================================

_SQUEUE_NAMES = tuple(name for _, name in SQUEUE_FIELDS)
_STARTED = (JobStatus.RUNNING, JobStatus.SUSPENDED)


def parse_squeue(output: str) -> list[JobInfo]:
    """The live jobs in ``squeue -h -o SQUEUE_FORMAT`` output, one per job id.

    An array is one entry under the id it was submitted with, however many
    lines ``squeue`` prints for it (one per started task, one for the
    tasks still waiting). Its status is the reduction of those lines and
    the rest comes from the first line in that status.

    ``StartTime`` of a job that is not running is when Slurm expects to
    start it, so the start time and the time used are read for a running
    or suspended job only.
    """
    groups: dict[str, list[Row]] = {}
    for row in _rows(output, _SQUEUE_NAMES):
        groups.setdefault(row["ArrayJobID"], []).append(row)
    jobs: list[JobInfo] = []
    for job_id, rows in groups.items():
        status = reduce_statuses(state_to_status(row["State"]) for row in rows)
        row = next(r for r in rows if state_to_status(r["State"]) is status)
        started = status in _STARTED
        task = row["ArrayTaskID"]
        jobs.append(
            JobInfo(
                job_id=job_id,
                name=row["JobName"],
                user=row["User"],
                status=status,
                queue=row["Partition"] or None,
                submit_time=parse_time(row["SubmitTime"]),
                start_time=parse_time(row["StartTime"]) if started else None,
                runtime=parse_duration(row["TimeUsed"]) if started else None,
                cpu=int(row["CPUs"]) if row["CPUs"].isdigit() else None,
                node=row["NodeList"] or None,
                working_dir=Path(row["WorkDir"]) if row["WorkDir"] else None,
                array_task_id=int(task) if task.isdigit() else None,
            )
        )
    return jobs


# =============================================================================
# scontrol show job
# =============================================================================

#: Keys whose value is the rest of the line: a path or a name may hold a space.
_WHOLE_LINE = ("JobName", "Command", "WorkDir", "StdErr", "StdIn", "StdOut")


def parse_scontrol_job(output: str) -> list[Row]:
    """The records in ``scontrol show job <id>`` output, each ``{key: value}``.

    One record per job; an array has one per started task and one for the
    tasks still waiting, separated by blank lines. A line holds several
    ``Key=Value`` words; the keys of :data:`_WHOLE_LINE` end their line.
    """
    records: list[Row] = []
    for block in re.split(r"\n\s*\n", output):
        record: Row = {}
        for line in block.splitlines():
            rest = line.strip()
            while rest:
                key, sep, after = rest.partition("=")
                if not sep:
                    break
                if key in _WHOLE_LINE:
                    record[key] = after
                    break
                value, _, rest = after.partition(" ")
                record[key] = value
                rest = rest.lstrip()
        if "JobId" in record:
            records.append(record)
    return records


def scontrol_base(record: Row) -> str:
    """The id the job of *record* was submitted under (the array's, for a task)."""
    return record.get("ArrayJobId") or record["JobId"]


def scontrol_tasks(record: Row) -> tuple[int, ...]:
    """The array task indices *record* stands for; empty for a job that is not an array."""
    spec = record.get("ArrayTaskId")
    return expand_tasks(spec) if spec else ()


def scontrol_has_ended(record: Row) -> bool:
    """Whether the job of *record* has finished."""
    return state_to_status(record.get("JobState", "")).is_terminal


def scontrol_exit_code(record: Row) -> int | None:
    """The exit code *record* settles, None when it settles none.

    ``scontrol show job`` prints the exit status as the script gave it
    (137 stays 137), and the signal of a job that timed out or was
    cancelled while running. ``0:0`` is an exit code only for a COMPLETED
    job.
    """
    if not scontrol_has_ended(record):
        return None
    code = parse_exit_code(record.get("ExitCode", ""))
    if code == 0 and record.get("JobState") != "COMPLETED":
        return None
    return code


def scontrol_output_paths(record: Row) -> tuple[Path | None, Path | None]:
    """``(stdout, stderr)`` of *record*; stderr is None when it is the same file.

    The record of array tasks that have not started has no task id in its
    path (Slurm prints 4294967294 in its place), so it names no file. Such
    a record has no node.
    """
    if "ArrayTaskId" in record and not record.get("NodeList"):
        return None, None
    stdout = record.get("StdOut") or None
    stderr = record.get("StdErr") or None
    if stderr == stdout:
        stderr = None
    return (Path(stdout) if stdout else None, Path(stderr) if stderr else None)
