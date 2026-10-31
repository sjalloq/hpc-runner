"""SGE output parsing utilities."""

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
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
    except ET.ParseError:
        return jobs
    _strip_namespaces(root)

    # One entry per job id. qstat lists running entries (queue_info) before
    # pending ones (job_info); an array job with running and queued tasks
    # has both, and the first, running, entry is the one kept.
    for job_list in root.iter("job_list"):
        job_info = _parse_job_element(job_list)
        if job_info:
            jobs.setdefault(job_info["job_id"], job_info)

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


# The qacct ``failed`` codes of gridengine 8.1.9 and the text qacct prints
# after each (``failed  19  : before writing exit_status``). Taken from the
# testbed image's qacct itself (fed an accounting file with every code), not
# from memory: 0 is "no failure", 23 and anything above 38 other than 100
# print "invalid execution state". Codes 1-38 say where the shepherd was
# when the job failed; 100 is the one code set *after* the job ran.
SGE_FAILED_CODES: dict[int, str] = {
    1: "assumedly before job",
    2: "on executing shepherd",
    3: "before writing config",
    4: "before writing pid",
    5: "on reading config file",
    6: "setting processor set",
    7: "before prolog",
    8: "in prolog",
    9: "before pestart",
    10: "in pestart",
    11: "before job",
    12: "before pestop",
    13: "in pestop",
    14: "before epilog",
    15: "in epilog",
    16: "releasing processor set",
    17: "through signal",
    18: "shepherd returned error",
    19: "before writing exit_status",
    20: "found unexpected error file",
    21: "in recognizing job",
    22: "removed manually",
    24: "migrating",
    25: "rescheduling",
    26: "opening input/output file",
    27: "searching requested shell",
    28: "changing into working directory",
    29: "AFS setup",
    30: "application error returned",
    31: "accessing sgepasswd file",
    32: "entry is missing in sgepasswd file",
    33: "wrong password",
    34: "communicating with Grid Engine Helper Service",
    35: "before job in Grid Engine Helper Service",
    36: "checking configured daemons",
    37: "qmaster enforced h_rt, h_cpu, or h_vmem limit",
    38: "adding supplementary group",
    100: "assumedly after job",
}
QACCT_FAILED_INVALID = "invalid execution state"

#: ``failed`` 37: qmaster enforced ``h_rt``, ``h_cpu`` or ``h_vmem``.
QACCT_FAILED_LIMIT = 37
#: ``failed`` 100: the job ran; ``exit_status`` is its real exit code.
QACCT_FAILED_AFTER_JOB = 100
#: ``failed`` codes after which the job runs again, so the record is not final.
QACCT_FAILED_NOT_FINAL = frozenset({24, 25})


def failed_reason(code: int) -> str:
    """qacct's text for a ``failed`` *code* (``""`` for 0)."""
    if code == 0:
        return ""
    return SGE_FAILED_CODES.get(code, QACCT_FAILED_INVALID)


def parse_failed(value: object) -> tuple[int, str] | None:
    """``(code, reason)`` from a qacct ``failed`` value, or None if absent/garbled.

    The reason is the text qacct printed after the colon when there is one,
    else the table entry for the code.
    """
    code = _leading_int(value)
    if code is None:
        return None
    assert isinstance(value, str)
    _, sep, text = value.partition(":")
    reason = text.strip() if sep else failed_reason(code)
    return code, reason


def qacct_status(record: dict[str, Any]) -> JobStatus | None:
    """The status of a finished job from its parsed qacct record.

    One table decides, on the ``failed`` code first:

    * 0 or absent: the job ran to completion; ``exit_status`` decides via
      :func:`~hpc_runner.core.result.status_from_exit_code` (113 is SKIPPED).
    * 37 (qmaster enforced ``h_rt``/``h_cpu``/``h_vmem``): TIMEOUT.
    * 100 ("assumedly after job"): the job ran but ended abnormally, as on a
      ``qdel`` or an execd limit kill; ``exit_status`` (137 for SIGKILL) is
      real and decides, as for 0.
    * 24 and 25 (migrating, rescheduling): the job runs again, this record
      is not its end: None.
    * Anything else (1-38 bar 37): the job did not run to completion and
      ``exit_status`` is meaningless (a node that died mid-job records
      ``failed 19`` with ``exit_status 0``): FAILED.

    Returns:
        The status, or None if the record cannot decide it.
    """
    failed = _leading_int(record.get("failed")) or 0
    if failed == QACCT_FAILED_LIMIT:
        return JobStatus.TIMEOUT
    if failed in QACCT_FAILED_NOT_FINAL:
        return None
    if failed not in (0, QACCT_FAILED_AFTER_JOB):
        return JobStatus.FAILED
    exit_code = _leading_int(record.get("exit_status"))
    if exit_code is None:
        return None
    return status_from_exit_code(exit_code)


