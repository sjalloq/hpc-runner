"""SGE output parsing utilities."""

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Any

from hpc_runner.core.job_info import JobInfo
from hpc_runner.core.result import JobStatus, status_from_exit_code


def parse_qstat_xml(xml_output: str) -> dict[str, Any]:
    """Parse qstat -xml output.

    Returns dict with job_id -> job_info mappings.
    """
    jobs: dict[str, Any] = {}

    try:
        root = ET.fromstring(xml_output)
        _strip_namespaces(root)

        # Parse queue_info (running jobs)
        for job_list in root.findall(".//job_list"):
            job_info = _parse_job_element(job_list)
            if job_info:
                jobs[job_info["job_id"]] = job_info

        # Parse job_info (pending jobs)
        for job_list in root.findall(".//job_info/job_list"):
            job_info = _parse_job_element(job_list)
            if job_info:
                jobs[job_info["job_id"]] = job_info

    except ET.ParseError:
        pass

    return jobs


def parse_qstat_xml_states(xml_output: str) -> dict[str, str]:
    """Map every job and array task in ``qstat -xml`` output to its state code.

    A non-array job is keyed by its id. An array job's entries carry a
    ``tasks`` element (one task index when running, a range such as
    ``5-10:1`` or a list ``1,3`` when pending); each task is keyed
    ``<id>.<index>``. :func:`parse_qstat_xml` keeps one entry per id, which
    is enough for listing but not for per-task status.
    """
    states: dict[str, str] = {}
    try:
        root = ET.fromstring(xml_output)
    except ET.ParseError:
        return states
    _strip_namespaces(root)
    for elem in root.iter("job_list"):
        job_id = elem.findtext("JB_job_number")
        state = elem.findtext("state") or ""
        if not job_id:
            continue
        tasks = elem.findtext("tasks")
        if tasks:
            for index in _expand_task_list(tasks):
                states[f"{job_id}.{index}"] = state
        else:
            states[job_id] = state
    return states


def _expand_task_list(text: str) -> list[int]:
    """Expand an SGE task list (``4``, ``5-10:1``, ``1,3,7-9:2``) to indices."""
    indices: list[int] = []
    for part in text.split(","):
        match = re.fullmatch(r"\s*(\d+)(?:-(\d+)(?::(\d+))?)?\s*", part)
        if match is None:
            continue
        start, end, step = match.groups()
        last = int(end) if end is not None else int(start)
        indices.extend(range(int(start), last + 1, int(step) if step else 1))
    return indices


def _parse_job_element(elem: ET.Element) -> dict[str, Any] | None:
    """Parse a single job_list element.

    SGE XML elements include:
    - JB_job_number: Job ID
    - JB_name: Job name
    - JB_owner: Username
    - state: Job state (r, qw, hqw, etc.)
    - queue_name: Queue@host (for running jobs)
    - hard_req_queue: Requested queue (for pending jobs)
    - slots: Number of slots/CPUs
    - JB_submission_time: Submission timestamp (epoch)
    - JAT_start_time: Start timestamp (epoch, running jobs only)
    - tasks: Array task ID (for array jobs)
    """
    job_id_elem = elem.find("JB_job_number")
    if job_id_elem is None or job_id_elem.text is None:
        return None

    job_info: dict[str, Any] = {
        "job_id": job_id_elem.text,
    }

    # Job name
    name_elem = elem.find("JB_name")
    if name_elem is not None and name_elem.text:
        job_info["name"] = name_elem.text

    # Owner/user
    owner_elem = elem.find("JB_owner")
    if owner_elem is not None and owner_elem.text:
        job_info["user"] = owner_elem.text

    # State
    state_elem = elem.find("state")
    if state_elem is not None and state_elem.text:
        job_info["state"] = state_elem.text

    # Queue - running jobs have queue_name, pending may have hard_req_queue
    queue_elem = elem.find("queue_name")
    if queue_elem is not None and queue_elem.text:
        # Format is usually "queue@host", extract queue and host separately
        queue_full = queue_elem.text
        if "@" in queue_full:
            queue_name, host = queue_full.split("@", 1)
            job_info["queue"] = queue_name
            job_info["node"] = host
        else:
            job_info["queue"] = queue_full
    else:
        # Check for requested queue (pending jobs)
        hard_queue = elem.find("hard_req_queue")
        if hard_queue is not None and hard_queue.text:
            job_info["queue"] = hard_queue.text

    # Slots (CPU count)
    slots_elem = elem.find("slots")
    if slots_elem is not None and slots_elem.text:
        job_info["slots"] = int(slots_elem.text)

    # Submission time (epoch seconds or ISO 8601)
    submit_text = elem.findtext(".//JB_submission_time")
    if submit_text:
        submit_epoch = _parse_sge_timestamp(submit_text)
        if submit_epoch is not None:
            job_info["submit_time"] = submit_epoch

    # Start time (epoch seconds, only for running jobs)
    start_text = elem.findtext(".//JAT_start_time")
    if start_text:
        start_epoch = _parse_sge_timestamp(start_text)
        if start_epoch is not None:
            job_info["start_time"] = start_epoch

    # Array task ID
    tasks_elem = elem.find("tasks")
    if tasks_elem is not None and tasks_elem.text:
        job_info["array_task_id"] = tasks_elem.text

    return job_info


