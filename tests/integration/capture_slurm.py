"""Capture real Slurm output from the testbed as unit-test fixtures.

Run inside ``login`` by ``hpc-testbed --scheduler slurm capture`` (``python -m
tests.integration.capture_slurm OUTDIR FLAVOUR``); the driver copies OUTDIR to
``tests/unit/fixtures/slurm/<flavour>/``. The SGE counterpart is
``tests/integration/capture.py``. Regenerate on demand; job ids, timestamps
and host names change every capture, structure does not.

Every job is submitted with the raw CLI, so the texts do not depend on
hpc-runner's own rendering. What is kept of a scenario:

* ``sbatch_<scenario>.json``: the submit command's argv, exit status, stdout
  and stderr (``srun_<scenario>.json`` too for the two rejections).
* ``sacct_<scenario>[_<stage>].txt``: ``sacct -P -n -j <id>
  --format=SACCT_FORMAT``, no ``-X``, so the job's own row, its ``.batch``
  row and one row per started array task. ``..._duplicates.txt`` is the same
  with ``-D``.
* ``squeue_<scenario>[_<stage>].txt``: ``squeue -h -j <id> -o SQUEUE_FORMAT``
  while the job is live; ``..._tasks.txt`` is the same with ``-r`` (one line
  per array task).
* ``scontrol_<scenario>[_<stage>].txt``: ``scontrol show job <id>`` while the
  controller knows the job.
* ``scancel_<scenario>.json``: ``scancel <id>`` where the scenario cancels.
* ``sbatch_wait_<scenario>.json``: ``sbatch --wait`` of the job, which returns
  when the job has ended: argv, exit status, stdout and stderr. Its job is
  the scenario ``wait_<scenario>``, with ``sacct`` and ``scontrol`` texts
  taken once it has ended.

Scenarios (docs/dev/slurm/PLAN.md, "Captures to take"; the ones marked + are
not in that list):

* ``ok``, ``exit1``: finished jobs, exit 0 and 1.
* ``ok_stub`` +, ``running_stub`` +: ``sacct`` rows caught before slurmdbd
  has the whole record (name ``allocation``, no partition), of a finished and
  of a running job. Hunted over several jobs; kept only when seen (22.05).
* ``signal``: the batch shell kills itself with SIGKILL.
* ``exit137`` +: the script exits 137, what a shell reports for a command
  killed by SIGKILL.
* ``timeout``: ``--time=1``, killed at the limit. Submitted first, collected
  when it has ended.
* ``out_of_memory``: a 200 MB allocation under ``--mem=50M``.
  ``out_of_memory_step`` +: the same inside an ``srun`` step, script exits 0.
* ``cancelled_pending``, ``cancelled_running``: ``scancel`` of a held job and
  of a running one.
* ``completing`` +: ``scancel`` of a running job, read at once (stage
  ``just_cancelled``): ``scontrol show job`` says ``JobState=COMPLETING``
  for a moment. Submitted again until it is seen (:data:`COMPLETING_ATTEMPTS`
  jobs at most), then read once it has ended.
* ``held``: ``--hold``, never released.
* ``dependent_ok``, ``dependent_skipped``, ``dependent_pending``: ``afterok``
  on a parent that exits 0, and on one that exits 1 with and without
  ``--kill-on-invalid-dep=yes``. Stage ``before``: the parent held, not yet
  run. ``dependent_pending`` is then cancelled (stage ``cancelled``).
* ``array``: four tasks, held (stage ``pending``), then finished.
* ``array_partial``: three tasks, task 3 exits 1.
* ``array_throttled``: ``--array=1-4%2`` with two tasks running and two
  pending, then cancelled (stage ``cancelled``).
* ``array_cancelled_pending`` +: a held array cancelled before any task ran.
* ``suspended``: ``scontrol suspend`` (as root) of a running job.
* ``requeued``: ``scontrol requeue`` of a running job, pending again; stage
  ``running`` once it has started a second time.
* ``node_fail``: a ``--no-requeue`` job whose node is killed.
  ``node_fail_requeued`` +: the same job without ``--no-requeue``, which the
  controller requeues.
* ``running``: a running job.
* ``pending_resources``: a two-CPU job with no node able to hold it.
* ``bad_partition``, ``bad_account``: ``sbatch`` and ``srun`` rejections.
* ``array_list`` +, ``array_stepped`` +, ``array_single`` +: held arrays
  submitted with ``--array=1,3,7-9:2``, ``--array=1-100:3`` and
  ``--array=5``, for the task ids the readers print in one row.
* ``missing`` +: ``sacct``, ``squeue``, ``scontrol show job`` and ``scancel``
  of an id that never existed (JSON).
* ``not_owner`` +: ``scancel`` of another user's job (JSON).
* ``wait_ok`` +, ``wait_exit1`` +, ``wait_exit137`` +, ``wait_out_of_memory``
  +, ``wait_array_partial`` +, ``wait_cancelled_pending`` +,
  ``wait_cancelled_running`` +, ``wait_dependent_skipped`` +: the jobs of the
  same names without ``wait_``, submitted with ``sbatch --wait``.

Not about one job: ``squeue_empty.txt`` (nothing queued), ``squeue_all.txt``
(every live job of the last snapshot: running, suspended, held, pending for
resources), ``sacct_several.txt`` (``-j`` with three ids) and
``sacct_history.txt`` (``sacct -u tester -S <when the capture began>``, taken
while the snapshot's jobs are live: every job of the run, finished or not).

``manifest.json`` records the job id of every scenario and, for each file,
what it is and the argv that produced it; ``history_since`` is the time
given to ``sacct -S``.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tests.integration.cluster import WORK_ROOT, SlurmCluster

#: The ``sacct`` columns every capture is taken with, in order. Every name is
#: accepted by 22.05 and by 24.11 (``sacct -e``); ``StdOut`` is not in it
#: because 22.05 has no such field. The job name is last.
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

#: The ``squeue`` columns, in order: (format code, what it is). The job name
#: is last.
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

#: Where the jobs run and write their output; removed when the capture ends.
JOBS_DIR = WORK_ROOT / "capture-jobs"
#: A job's output file, relative to its working directory.
OUTPUT_PATTERN = "%x.%j.out"
ARRAY_OUTPUT_PATTERN = "%x.%A.%a.out"

WAIT = 60.0
POLL = 0.2
#: How many jobs a stub hunt submits before it gives up.
STUB_ATTEMPTS = 6
#: How many jobs are cancelled to see ``JobState=COMPLETING`` before the capture fails.
COMPLETING_ATTEMPTS = 5

_JOB_ID = SACCT_FIELDS.index("JobID")
_PARTITION = SACCT_FIELDS.index("Partition")
_STATE = SACCT_FIELDS.index("State")
_NAME = SACCT_FIELDS.index("JobName")

Rows = list[list[str]]


def sacct_argv(job_ids: str, *, duplicates: bool = False) -> list[str]:
    """``sacct`` of *job_ids* (comma-separated), with ``-D`` for *duplicates*."""
    flags = ["-D"] if duplicates else []
    return ["sacct", "-P", "-n", *flags, "-j", job_ids, f"--format={SACCT_FORMAT}"]


def squeue_argv(job_id: str | None = None, *, tasks: bool = False) -> list[str]:
    """``squeue`` of *job_id* (every job when None); *tasks* gives one line per array task."""
    argv = ["squeue", "-h"]
    if tasks:
        argv.append("-r")
    if job_id is not None:
        argv += ["-j", job_id]
    return [*argv, "-o", SQUEUE_FORMAT]


def history_argv(user: str, since: str) -> list[str]:
    """``sacct`` of every job of *user* since *since* (``YYYY-MM-DDTHH:MM:SS``)."""
    return ["sacct", "-P", "-n", "-u", user, "-S", since, f"--format={SACCT_FORMAT}"]


def scontrol_argv(job_id: str) -> list[str]:
    return ["scontrol", "show", "job", job_id]


def sbatch_argv(scenario: str, script: str, *args: str, array: bool = False) -> list[str]:
    """``sbatch`` of *script*, named *scenario*, running and writing in :data:`JOBS_DIR`."""
    pattern = ARRAY_OUTPUT_PATTERN if array else OUTPUT_PATTERN
    return ["sbatch", "-J", scenario, f"--chdir={JOBS_DIR}", "-o", pattern, *args, script]


def job_rows(rows: Rows) -> Rows:
    """The rows of jobs and array tasks themselves: no ``.batch``, no step."""
    return [row for row in rows if "." not in row[_JOB_ID]]


def is_stub(rows: Rows) -> bool:
    """Whether a job's own row is the early one with no partition."""
    return any(row[_PARTITION] == "" for row in job_rows(rows))


