"""Capture real scheduler output from the testbed as unit-test fixtures.

Run inside ``login`` by ``hpc-testbed capture`` (``python -m
tests.integration.capture OUTDIR``); the driver copies OUTDIR to
``tests/unit/fixtures/sge/``. The unit suite's ``mock_sge_commands`` and the
parser tests read those files, so the canned text the unit tests see is what
gridengine actually emits (test QA review, decision 5b). Regenerate on demand,
like the goldens; job ids and timestamps change every capture, structure does
not.

Scenarios (one job each unless noted), all submitted with the raw CLI so the
texts do not depend on hpc-runner's own rendering:

* ``ok`` / ``exit1``: finished jobs, exit 0 and 1 (``qacct -j``).
* ``array_done``: a finished three-task array, task 3 exited 1 (``qacct -j <id>``
  and ``qacct -j <id> -t 2``).
* ``signal``: a running job deleted with ``qdel`` (``exit_status 137 (Killed)``,
  ``failed 100``; also ``qdel``'s stdout).
* ``lost_node``: a job whose node is killed (``failed 19``, ``exit_status 0``).
* ``skipped``: a job that exits 113, the dependency guard's skip code.
* ``bob``: a job of another user (``qacct -j -o bob -b <since>``, with the
  trailing owner summary header, rule and row, BUG-T2).
* ``timeout``: a ``short.q`` job (``h_rt`` one minute) killed by qmaster
  (``failed 37``, ``exit_status 137``). Submitted first, collected last.
* ``vmem``: a 200 MB allocation under ``-l h_vmem=50M``; the limit is a
  setrlimit, so the process fails by itself (``failed 0``, non-zero exit).
* ``deleted_pending``: a held job deleted with ``qdel`` before it ran: no
  accounting record ever (``qacct`` "not found", JSON).
* ``suspended``, ``queue_suspended``, ``requeued``: alive together for one
  snapshot (``qstat_lifecycle.xml``/``.txt``, ``qstat_j_suspended.xml``): ``qmod -sj`` (``s``), its
  queue instance suspended with ``qmod -sq`` (``S``), and a ``-r y`` job
  rescheduled with ``qmod -rj`` running again (``Rr``). The requeued job's
  ``qacct -j`` after ``qdel`` has two records, ``failed 25`` then its end.
* ``deleting``: the ``queue_suspended`` job deleted while its node is
  paused, so qstat holds it in ``dr`` (``qstat_deleting.xml``, and
  ``qdel``'s "registered for deletion" stdout).
* ``threshold``, ``requeue_pending``, ``deleting_transfer``: alive together
  for one snapshot (``qstat_transient.xml``/``.txt``, ``qstat_j_threshold.xml``): a queue suspend
  threshold (``T``), a rescheduled job hard-pinned to its host (``Rq``) and
  a job deleted while it cannot be delivered (``dt``).
* ``running``, ``held``, ``dependent``, ``eqw``, ``array``: alive together
  for one ``qstat -xml -u '*'`` (and plain ``qstat``) snapshot: ``r``,
  ``hqw`` (user hold and ``-hold_jid`` on the running job), ``Eqw`` and an
  array with running and queued tasks; ``qstat -j -xml`` of the running one.
* ``master_down``: ``qstat -xml -u '*'`` with the qmaster stopped (exit
  status, stdout, stderr as JSON).

``manifest.json`` records the job id of every scenario and, for each file,
what it is and the argv that produced it (``hpc trace compare --fixtures``
reads the argv to tell which call shapes the fixtures cover).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tests.integration.cluster import WORK_ROOT, SGECluster

QACCT_WAIT = 60.0
STATE_WAIT = 60.0
POLL = 0.5


class Capture:
    def __init__(self, cluster: SGECluster, out: Path) -> None:
        self.cluster = cluster
        self.out = out
        self.jobs: dict[str, str] = {}
        self.files: dict[str, dict[str, Any]] = {}

    # -- helpers --------------------------------------------------------------

    def log(self, message: str) -> None:
        print(f"capture: {message}", file=sys.stderr, flush=True)

    def save(self, name: str, text: str, what: str, argv: Sequence[str]) -> None:
        (self.out / name).write_text(text)
        self.files[name] = {"what": what, "argv": list(argv)}

    def capture(self, name: str, argv: Sequence[str], what: str, *, as_json: bool = False) -> None:
        """Run *argv* and save its stdout (or the whole result as JSON) as *name*."""
        proc = self.cluster.run(list(argv), check=not as_json)
        self.save(name, _as_json(proc) if as_json else proc.stdout, what, argv)

    def qsub(self, scenario: str, *args: str, user: str = "tester") -> str:
        """``qsub -b y`` *args* as *user*; record and return the job id."""
        argv = ["qsub", "-b", "y", "-N", scenario, "-o", "/dev/null", "-j", "y", *args]
        proc = self.cluster.run(argv, user=user)
        match = re.search(r"Your job(?:-array)? (\d+)", proc.stdout)
        assert match, proc.stdout
        self.jobs[scenario] = match.group(1)
        self.log(f"{scenario}: job {match.group(1)}")
        return match.group(1)

    def qsub_script(self, scenario: str, body: str, user: str = "tester") -> str:
        """``qsub`` a script whose text after the output directives is *body*."""
        path = self.out / f"{scenario}.sh"
        path.write_text(f"#!/bin/bash\n#$ -o /dev/null\n#$ -j y\n{body}")
        proc = self.cluster.run(["qsub", "-N", scenario, str(path)], user=user)
        match = re.search(r"Your job(?:-array)? (\d+)", proc.stdout)
        assert match, proc.stdout
        self.jobs[scenario] = match.group(1)
        self.log(f"{scenario}: job {match.group(1)}")
        return match.group(1)

    def script(self, name: str, directives: str) -> Path:
        """A job script in the output directory that runs ``true``, output discarded."""
        path = self.out / name
        path.write_text(f"#!/bin/bash\n#$ -o /dev/null\n#$ -j y\n{directives}true\n")
        return path

    def qstat_states(self) -> dict[str, str]:
        proc = self.cluster.run(["qstat", "-u", "*"], check=False)
        states: dict[str, str] = {}
        for line in proc.stdout.splitlines()[2:]:
            parts = line.split()
            if len(parts) >= 5 and parts[0].isdigit():
                states.setdefault(parts[0], parts[4])
        return states

    def queue_of(self, job_id: str) -> str | None:
        """The queue instance a running job is in, from plain ``qstat``."""
        for line in self.cluster.run(["qstat", "-u", "*"]).stdout.splitlines()[2:]:
            parts = line.split()
            if len(parts) >= 8 and parts[0] == job_id:
                return parts[7]
        return None

    def wait_state(self, job_id: str, want: set[str], timeout: float = STATE_WAIT) -> str:
        deadline = time.monotonic() + timeout
        while True:
            state = self.qstat_states().get(job_id)
            if state is not None and state in want:
                return state
            assert time.monotonic() < deadline, f"job {job_id} is {state!r}, wanted {want}"
            time.sleep(POLL)

    def wait_gone(self, job_id: str, timeout: float = STATE_WAIT) -> None:
        deadline = time.monotonic() + timeout
        while job_id in self.qstat_states():
            assert time.monotonic() < deadline, f"job {job_id} still in qstat"
            time.sleep(POLL)

    def qacct(self, job_id: str, *args: str, records: int = 1, timeout: float = QACCT_WAIT) -> str:
        """``qacct -j <id> [args]`` once *records* records exist (one per array task)."""
        deadline = time.monotonic() + timeout
        while True:
            proc = self.cluster.run(["qacct", "-j", job_id, *args], check=False)
            if proc.returncode == 0 and proc.stdout.count("jobnumber") >= records:
                return proc.stdout
            assert time.monotonic() < deadline, f"no qacct record for {job_id}: {proc.stderr}"
            time.sleep(POLL)

    # -- scenarios ------------------------------------------------------------

    def finished_jobs(self) -> None:
        ok = self.qsub("ok", "true")
        exit1 = self.qsub("exit1", "false")
        # Task 3 exits 1, so `qacct -j <base>` has records that disagree: the
        # base-id readers must reduce over them, not take the last printed.
        # A script, not `-b y`: the task id is only known inside the job.
        array = self.qsub_script("array_done", '#$ -t 1-3\ntest "$SGE_TASK_ID" -ne 3\n')
        # Non-zero wallclock, so `qacct -o bob` ends with bob's summary row
        # (BUG-T2); an owner whose jobs all ran in 0s gets the header and rule only.
        bob = self.qsub("bob", "sleep", "2", user="bob")
        # The guard's skip code (core/job.py SKIP_EXIT_CODE); a script, since
        # `-b y sh -c "exit 113"` loses the quoting.
        skipped = self.qsub_script("skipped", "exit 113\n")
        # Submitted as scripts, the way hpc-runner submits (`qsub <script>`), so
        # the recorded argv has the shape `hpc trace compare` sees in a trace.
        plain_script = self.script("qsubtxt.sh", "")
        array_script = self.script("qsubarr.sh", "#$ -t 1-4\n")
        self.capture("qsub.txt", ["qsub", str(plain_script)], "qsub stdout for a plain job")
        self.capture("qsub_array.txt", ["qsub", str(array_script)], "qsub stdout for an array job")
        self.save(
            "qacct_ok.txt", self.qacct(ok), "qacct -j of a job that exited 0", ["qacct", "-j", ok]
        )
        self.save(
            "qacct_exit1.txt",
            self.qacct(exit1),
            "qacct -j of a job that exited 1",
            ["qacct", "-j", exit1],
        )
        self.save(
            "qacct_array_task.txt",
            self.qacct(array, "-t", "2"),
            "qacct -j <id> -t 2 of a finished array (task 2 exited 0)",
            ["qacct", "-j", array, "-t", "2"],
        )
        self.save(
            "qacct_array_base.txt",
            self.qacct(array, records=3),
            "qacct -j <id> of a finished three-task array: one record per task, task 3 exited 1",
            ["qacct", "-j", array],
        )
        self.save(
            "qacct_skipped.txt",
            self.qacct(skipped),
            "qacct -j of a job that exited 113 (the dependency guard's skip code)",
            ["qacct", "-j", skipped],
        )
        self.qacct(bob)
        # With -b, as `hpc status --history` asks (list_completed_jobs).
        self.capture(
            "qacct_owner_bob.txt",
            ["qacct", "-j", "-o", "bob", "-b", "200001010000"],
            "qacct -j -o bob -b <since>: bob's records then the owner summary block (BUG-T2)",
        )

    def signal(self) -> None:
        job_id = self.qsub("signal", "sleep", "300")
        self.wait_state(job_id, {"r"})
        self.capture("qdel.txt", ["qdel", job_id], "qdel stdout")
        self.wait_gone(job_id)
        self.save(
            "qacct_signal.txt",
            self.qacct(job_id),
            "qacct -j of a running job deleted with qdel (exit_status 137 (Killed), BUG-T5)",
            ["qacct", "-j", job_id],
        )

    def lost_node(self) -> None:
        job_id = self.qsub("lost_node", "-l", "hostname=exec1", "sleep", "300")
        self.wait_state(job_id, {"r"})
        self.cluster.kill_node("exec1")
        time.sleep(2)
        self.cluster.start_node("exec1")
        self.wait_gone(job_id, timeout=120)
        self.cluster.wait_ready()
        self.save(
            "qacct_lost_node.txt",
            self.qacct(job_id),
            "qacct -j of a job whose node was killed (failed 19, BUG-T3)",
            ["qacct", "-j", job_id],
        )

    def timeout_submit(self) -> str:
        """The ``h_rt`` job; qmaster kills it a minute after it starts.

        Pinned to exec2 and collected before ``lost_node`` (kills exec1) and
        ``lifecycle`` (pauses exec2), so nothing but the limit ends it.
        """
        return self.qsub("timeout", "-q", "short.q", "-l", "hostname=exec2", "sleep", "120")

    def timeout_collect(self, job_id: str) -> None:
        self.save(
            "qacct_timeout.txt",
            self.qacct(job_id, timeout=180),
            "qacct -j of a short.q job killed at h_rt (failed 37, exit_status 137)",
            ["qacct", "-j", job_id],
        )

    def vmem(self) -> None:
        job_id = self.qsub(
            "vmem", "-l", "h_vmem=50M", "python3", "-c", "b = bytearray(200 * 1024 * 1024)"
        )
        self.save(
            "qacct_vmem.txt",
            self.qacct(job_id),
            "qacct -j of a 200 MB allocation under h_vmem=50M (failed 0, non-zero exit)",
            ["qacct", "-j", job_id],
        )

    def deleted_pending(self) -> None:
        job_id = self.qsub("deleted_pending", "-h", "sleep", "600")
        self.wait_state(job_id, {"hqw"})
        self.capture("qdel_pending.txt", ["qdel", job_id], "qdel stdout for a job that never ran")
        self.wait_gone(job_id)
        # qmaster flushes accounting within a second on this stack (the other
        # scenarios' records appear in under one); wait well past that so
        # "not found" means no record, not "not yet".
        time.sleep(5)
        self.capture(
            "qacct_deleted_pending.txt",
            ["qacct", "-j", job_id],
            "qacct -j of a job deleted while pending: no record is ever written (JSON)",
            as_json=True,
        )

    def lifecycle(self) -> None:
        """Suspended, queue-suspended, requeued, then deleting (``dr``)."""
        susp = self.qsub("suspended", "-l", "hostname=exec1", "sleep", "600")
        qsusp = self.qsub("queue_suspended", "-l", "hostname=exec2", "-q", "all.q", "sleep", "600")
        # Starts on exec2 (soft request) and must be free to come back on exec1:
        # SGE will not rerun a rescheduled job on a host until that host has
        # cleaned up its previous run, so a hard pin leaves it in Rq.
        requeued = self.qsub(
            "requeued", "-soft", "-l", "hostname=exec2", "-hard", "-r", "y", "sleep", "600"
        )
        for job_id in (susp, qsusp, requeued):
            self.wait_state(job_id, {"r"})
        assert self.queue_of(requeued) == "all.q@exec2", self.queue_of(requeued)
        self.cluster.run(["qmod", "-rj", requeued], user="root")
        self.wait_state(requeued, {"Rr"})
        assert self.queue_of(requeued) == "all.q@exec1", self.queue_of(requeued)
        self.cluster.run(["qmod", "-sj", susp])
        self.cluster.run(["qmod", "-sq", "all.q@exec2"], user="root")
        try:
            self.wait_state(susp, {"s"})
            self.wait_state(qsusp, {"S"})
            self.capture(
                "qstat_lifecycle.xml",
                ["qstat", "-xml", "-u", "*"],
                "qstat -xml -u '*': user-suspended (s), queue-suspended (S), requeued (Rr)",
            )
            self.capture(
                "qstat_lifecycle.txt", ["qstat", "-u", "*"], "plain qstat -u '*' of the same moment"
            )
            self.capture(
                "qstat_j_suspended.xml",
                ["qstat", "-j", susp, "-xml"],
                "qstat -j -xml of the user-suspended job",
            )
        finally:
            self.cluster.run(["qmod", "-usq", "all.q@exec2"], user="root")
        self.wait_state(qsusp, {"r"})

        # A paused execd cannot confirm the kill, so qstat holds the job in dr.
        self.cluster.run(["sudo", "docker", "pause", "exec2"])
        try:
            self.capture("qdel_deleting.txt", ["qdel", qsusp], "qdel stdout when execd is away")
            self.jobs["deleting"] = qsusp
            self.wait_state(qsusp, {"dr"})
            self.capture(
                "qstat_deleting.xml",
                ["qstat", "-xml", "-u", "*"],
                "qstat -xml -u '*' with a deleted job still in dr (its node paused)",
            )
        finally:
            self.cluster.run(["sudo", "docker", "unpause", "exec2"])
        self.wait_gone(qsusp)

        self.cluster.run(["qdel", requeued, susp])
        self.wait_gone(requeued)
        self.save(
            "qacct_requeued.txt",
            self.qacct(requeued, records=2),
            "qacct -j of a rescheduled job: failed 25 (rescheduling), then its end (qdel)",
            ["qacct", "-j", requeued],
        )
        self.wait_gone(susp)

    def transient_states(self) -> None:
        """``T``, ``Rq`` and ``dt`` alive together for one snapshot.

        * ``T``: a ``short.q@exec1`` job, then that queue instance gets
          ``suspend_thresholds np_load_avg=0.0`` (``np_load_avg`` compares
          with ``>=``, so it always trips). Set after the job starts: a queue
          in suspend alarm dispatches nothing.
        * ``Rq``: a ``-r y`` job hard-pinned to exec1, rescheduled with
          ``qmod -rj``. SGE will not rerun it on exec1 until exec1 has
          cleaned up the previous run, so it stays ``Rq``.
        * ``dt``: a held job pinned to exec2; exec2 is paused, the hold is
          released, the job is dispatched but cannot be delivered (``t``),
          then deleted (``dt``).
        """
        threshold = self.qsub("threshold", "-q", "short.q", "-l", "hostname=exec1", "sleep", "600")
        requeue = self.qsub(
            "requeue_pending", "-q", "all.q", "-l", "hostname=exec1", "-r", "y", "sleep", "600"
        )
        transfer = self.qsub(
            "deleting_transfer", "-h", "-q", "all.q", "-l", "hostname=exec2", "sleep", "600"
        )
        for job_id in (threshold, requeue):
            self.wait_state(job_id, {"r"})
        self.wait_state(transfer, {"hqw"})
        self.cluster.run(["qmod", "-rj", requeue], user="root")
        self.wait_state(requeue, {"Rq"})
        self.cluster.run(
            ["qconf", "-mattr", "queue", "suspend_thresholds", "np_load_avg=0.0", "short.q@exec1"],
            user="root",
        )
        try:
            self.wait_state(threshold, {"T"})
            self.cluster.run(["sudo", "docker", "pause", "exec2"])
            try:
                self.cluster.run(["qrls", transfer])
                self.wait_state(transfer, {"t"})
                self.cluster.run(["qdel", transfer])
                self.wait_state(transfer, {"dt"})
                states = self.qstat_states()
                assert states.get(requeue) == "Rq" and states.get(threshold) == "T", states
                self.capture(
                    "qstat_transient.xml",
                    ["qstat", "-xml", "-u", "*"],
                    "qstat -xml -u '*': threshold-suspended (T), requeued waiting (Rq), "
                    "deleted while transferring (dt)",
                )
                self.capture(
                    "qstat_transient.txt",
                    ["qstat", "-u", "*"],
                    "plain qstat -u '*' of the same moment",
                )
                self.capture(
                    "qstat_j_threshold.xml",
                    ["qstat", "-j", threshold, "-xml"],
                    "qstat -j -xml of the threshold-suspended job",
                )
            finally:
                self.cluster.run(["sudo", "docker", "unpause", "exec2"])
        finally:
            self.cluster.run(
                ["qconf", "-purge", "queue", "suspend_thresholds", "short.q@exec1"], user="root"
            )
        self.cluster.run(["qdel", threshold, requeue], check=False)
        for job_id in (threshold, requeue, transfer):
            self.wait_gone(job_id)

    def snapshot(self) -> None:
        running = self.qsub("running", "sleep", "600")
        self.qsub("held", "-h", "sleep", "600")
        self.qsub("dependent", "-hold_jid", running, "sleep", "1")
        eqw = self.qsub("eqw", "-wd", "/nonexistent", "sleep", "1")
        array = self.qsub("array", "-t", "1-10", "sleep", "600")
        self.wait_state(running, {"r"})
        self.wait_state(eqw, {"Eqw"})
        deadline = time.monotonic() + STATE_WAIT
        while True:  # some tasks running, some still queued
            tasks = [
                line.split()[4]
                for line in self.cluster.run(["qstat", "-u", "*"]).stdout.splitlines()[2:]
                if line.split() and line.split()[0] == array
            ]
            if "r" in tasks and "qw" in tasks:
                break
            assert time.monotonic() < deadline, tasks
            time.sleep(POLL)
        self.capture(
            "qstat_mixed.xml",
            ["qstat", "-xml", "-u", "*"],
            "qstat -xml -u '*': running, hqw (user hold, -hold_jid), Eqw, array with r and qw",
        )
        self.capture(
            "qstat_mixed.txt", ["qstat", "-u", "*"], "plain qstat -u '*' of the same moment"
        )
        self.capture(
            "qstat_j_running.xml",
            ["qstat", "-j", running, "-xml"],
            "qstat -j <id> -xml of the running job",
        )
        self.capture(
            "qstat_j_missing.txt",
            ["qstat", "-j", "999999", "-xml"],
            "qstat -j of an unknown id (JSON: returncode, stdout, stderr)",
            as_json=True,
        )

    def empty(self) -> None:
        self.capture(
            "qstat_empty.xml", ["qstat", "-xml", "-u", "*"], "qstat -xml -u '*' with nothing queued"
        )
        self.capture(
            "qacct_missing.txt",
            ["qacct", "-j", "999999"],
            "qacct -j of an unknown id (JSON)",
            as_json=True,
        )

    def master_down(self) -> None:
        self.cluster.stop_master()
        try:
            proc = self.cluster.run(["qstat", "-xml", "-u", "*"], check=False, timeout=120)
            self.save(
                "qstat_master_down.json",
                _as_json(proc),
                "qstat -xml -u '*' with the qmaster stopped (JSON, BUG-T6)",
                proc.args,
            )
        finally:
            self.cluster.start_master()
            self.cluster.wait_ready()

    def run(self) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        for old in self.out.iterdir():
            if old.is_file():
                old.unlink()
        self.cluster.run([str(Path("/opt/testbed/clean.sh"))])
        self.empty()
        timeout = self.timeout_submit()
        self.finished_jobs()
        self.vmem()
        self.deleted_pending()
        self.signal()
        self.timeout_collect(timeout)
        self.lost_node()
        self.lifecycle()
        self.transient_states()
        self.snapshot()
        self.master_down()
        self.cluster.run([str(Path("/opt/testbed/clean.sh"))])
        for script in self.out.glob("*.sh"):
            script.unlink()
        version = self.cluster.run(["qstat", "-help"], check=False)
        manifest = {
            "captured": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "flavour": self.cluster.flavour,
            "scheduler": (version.stdout or version.stderr).splitlines()[0].strip(),
            "jobs": self.jobs,
            "files": self.files,
        }
        (self.out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        self.log(f"wrote {len(self.files) + 1} files to {self.out}")


def _as_json(proc: subprocess.CompletedProcess[str]) -> str:
    return (
        json.dumps(
            {
                "argv": proc.args,
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
    flavour = argv[2] if len(argv) > 2 else "soge"
    cluster = SGECluster(flavour=flavour, workdir=out)
    Capture(cluster, out).run()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