def _strip_namespaces(root: ET.Element) -> None:
    """Strip XML namespaces so ElementTree finds simple tag names."""
    for elem in root.iter():
        if isinstance(elem.tag, str) and "}" in elem.tag:
            elem.tag = elem.tag.split("}", 1)[1]


def parse_qstat_plain(output: str) -> dict[str, Any]:
    """Parse plain qstat output.

    Format:
    job-ID  prior   name       user         state submit/start at     queue  slots ja-task-ID
    --------------------------------------------------------------------------
    12345   0.55500 myjob      user         r     01/01/2024 10:00:00 all.q@node1  1
    """
    jobs: dict[str, Any] = {}

    lines = output.strip().split("\n")

    # Skip header lines
    data_started = False
    for line in lines:
        if line.startswith("-"):
            data_started = True
            continue
        if not data_started:
            continue

        parts = line.split()
        if len(parts) >= 5:
            job_id = parts[0]
            jobs[job_id] = {
                "job_id": job_id,
                "priority": parts[1],
                "name": parts[2],
                "user": parts[3],
                "state": parts[4],
            }

            # Parse submit/start time (MM/DD/YYYY HH:MM:SS)
            if len(parts) >= 7:
                timestamp = _parse_qstat_datetime(parts[5], parts[6])
                if timestamp is not None:
                    if "r" in parts[4]:
                        jobs[job_id]["start_time"] = timestamp
                    else:
                        jobs[job_id]["submit_time"] = timestamp

            # Parse queue if present
            if len(parts) >= 8:
                jobs[job_id]["queue"] = parts[7]

            # Parse slots if present
            if len(parts) >= 9:
                try:
                    jobs[job_id]["slots"] = int(parts[8])
                except ValueError:
                    pass

    return jobs


def _parse_qstat_datetime(date_part: str, time_part: str) -> int | None:
    """Parse qstat date/time into epoch seconds."""
    try:
        dt = datetime.strptime(f"{date_part} {time_part}", "%m/%d/%Y %H:%M:%S")
    except ValueError:
        return None
    return int(dt.timestamp())


def _parse_sge_timestamp(value: str) -> int | None:
    """Parse SGE timestamps that may be epoch seconds or ISO 8601."""
    if value.isdigit():
        try:
            return int(value)
        except ValueError:
            return None
    try:
        return int(datetime.strptime(value, "%Y-%m-%dT%H:%M:%S").timestamp())
    except ValueError:
        return None


def parse_qacct_output(output: str) -> dict[str, Any]:
    """Parse qacct output for job accounting info.

    Format:
    ==============================================================
    qname        all.q
    hostname     node1
    group        users
    owner        user
    jobname      myjob
    jobnumber    12345
    ...
    exit_status  0
    """
    info: dict[str, Any] = {}

    for line in output.strip().split("\n"):
        if line.startswith("="):
            continue

        parts = line.split(None, 1)
        if len(parts) == 2:
            key, value = parts
            info[key] = value.strip()

    return info


# qacct ``failed`` code for "qmaster enforced h_rt, h_cpu, or h_vmem limit"
# (accounting.5 / sge_status.5, SGE 8.1.9).
QACCT_FAILED_LIMIT = 37


def qacct_status(record: dict[str, Any]) -> JobStatus | None:
    """The status of a finished job from its parsed qacct record.

    ``failed`` 37 (qmaster enforced a time or memory limit) is TIMEOUT.
    Otherwise ``exit_status`` decides via
    :func:`~hpc_runner.core.result.status_from_exit_code` (113 is SKIPPED).
    An execd ``h_rt`` kill is recorded as ``failed 100`` with
    ``exit_status 137``, indistinguishable from any other signal death, so
    it stays FAILED.

    Returns:
        The status, or None if the record has no usable exit status.
    """
    failed = _leading_int(record.get("failed"))
    if failed == QACCT_FAILED_LIMIT:
        return JobStatus.TIMEOUT
    exit_code = _leading_int(record.get("exit_status"))
    if exit_code is None:
        return None
    return status_from_exit_code(exit_code)


def _leading_int(value: object) -> int | None:
    """The integer at the start of a qacct value (``"37  : qmaster ..."`` -> 37)."""
    if not isinstance(value, str):
        return None
    match = re.match(r"\s*(\d+)", value)
    return int(match.group(1)) if match else None