def _qacct_seconds(value: object) -> float | None:
    """A qacct duration (``"123.456"``, ``"7s"``, ``"0.030s"``) in seconds, or None."""
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*s?\s*", value)
    return float(match.group(1)) if match else None


def _leading_int(value: object) -> int | None:
    """The integer at the start of a qacct value.

    ``"37  : qmaster ..."`` -> 37; ``"137 (Killed)"`` (``exit_status`` of a
    signal death) -> 137.
    """
    if not isinstance(value, str):
        return None
    match = re.match(r"\s*(\d+)", value)
    return int(match.group(1)) if match else None


# What qstat writes to stderr when it cannot reach the master (gridengine
# 8.1.9 commlib): "error: unable to contact qmaster using port 6444 on host
# \"master\"", "error: commlib error: got select error (Connection refused)",
# "error: commlib error: can't connect to service (...)". A qstat that exits
# non-zero for any *other* reason (some sites' "no jobs") is still "nothing
# active".
_QSTAT_UNREACHABLE_RE = re.compile(r"qmaster|commlib|connect|contact", re.IGNORECASE)


def qstat_unreachable(stderr: str) -> bool:
    """Whether a failed qstat's *stderr* says the qmaster could not be reached."""
    return bool(_QSTAT_UNREACHABLE_RE.search(stderr))


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
    parsed by :func:`parse_qacct_output`. Only blocks with a ``jobnumber``
    are jobs: ``qacct -o <user>`` ends with a per-owner usage summary (a
    header, a rule and, when usage is non-zero, one row per owner) that
    would otherwise come back as a job with no id (BUG-T2).
    """
    records: list[dict[str, Any]] = []
    current_block: list[str] = []

    def flush() -> None:
        if current_block:
            record = parse_qacct_output("\n".join(current_block))
            if record.get("jobnumber"):
                records.append(record)
            current_block.clear()

    for line in output.split("\n"):
        if _SEPARATOR_RE.match(line.strip()):
            flush()
        else:
            current_block.append(line)
    flush()  # trailing block (no final separator)

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
      :func:`qacct_status`); ``failed`` -> ``failed_code`` and, when non-zero,
      ``failed_reason`` (the scheduler's own words, for ``hpc status``)
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
    failed = parse_failed(record.get("failed"))

    # CPU slots
    cpu: int | None = None
    slots_str = record.get("slots")
    if slots_str is not None:
        try:
            cpu = int(slots_str)
        except (ValueError, TypeError):
            pass

    # Runtime from ru_wallclock: "123.456" on older releases, "7s" (whole
    # seconds with a unit) on gridengine 8.1.9.
    runtime: timedelta | None = None
    seconds = _qacct_seconds(record.get("ru_wallclock"))
    if seconds is not None:
        runtime = timedelta(seconds=seconds)

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
        failed_code=failed[0] if failed else None,
        failed_reason=failed[1] if failed and failed[0] else None,
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


def parse_qstat_j_xml(xml_output: str) -> dict[str, object]:
    """Parse qstat -j -xml output to extract job details.

    Returns a dict with:
    - Basic: name, user, stdout_path, stderr_path
    - Resources: dict of resource_name -> value
    - PE: pe_name, pe_range
    - Paths: cwd, script_file
    - Dependencies: list of job IDs
    - Other: project, department
    """

    data: dict[str, object] = {}

    root = _parse_xml_root(xml_output)
    if root is None:
        return data
    _strip_namespaces(root)

    # Find job info element
    job_info = root.find(".//JB_job_number/..")
    if job_info is None:
        # Try alternative structure
        job_info = root.find(".//djob_info/element")
    if job_info is None:
        return data

    # Extract basic fields
    name_elem = job_info.find(".//JB_job_name")
    if name_elem is not None and name_elem.text:
        data["name"] = name_elem.text

    owner_elem = job_info.find(".//JB_owner")
    if owner_elem is not None and owner_elem.text:
        data["user"] = owner_elem.text

    # Project and department
    project_elem = job_info.find(".//JB_project")
    if project_elem is not None and project_elem.text:
        data["project"] = project_elem.text

    dept_elem = job_info.find(".//JB_department")
    if dept_elem is not None and dept_elem.text:
        data["department"] = dept_elem.text

    # Get cwd for resolving relative paths
    cwd: Path | None = None
    cwd_elem = job_info.find(".//JB_cwd")
    if cwd_elem is not None and cwd_elem.text:
        cwd = Path(cwd_elem.text)
        data["cwd"] = str(cwd)

    # Script file
    script_elem = job_info.find(".//JB_script_file")
    if script_elem is not None and script_elem.text:
        data["script_file"] = script_elem.text

    # Job arguments/command
    job_args: list[str] = []
    for arg_elem in job_info.findall(".//JB_job_args//ST_name"):
        if arg_elem.text:
            job_args.append(arg_elem.text)
    if job_args:
        data["job_args"] = job_args

    # Submission time
    submit_text = job_info.findtext(".//JB_submission_time")
    if submit_text:
        try:
            data["submit_time"] = int(submit_text)
        except ValueError:
            pass

    # Start time (for running jobs) - in JB_ja_tasks/ulong_sublist/JAT_start_time
    task_start_text = job_info.findtext(".//JB_ja_tasks/ulong_sublist/JAT_start_time")
    if task_start_text:
        try:
            data["start_time"] = int(task_start_text)
        except ValueError:
            pass

    # Also check direct JAT_start_time (alternative structure)
    if "start_time" not in data:
        start_text = job_info.findtext(".//JAT_start_time")
        if start_text:
            try:
                data["start_time"] = int(start_text)
            except ValueError:
                pass

    # For interactive jobs (qrsh), get command from QRSH_COMMAND env var
    for env_elem in job_info.findall(".//JB_env_list/job_sublist"):
        var_elem = env_elem.find("VA_variable")
        val_elem = env_elem.find("VA_value")
        if var_elem is not None and var_elem.text == "QRSH_COMMAND":
            if val_elem is not None and val_elem.text:
                data["command"] = _normalize_qrsh_command(val_elem.text)
            break

    # stdout path - look for PN_path in stdout_path_list
    stdout_path_elem = job_info.find(".//JB_stdout_path_list//PN_path")
    if stdout_path_elem is not None and stdout_path_elem.text:
        stdout_path = Path(stdout_path_elem.text)
        # Resolve relative paths against cwd
        if not stdout_path.is_absolute() and cwd:
            stdout_path = cwd / stdout_path
        data["stdout_path"] = stdout_path

    # stderr path
    stderr_path_elem = job_info.find(".//JB_stderr_path_list//PN_path")
    if stderr_path_elem is not None and stderr_path_elem.text:
        stderr_path = Path(stderr_path_elem.text)
        if not stderr_path.is_absolute() and cwd:
            stderr_path = cwd / stderr_path
        data["stderr_path"] = stderr_path

    # Check merge flag
    merge_elem = job_info.find(".//JB_merge_stderr")
    if merge_elem is not None and merge_elem.text:
        if merge_elem.text.lower() in ("true", "1", "y"):
            data["merge"] = True

    # If merge is enabled and we have stdout but no stderr, use stdout for both
    if data.get("merge") and data.get("stdout_path") and not data.get("stderr_path"):
        data["stderr_path"] = data["stdout_path"]

    # Parse hard resource list
    resources: dict[str, str] = {}
    for qstat_elem in job_info.findall(".//JB_hard_resource_list/qstat_l_requests"):
        res_name_elem = qstat_elem.find("CE_name")
        res_val_elem = qstat_elem.find("CE_stringval")
        if res_name_elem is not None and res_name_elem.text:
            res_name = res_name_elem.text
            res_val = res_val_elem.text if res_val_elem is not None else ""
            resources[res_name] = res_val or ""

    # Also check soft resources
    for qstat_elem in job_info.findall(".//JB_soft_resource_list/qstat_l_requests"):
        res_name_elem = qstat_elem.find("CE_name")
        res_val_elem = qstat_elem.find("CE_stringval")
        if res_name_elem is not None and res_name_elem.text:
            res_name = res_name_elem.text
            res_val = res_val_elem.text if res_val_elem is not None else ""
            resources[f"{res_name} (soft)"] = res_val or ""

    if resources:
        data["resources"] = resources

    # Parallel environment
    pe_elem = job_info.find(".//JB_pe")
    if pe_elem is not None and pe_elem.text:
        data["pe_name"] = pe_elem.text

    # PE range (min-max slots)
    pe_range_min = job_info.find(".//JB_pe_range//RN_min")
    pe_range_max = job_info.find(".//JB_pe_range//RN_max")
    if pe_range_min is not None and pe_range_max is not None:
        min_val = pe_range_min.text or "1"
        max_val = pe_range_max.text or "1"
        if min_val == max_val:
            data["pe_range"] = min_val
        else:
            data["pe_range"] = f"{min_val}-{max_val}"

    # Dependencies (predecessor jobs)
    dependencies: list[str] = []
    for dep_elem in job_info.findall(".//JB_jid_predecessor_list//JRE_job_number"):
        if dep_elem.text:
            dependencies.append(dep_elem.text)
    if dependencies:
        data["dependencies"] = dependencies

    return data


def _normalize_qrsh_command(value: str) -> str:
    """Normalize QRSH_COMMAND by replacing non-ASCII separators with spaces."""
    cleaned = "".join(ch if 32 <= ord(ch) < 127 else " " for ch in value)
    return " ".join(cleaned.split())


def _parse_xml_root(xml_output: str) -> ET.Element | None:
    """Parse XML output, tolerating leading/trailing non-XML noise."""

    try:
        return ET.fromstring(xml_output)
    except ET.ParseError:
        pass
    start = xml_output.find("<")
    end = xml_output.rfind(">")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return ET.fromstring(xml_output[start : end + 1])
    except ET.ParseError:
        return None
