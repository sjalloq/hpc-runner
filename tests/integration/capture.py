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
* ``bob``: a job of another user (``qacct -j -o bob -b <since>``, with the
  trailing owner summary header, rule and row, BUG-T2).
* ``running``, ``held``, ``eqw``, ``array``: alive together for one
  ``qstat -xml -u '*'`` (and plain ``qstat``) snapshot: ``r``, ``hqw``,
  ``Eqw`` and an array with running and queued tasks; ``qstat -j -xml`` of
  the running one.
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

    def snapshot(self) -> None:
        running = self.qsub("running", "sleep", "600")
        self.qsub("held", "-h", "sleep", "600")
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
            "qstat -xml -u '*': running, hqw, Eqw and an array with r and qw tasks",
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
        self.finished_jobs()
        self.signal()
        self.lost_node()
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