def states(rows: Rows) -> list[str]:
    """The first word of each job row's state (``CANCELLED by 1003`` is ``CANCELLED``)."""
    return [row[_STATE].split()[0] for row in job_rows(rows)]


class Capture:
    def __init__(self, cluster: SlurmCluster, out: Path) -> None:
        self.cluster = cluster
        self.out = out
        self.jobs: dict[str, str] = {}
        self.files: dict[str, dict[str, Any]] = {}
        self.began = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    # -- helpers --------------------------------------------------------------

    def log(self, message: str) -> None:
        print(f"capture: {message}", file=sys.stderr, flush=True)

    def save(self, name: str, text: str, what: str, argv: Sequence[str]) -> None:
        (self.out / name).write_text(text)
        self.files[name] = {"what": what, "argv": list(argv)}

    def capture(self, name: str, argv: Sequence[str], what: str) -> None:
        """Run *argv* and save its stdout as *name*."""
        self.save(name, self.cluster.run(list(argv)).stdout, what, argv)

    def capture_result(
        self, name: str, argv: Sequence[str], what: str, *, user: str = "tester"
    ) -> subprocess.CompletedProcess[str]:
        """Run *argv* and save argv, exit status, stdout and stderr as JSON."""
        proc = self.cluster.run(list(argv), user=user, check=False)
        self.save(name, _as_json(argv, proc), f"{what} (JSON)", argv)
        return proc

    def script(self, name: str, body: str) -> str:
        path = JOBS_DIR / f"{name}.sh"
        path.write_text(f"#!/bin/bash\n{body}")
        return str(path)

    def sbatch(self, scenario: str, body: str, *args: str, save: bool = True) -> str:
        """Submit a script whose text is *body*; record and return the job id.

        With *save*, the submission is kept as ``sbatch_<scenario>.json``.
        """
        array = any(arg.startswith("--array") for arg in args)
        argv = sbatch_argv(scenario, self.script(scenario, body), *args, array=array)
        if save:
            proc = self.capture_result(
                f"sbatch_{scenario}.json", argv, f"sbatch of the {scenario} job"
            )
        else:
            proc = self.cluster.run(argv, check=False)
        words = proc.stdout.split()
        assert proc.returncode == 0 and words[:3] == ["Submitted", "batch", "job"], (
            proc.stdout,
            proc.stderr,
        )
        self.jobs[scenario] = words[3]
        self.log(f"{scenario}: job {words[3]}")
        return words[3]

    def rows(self, job_id: str, *, duplicates: bool = False) -> Rows:
        text = self.cluster.run(sacct_argv(job_id, duplicates=duplicates)).stdout
        return [line.split("|") for line in text.splitlines()]

    def live(self, job_id: str) -> list[tuple[str, str]]:
        """(state, reason) of each ``squeue`` line of *job_id*, one per array task."""
        argv = ["squeue", "-h", "-r", "-j", job_id, "-o", "%T|%r"]
        text = self.cluster.run(argv, check=False).stdout
        return [(line.split("|")[0], line.split("|")[1]) for line in text.splitlines()]

    def wait(self, what: str, done: Callable[[], bool], timeout: float = WAIT) -> None:
        deadline = time.monotonic() + timeout
        while not done():
            assert time.monotonic() < deadline, f"timed out after {timeout}s: {what}"
            time.sleep(POLL)

    def wait_sacct(
        self,
        scenario: str,
        done: Callable[[Rows], bool],
        timeout: float = WAIT,
        *,
        duplicates: bool = False,
    ) -> None:
        """Wait until the scenario job's ``sacct`` rows are whole and *done* accepts them."""
        job_id = self.jobs[scenario]

        def ready() -> bool:
            rows = self.rows(job_id, duplicates=duplicates)
            return bool(rows) and not is_stub(rows) and done(rows)

        self.wait(f"sacct of {scenario} ({job_id})", ready, timeout)

    def wait_ended(self, scenario: str, state: str, tasks: int = 1, timeout: float = WAIT) -> None:
        """Wait until *tasks* job rows say *state* and ``squeue`` has let the job go."""
        self.wait_sacct(scenario, lambda rows: states(rows) == [state] * tasks, timeout)
        self.wait(f"{scenario} leaving squeue", lambda: not self.live(self.jobs[scenario]))

    def wait_live(self, scenario: str, state: str, reason: str | None = None) -> None:
        """Wait until ``squeue`` gives the job *state* (and *reason*) and ``sacct`` agrees."""
        job_id = self.jobs[scenario]

        def ready() -> bool:
            live = self.live(job_id)
            return bool(live) and all(
                s == state and (reason is None or r == reason) for s, r in live
            )

        self.wait(f"{scenario} ({job_id}) being {state} {reason or ''}", ready)
        self.wait_sacct(scenario, lambda rows: states(rows)[-1:] == [state])

    def stage(
        self,
        scenario: str,
        what: str,
        stage: str = "",
        *,
        squeue: bool = False,
        tasks: bool = False,
        duplicates: bool = False,
        scontrol: bool = True,
    ) -> None:
        """Save what the readers show of the scenario's job as it is now."""
        job_id = self.jobs[scenario]
        tail = f"{scenario}_{stage}" if stage else scenario
        label = f"the {scenario} job: {what}"
        self.capture(f"sacct_{tail}.txt", sacct_argv(job_id), f"sacct of {label}")
        if duplicates:
            self.capture(
                f"sacct_{tail}_duplicates.txt",
                sacct_argv(job_id, duplicates=True),
                f"sacct -D of {label}",
            )
        if squeue:
            self.capture(f"squeue_{tail}.txt", squeue_argv(job_id), f"squeue of {label}")
        if tasks:
            self.capture(
                f"squeue_{tail}_tasks.txt",
                squeue_argv(job_id, tasks=True),
                f"squeue -r of {label}",
            )
        if scontrol:
            self.capture(
                f"scontrol_{tail}.txt", scontrol_argv(job_id), f"scontrol show job of {label}"
            )

    def scancel(self, scenario: str) -> None:
        argv = ["scancel", self.jobs[scenario]]
        proc = self.capture_result(
            f"scancel_{scenario}.json", argv, f"scancel of the {scenario} job"
        )
        assert proc.returncode == 0, proc.stderr

    # -- scenarios ------------------------------------------------------------

    def empty(self) -> None:
        self.capture("squeue_empty.txt", squeue_argv(), "squeue with nothing queued")
        for name, argv in (
            ("sacct_missing.json", sacct_argv("999999")),
            ("squeue_missing.json", squeue_argv("999999")),
            ("scontrol_missing.json", scontrol_argv("999999")),
            ("scancel_missing.json", ["scancel", "999999"]),
        ):
            self.capture_result(name, argv, f"{argv[0]} of an id that never existed")

    def stub(self, scenario: str, body: str, wanted: Callable[[Rows], bool]) -> None:
        """Hunt for the early ``sacct`` row of a job: no partition, name ``allocation``.

        Polls ``sacct`` without a pause from the moment of submission, over up
        to :data:`STUB_ATTEMPTS` jobs. Nothing is saved when no stub that
        *wanted* accepts is seen (24.11 has never shown one).
        """
        for _ in range(STUB_ATTEMPTS):
            job_id = self.sbatch(scenario, body, save=False)
            argv = sacct_argv(job_id)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                text = self.cluster.run(argv).stdout
                rows = [line.split("|") for line in text.splitlines()]
                if rows and is_stub(rows) and wanted(rows):
                    what = f"sacct of the {scenario} job: the early row, before the whole record"
                    self.save(f"sacct_{scenario}.txt", text, what, argv)
                    self.cluster.run(["scancel", job_id], check=False)
                    return
                if rows and not is_stub(rows) and wanted(rows):
                    break
            self.cluster.run(["scancel", job_id], check=False)
        self.log(f"{scenario}: no stub row seen in {STUB_ATTEMPTS} jobs")
        del self.jobs[scenario]

    def stubs(self) -> None:
        self.stub("ok_stub", "true\n", lambda rows: states(rows) == ["COMPLETED"])
        # The job's own row with its .batch row already RUNNING.
        self.stub(
            "running_stub",
            "sleep 600\n",
            lambda rows: any(
                r[_JOB_ID].endswith(".batch") and r[_STATE] == "RUNNING" for r in rows
            ),
        )

    def finished_jobs(self) -> None:
        """The jobs that end by themselves, submitted together, collected in turn."""
        oom = 'python3 -c "b = bytearray(200 * 1024 * 1024)"'
        self.sbatch("ok", "true\n")
        self.sbatch("exit1", "exit 1\n")
        self.sbatch("signal", "kill -KILL $$\n")
        self.sbatch("exit137", "exit 137\n")
        self.sbatch("out_of_memory", f"{oom}\n", "--mem=50M")
        self.sbatch("out_of_memory_step", f"srun {oom}\nexit 0\n", "--mem=50M")
        self.sbatch("array_partial", 'test "$SLURM_ARRAY_TASK_ID" -ne 3\n', "--array=1-3")
        for scenario, state, what in (
            ("ok", "COMPLETED", "exited 0"),
            ("exit1", "FAILED", "exited 1"),
            ("signal", "FAILED", "the batch shell killed itself with SIGKILL"),
            ("exit137", "FAILED", "the script exited 137"),
            ("out_of_memory", "OUT_OF_MEMORY", "200 MB allocated under --mem=50M"),
            (
                "out_of_memory_step",
                "COMPLETED",
                "200 MB allocated under --mem=50M inside an srun step, then exit 0",
            ),
        ):
            self.wait_ended(scenario, state)
            self.stage(scenario, what)
        self.wait_sacct(
            "array_partial",
            lambda rows: sorted(states(rows)) == ["COMPLETED", "COMPLETED", "FAILED"],
        )
        self.wait("array_partial leaving squeue", lambda: not self.live(self.jobs["array_partial"]))
        self.stage("array_partial", "three tasks finished, task 3 exited 1")
        ids = ",".join(self.jobs[s] for s in ("ok", "exit1", "array_partial"))
        self.capture(
            "sacct_several.txt",
            sacct_argv(ids),
            "sacct -j of three ids at once: the ok, exit1 and array_partial jobs",
        )

    def cancelled(self) -> None:
        self.sbatch("cancelled_pending", "sleep 600\n", "--hold")
        self.sbatch("cancelled_running", "sleep 600\n")
        self.sbatch("array_cancelled_pending", "sleep 600\n", "--hold", "--array=1-4")
        self.wait_live("cancelled_pending", "PENDING", "JobHeldUser")
        self.wait_live("array_cancelled_pending", "PENDING", "JobHeldUser")
        self.wait_live("cancelled_running", "RUNNING")
        for scenario, what in (
            ("cancelled_pending", "held, then cancelled before it ran"),
            ("cancelled_running", "cancelled while running"),
            ("array_cancelled_pending", "a held array cancelled before any task ran"),
        ):
            self.scancel(scenario)
            self.wait_ended(scenario, "CANCELLED")
            self.stage(scenario, what)

    def completing(self) -> None:
        """A running job just cancelled, while the controller still says ``COMPLETING``.

        ``scontrol show job`` and then ``sacct`` are read straight after
        ``scancel`` returns. The whole scenario is run again with a new job
        when the record does not say ``JobState=COMPLETING``.

        Raises:
            AssertionError: No job of :data:`COMPLETING_ATTEMPTS` showed it.
        """
        for _ in range(COMPLETING_ATTEMPTS):
            job_id = self.sbatch("completing", "sleep 600\n")
            self.wait_live("completing", "RUNNING")
            self.scancel("completing")
            scontrol = self.cluster.run(scontrol_argv(job_id), check=False).stdout
            sacct = self.cluster.run(sacct_argv(job_id)).stdout
            self.wait_ended("completing", "CANCELLED")
            if "JobState=COMPLETING" not in scontrol:
                self.log(f"completing: job {job_id} was not seen COMPLETING")
                continue
            label = "the completing job: just cancelled while running, still COMPLETING"
            self.save(
                "scontrol_completing_just_cancelled.txt",
                scontrol,
                f"scontrol show job of {label}",
                scontrol_argv(job_id),
            )
            self.save(
                "sacct_completing_just_cancelled.txt",
                sacct,
                f"sacct of {label}",
                sacct_argv(job_id),
            )
            self.stage("completing", "cancelled while running, once it has ended")
            return
        raise AssertionError(
            f"no job said JobState=COMPLETING after scancel in {COMPLETING_ATTEMPTS} attempts"
        )

    def not_owner(self) -> None:
        """``scancel``, as tester, of a job that is alice's."""
        argv = ["sbatch", "-J", "not_owner", "--hold", "-o", "/dev/null", "--wrap", "sleep 600"]
        job_id = self.cluster.run(argv, user="alice").stdout.split()[3]
        self.capture_result(
            "scancel_not_owner.json", ["scancel", job_id], "scancel of another user's job"
        )
        self.cluster.run(["scancel", job_id], user="alice")

    def waited(self) -> None:
        """``sbatch --wait``: what it prints, and what it exits with when the job has ended."""
        oom = 'python3 -c "b = bytearray(200 * 1024 * 1024)"'
        partial = 'test "$SLURM_ARRAY_TASK_ID" -ne 3\n'

        def submit(scenario: str, body: str, *args: str) -> None:
            name = f"wait_{scenario}"
            array = any(arg.startswith("--array") for arg in args)
            argv = sbatch_argv(name, self.script(name, body), "--wait", *args, array=array)
            proc = self.capture_result(
                f"sbatch_{name}.json", argv, f"sbatch --wait of the {name} job"
            )
            words = proc.stdout.split()
            assert words[:3] == ["Submitted", "batch", "job"], (proc.stdout, proc.stderr)
            self.jobs[name] = words[3]
            self.log(f"{name}: job {words[3]}, sbatch exited {proc.returncode}")

        def start(scenario: str, body: str, *args: str) -> threading.Thread:
            """Submit in a thread, and return once ``squeue`` lists the job."""
            thread = threading.Thread(target=submit, args=(scenario, body, *args))
            thread.start()
            argv = ["squeue", "-h", "-n", f"wait_{scenario}", "-o", "%i"]
            self.wait(
                f"wait_{scenario} in squeue", lambda: bool(self.cluster.run(argv).stdout.strip())
            )
            return thread

        for scenario, body, args, state, what in (
            ("ok", "true\n", (), "COMPLETED", "exited 0"),
            ("exit1", "exit 1\n", (), "FAILED", "exited 1"),
            ("exit137", "exit 137\n", (), "FAILED", "the script exited 137"),
            ("out_of_memory", f"{oom}\n", ("--mem=50M",), "OUT_OF_MEMORY", "over --mem=50M"),
        ):
            submit(scenario, body, *args)
            self.wait_ended(f"wait_{scenario}", state)
            self.stage(f"wait_{scenario}", f"submitted with sbatch --wait: {what}")

        submit("array_partial", partial, "--array=1-3")
        self.wait_sacct(
            "wait_array_partial",
            lambda rows: sorted(states(rows)) == ["COMPLETED", "COMPLETED", "FAILED"],
        )
        self.stage(
            "wait_array_partial", "submitted with sbatch --wait: three tasks, task 3 exited 1"
        )

        pending = start("cancelled_pending", "sleep 600\n", "--hold")
        running = start("cancelled_running", "sleep 600\n")
        parent = self.sbatch("wait_dependent_skipped_parent", "exit 1\n", "--hold", save=False)
        skipped = start(
            "dependent_skipped",
            "true\n",
            f"--dependency=afterok:{parent}",
            "--kill-on-invalid-dep=yes",
        )
        self.wait(
            "wait_cancelled_running running",
            lambda: (
                self.cluster.run(
                    ["squeue", "-h", "-n", "wait_cancelled_running", "-o", "%T"]
                ).stdout.strip()
                == "RUNNING"
            ),
        )
        for name in ("wait_cancelled_pending", "wait_cancelled_running"):
            self.cluster.run(["scancel", "-n", name])
        self.cluster.run(["scontrol", "release", parent])
        for thread in (pending, running, skipped):
            thread.join()
        for scenario, what in (
            ("cancelled_pending", "held, then cancelled before it ran"),
            ("cancelled_running", "cancelled while running"),
            ("dependent_skipped", "its parent exited 1; cancelled by --kill-on-invalid-dep=yes"),
        ):
            self.wait_ended(f"wait_{scenario}", "CANCELLED")
            self.stage(f"wait_{scenario}", f"submitted with sbatch --wait: {what}")

    def dependents(self) -> None:
        """Three children, each ``afterok`` on its own held parent."""
        cases = (
            ("dependent_ok", "true\n", ()),
            ("dependent_skipped", "exit 1\n", ("--kill-on-invalid-dep=yes",)),
            ("dependent_pending", "exit 1\n", ("--kill-on-invalid-dep=no",)),
        )
        for scenario, parent_body, flags in cases:
            parent = self.sbatch(f"{scenario}_parent", parent_body, "--hold", save=False)
            self.sbatch(scenario, "true\n", f"--dependency=afterok:{parent}", *flags)
        for scenario, _, _ in cases:
            self.wait_live(scenario, "PENDING", "Dependency")
            self.stage(scenario, "its parent held, not yet run", "before", squeue=True)
        for scenario, _, _ in cases:
            self.cluster.run(["scontrol", "release", self.jobs[f"{scenario}_parent"]])
        self.wait_ended("dependent_ok", "COMPLETED")
        self.stage("dependent_ok", "its parent exited 0, then it ran")
        self.wait_ended("dependent_skipped", "CANCELLED")
        self.stage(
            "dependent_skipped", "its parent exited 1; cancelled by --kill-on-invalid-dep=yes"
        )
        self.wait_live("dependent_pending", "PENDING", "DependencyNeverSatisfied")
        self.stage(
            "dependent_pending",
            "its parent exited 1; --kill-on-invalid-dep=no, so it pends for ever",
            squeue=True,
        )
        self.scancel("dependent_pending")
        self.wait_ended("dependent_pending", "CANCELLED")
        self.stage("dependent_pending", "then cancelled with scancel", "cancelled")

    def arrays(self) -> None:
        self.sbatch("array", "true\n", "--hold", "--array=1-4")
        self.sbatch("array_throttled", "sleep 600\n", "--array=1-4%2")
        self.wait_live("array", "PENDING", "JobHeldUser")
        self.stage("array", "four tasks, held", "pending", squeue=True, tasks=True)
        self.cluster.run(["scontrol", "release", self.jobs["array"]])

        throttled = self.jobs["array_throttled"]
        self.wait(
            "two array_throttled tasks running",
            lambda: (
                sorted(s for s, _ in self.live(throttled))
                == ["PENDING", "PENDING", "RUNNING", "RUNNING"]
            ),
        )
        self.wait_sacct(
            "array_throttled",
            lambda rows: sorted(states(rows)) == ["PENDING", "RUNNING", "RUNNING"],
        )
        self.stage(
            "array_throttled",
            "--array=1-4%2, two tasks running and two pending",
            squeue=True,
            tasks=True,
        )
        self.scancel("array_throttled")
        self.wait("array_throttled leaving squeue", lambda: not self.live(throttled))
        self.wait_sacct("array_throttled", lambda rows: set(states(rows)) == {"CANCELLED"})
        self.stage("array_throttled", "then cancelled with scancel", "cancelled")

        self.wait_ended("array", "COMPLETED", tasks=4)
        self.stage("array", "four tasks finished")

        for scenario, spec in (
            ("array_list", "1,3,7-9:2"),
            ("array_stepped", "1-100:3"),
            ("array_single", "5"),
        ):
            self.sbatch(scenario, "true\n", "--hold", f"--array={spec}")
            self.wait_live(scenario, "PENDING", "JobHeldUser")
            self.stage(scenario, f"--array={spec}, held", squeue=True)
            self.cluster.run(["scancel", self.jobs[scenario]])

    def timeout_submit(self) -> None:
        """Killed about 80 s later; on exec2, away from the node ``node_fail`` kills."""
        self.sbatch("timeout", "sleep 600\n", "--time=1", "-w", "exec2")

    def timeout_collect(self) -> None:
        self.wait_ended("timeout", "TIMEOUT", timeout=240)
        self.stage("timeout", "--time=1, killed at the limit")

    def requeue(self) -> None:
        """On exec2 too. It may start again two minutes after the requeue."""
        job_id = self.sbatch("requeued", "sleep 600\n", "-w", "exec2")
        self.wait_live("requeued", "RUNNING")
        proc = self.capture_result(
            "scontrol_requeue.json",
            ["scontrol", "requeue", job_id],
            "scontrol requeue of the running requeued job",
        )
        assert proc.returncode == 0, proc.stderr
        self.wait_live("requeued", "PENDING")
        self.wait_sacct(
            "requeued",
            lambda rows: states(rows) == ["REQUEUED", "PENDING"],
            duplicates=True,
        )
        self.stage(
            "requeued",
            "requeued while running, pending again",
            squeue=True,
            duplicates=True,
        )

    def requeue_collect(self) -> None:
        self.wait(
            "requeued running again",
            lambda: self.live(self.jobs["requeued"]) == [("RUNNING", "None")],
            timeout=240,
        )
        self.wait_sacct("requeued", lambda rows: states(rows) == ["RUNNING"])
        self.stage(
            "requeued",
            "requeued while running, now running a second time",
            "running",
            squeue=True,
            duplicates=True,
        )
        self.cluster.run(["scancel", self.jobs["requeued"]])

    def node_fail(self) -> None:
        self.sbatch("node_fail", "sleep 600\n", "--no-requeue", "-w", "exec1")
        self.sbatch("node_fail_requeued", "sleep 600\n", "-w", "exec1")
        self.wait_live("node_fail", "RUNNING")
        self.wait_live("node_fail_requeued", "RUNNING")
        self.cluster.kill_node("exec1")
        try:
            self.wait_sacct("node_fail", lambda rows: states(rows) == ["NODE_FAIL"], timeout=180)
            self.wait_sacct(
                "node_fail_requeued",
                lambda rows: states(rows) == ["NODE_FAIL", "PENDING"],
                timeout=180,
                duplicates=True,
            )
            self.stage("node_fail", "--no-requeue, its node killed")
            self.stage(
                "node_fail_requeued",
                "its node killed; the controller requeued it",
                squeue=True,
                duplicates=True,
            )
        finally:
            self.cluster.start_node("exec1")
        self.cluster.run(["scancel", self.jobs["node_fail_requeued"]])
        self.cluster.wait_ready(timeout=180)

    def snapshot(self) -> None:
        """Running, suspended, held and pending-for-resources jobs, alive together.

        exec1 holds ``running`` and ``suspended``, a two-CPU filler holds
        exec2. ``pending_resources`` asks for two CPUs: a request the cluster
        could meet but cannot now, even with the one CPU a suspended job
        gives back.
        """
        self.sbatch("running", "sleep 600\n", "-w", "exec1")
        self.sbatch("suspended", "sleep 600\n", "-w", "exec1")
        self.sbatch("filler", "sleep 600\n", "-w", "exec2", "-c", "2", save=False)
        self.sbatch("held", "sleep 600\n", "--hold")
        for scenario in ("running", "suspended", "filler"):
            self.wait_live(scenario, "RUNNING")
        self.wait_live("held", "PENDING", "JobHeldUser")
        self.sbatch("pending_resources", "sleep 600\n", "-c", "2")
        self.wait_live("pending_resources", "PENDING", "Resources")
        proc = self.capture_result(
            "scontrol_suspend.json",
            ["scontrol", "suspend", self.jobs["suspended"]],
            "scontrol suspend, as root, of the running suspended job",
            user="root",
        )
        assert proc.returncode == 0, proc.stderr
        self.wait_live("suspended", "SUSPENDED")
        self.wait_live("pending_resources", "PENDING", "Resources")
        self.stage("running", "running", squeue=True)
        self.stage("suspended", "suspended with scontrol suspend", squeue=True)
        self.stage("held", "submitted with --hold", squeue=True)
        self.stage("pending_resources", "two CPUs asked, no node has two free", squeue=True)
        self.capture(
            "squeue_all.txt",
            squeue_argv(),
            "squeue of every job: running, suspended, held, pending for resources, and a filler",
        )
        argv = history_argv("tester", self.began)
        self.capture(
            "sacct_history.txt",
            argv,
            "sacct of every job of tester since the capture began, the snapshot's jobs live",
        )
        self.cluster.run(["scontrol", "resume", self.jobs["suspended"]], user="root")

    def rejected(self) -> None:
        script = self.script("rejected", "true\n")
        for scenario, flag in (
            ("bad_partition", "--partition=nosuch"),
            ("bad_account", "--account=nosuch"),
        ):
            what = f"of a job with {flag}"
            self.capture_result(
                f"sbatch_{scenario}.json", sbatch_argv(scenario, script, flag), f"sbatch {what}"
            )
            argv = ["srun", "-J", scenario, f"--chdir={JOBS_DIR}", flag, script]
            self.capture_result(f"srun_{scenario}.json", argv, f"srun {what}")

    def run(self) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        for old in self.out.iterdir():
            if old.is_file():
                old.unlink()
        self.cluster.clean(JOBS_DIR)
        JOBS_DIR.mkdir()
        self.empty()
        self.timeout_submit()
        self.requeue()
        self.stubs()
        self.finished_jobs()
        self.cancelled()
        self.completing()
        self.not_owner()
        self.waited()
        self.dependents()
        self.arrays()
        self.node_fail()
        self.timeout_collect()
        self.requeue_collect()
        self.snapshot()
        self.rejected()
        self.cluster.clean(JOBS_DIR)
        version = self.cluster.run(["sinfo", "--version"])
        manifest = {
            "captured": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "flavour": self.cluster.flavour,
            "scheduler": version.stdout.strip(),
            "sacct_format": SACCT_FORMAT,
            "squeue_format": SQUEUE_FORMAT,
            "history_since": self.began,
            "jobs": self.jobs,
            "files": self.files,
        }
        (self.out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        self.log(f"wrote {len(self.files) + 1} files to {self.out}")


def _as_json(argv: Sequence[str], proc: subprocess.CompletedProcess[str]) -> str:
    return (
        json.dumps(
            {
                "argv": list(argv),
                "returncode": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            },
            indent=2,
        )
        + "\n"
    )


def main(argv: list[str]) -> int:
    out = Path(argv[1]) if len(argv) > 1 else WORK_ROOT / "capture"
    flavour = argv[2] if len(argv) > 2 else "24.11"
    Capture(SlurmCluster(flavour=flavour, workdir=out), out).run()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