def state_to_status(state: str) -> JobStatus:
    """Convert SGE state code to JobStatus.

    SGE states:
    - qw: pending (queued, waiting)
    - hqw: hold (on hold)
    - r: running
    - t: transferring
    - Rr, Rt: restarted
    - s, ts: suspended
    - S, tS: queue suspended
    - T, tT: threshold
    - Eqw: error (waiting)
    - dr: deleting (running)
    - dt: deleting (transferring)
    """
    state = state.lower()

    # Deleting or error states take precedence over other flags.
    if "d" in state:
        return JobStatus.CANCELLED
    if "e" in state:
        return JobStatus.FAILED

    # Running or transferring states.
    if "r" in state or "t" in state:
        return JobStatus.RUNNING

    # Queued, held, or suspended states.
    if "q" in state or "h" in state or "s" in state:
        return JobStatus.PENDING

    return JobStatus.UNKNOWN


def parse_qsub_output(output: str) -> str | None:
    """Parse qsub output to extract job ID.

    Expected format:
    Your job 12345 ("jobname") has been submitted
    Your job-array 12345.1-10:1 ("jobname") has been submitted
    """
    # Standard job
    match = re.search(r"Your job (\d+)", output)
    if match:
        return match.group(1)

    # Array job
    match = re.search(r"Your job-array (\d+)", output)
    if match:
        return match.group(1)

    return None


# =========================================================================
# qacct multi-record parsing
# =========================================================================

_SEPARATOR_RE = re.compile(r"^=+$")

# qacct timestamps: "Mon Feb 23 18:00:00 2026"
_QACCT_TIME_FMT = "%a %b %d %H:%M:%S %Y"


def parse_qacct_records(output: str) -> list[dict[str, Any]]:
    """Split multi-record qacct output into a list of parsed dicts.

    qacct separates records with lines of ``====…====``.  Each block is
    parsed by :func:`parse_qacct_output`.
    """
    records: list[dict[str, Any]] = []
    current_block: list[str] = []

    for line in output.split("\n"):
        if _SEPARATOR_RE.match(line.strip()):
            if current_block:
                record = parse_qacct_output("\n".join(current_block))
                if record:
                    records.append(record)
                current_block = []
        else:
            current_block.append(line)

    # Handle trailing block (no final separator)
    if current_block:
        record = parse_qacct_output("\n".join(current_block))
        if record:
            records.append(record)

    return records


def _parse_qacct_timestamp(value: str) -> datetime | None:
    """Parse a qacct timestamp string into a :class:`datetime`.

    Handles the standard ``"Mon Feb 23 18:00:00 2026"`` format.
    """
    try:
        return datetime.strptime(value.strip(), _QACCT_TIME_FMT)
    except ValueError:
        return None


def qacct_to_job_info(record: dict[str, Any]) -> JobInfo:
    """Convert a single parsed qacct record dict to a :class:`JobInfo`.

    Field mapping:

    * ``jobnumber`` -> ``job_id``
    * ``jobname`` -> ``name``
    * ``owner`` -> ``user``
    * ``exit_status`` -> ``exit_code``; with ``failed``, ``status`` (see
      :func:`qacct_status`)
    * ``qname`` -> ``queue``
    * ``hostname`` -> ``node``
    * ``slots`` -> ``cpu``
    * ``ru_wallclock`` -> ``runtime``
    * ``qsub_time`` -> ``submit_time``
    * ``start_time`` -> ``start_time``
    * ``end_time`` -> ``end_time``
    """
    exit_code = _leading_int(record.get("exit_status"))
    status = qacct_status(record) or JobStatus.UNKNOWN

    # CPU slots
    cpu: int | None = None
    slots_str = record.get("slots")
    if slots_str is not None:
        try:
            cpu = int(slots_str)
        except (ValueError, TypeError):
            pass

    # Runtime from ru_wallclock (seconds as a float string)
    runtime: timedelta | None = None
    wallclock_str = record.get("ru_wallclock")
    if wallclock_str is not None:
        try:
            runtime = timedelta(seconds=float(wallclock_str))
        except (ValueError, TypeError):
            pass

    job_info = JobInfo(
        job_id=record.get("jobnumber", ""),
        name=record.get("jobname", ""),
        user=record.get("owner", "unknown"),
        status=status,
        queue=record.get("qname"),
        node=record.get("hostname"),
        cpu=cpu,
        exit_code=exit_code,
        runtime=runtime,
    )

    # Timestamps
    qsub_time = record.get("qsub_time")
    if qsub_time:
        job_info.submit_time = _parse_qacct_timestamp(qsub_time)

    start_time = record.get("start_time")
    if start_time:
        job_info.start_time = _parse_qacct_timestamp(start_time)

    end_time = record.get("end_time")
    if end_time:
        job_info.end_time = _parse_qacct_timestamp(end_time)

    return job_info
