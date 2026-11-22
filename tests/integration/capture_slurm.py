"""Capture real Slurm output from the testbed as unit-test fixtures.

Run inside ``login`` by ``hpc-testbed --scheduler slurm capture`` (``python -m
tests.integration.capture_slurm OUTDIR FLAVOUR``); the driver copies OUTDIR to
``tests/unit/fixtures/slurm/<flavour>/``. The SGE counterpart is
``tests/integration/capture.py``. Regenerate on demand; job ids and
timestamps change every capture, structure does not.

Scenarios, all submitted with the raw CLI so the texts do not depend on
hpc-runner's own rendering:

* ``ok``: a finished job, exit 0 (``sbatch`` stdout, ``scontrol show job``
  while the controller still knows the job, ``sacct -P -n -j <id>
  --format=...``).

The rest of the list is docs/dev/slurm/PLAN.md, "Captures to take".

``manifest.json`` records the job id of every scenario and, for each file,
what it is and the argv that produced it.
"""

from __future__ import annotations

import json
import re
import sys
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tests.integration.cluster import CLEAN_SCRIPT, WORK_ROOT, SlurmCluster

#: The ``sacct`` columns every capture is taken with.
SACCT_FORMAT = "JobID,JobName,User,Partition,State,ExitCode,Submit,Start,End,Elapsed,NodeList"
ACCOUNTING_WAIT = 60.0
POLL = 0.5


class Capture:
    def __init__(self, cluster: SlurmCluster, out: Path) -> None:
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

    def capture(self, name: str, argv: Sequence[str], what: str) -> None:
        """Run *argv* and save its stdout as *name*."""
        self.save(name, self.cluster.run(list(argv)).stdout, what, argv)

    def script(self, name: str, body: str) -> Path:
        """A job script in the output directory, output discarded."""
        path = self.out / name
        path.write_text(f"#!/bin/bash\n#SBATCH -o /dev/null\n{body}")
        return path

    def sbatch(self, scenario: str, script: Path, *args: str, save: str | None = None) -> str:
        """``sbatch`` *args* *script*; record and return the job id.

        With *save*, sbatch's stdout is kept as that file.
        """
        argv = ["sbatch", "-J", scenario, *args, str(script)]
        proc = self.cluster.run(argv)
        match = re.fullmatch(r"Submitted batch job (\d+)\n", proc.stdout)
        assert match, proc.stdout
        self.jobs[scenario] = match.group(1)
        self.log(f"{scenario}: job {match.group(1)}")
        if save is not None:
            self.save(save, proc.stdout, f"sbatch stdout for the {scenario} job", argv)
        return match.group(1)

    def sacct_argv(self, job_id: str) -> list[str]:
        return ["sacct", "-P", "-n", "-j", job_id, f"--format={SACCT_FORMAT}"]

    def wait_finished(self, job_id: str, records: int = 1) -> None:
        deadline = time.monotonic() + ACCOUNTING_WAIT
        while self.cluster.accounting_records(job_id) < records:
            assert time.monotonic() < deadline, f"job {job_id} has not ended in sacct"
            time.sleep(POLL)

    # -- scenarios ------------------------------------------------------------

    def ok(self) -> None:
        script = self.script("ok.sh", "true\n")
        job_id = self.sbatch("ok", script, save="sbatch.txt")
        self.wait_finished(job_id)
        self.capture(
            "scontrol_ok.txt",
            ["scontrol", "show", "job", job_id],
            "scontrol show job of a job that exited 0, the controller still holding it",
        )
        self.capture("sacct_ok.txt", self.sacct_argv(job_id), "sacct of a job that exited 0")

    def run(self) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        for old in self.out.iterdir():
            if old.is_file():
                old.unlink()
        self.cluster.run([str(CLEAN_SCRIPT)])
        self.ok()
        self.cluster.run([str(CLEAN_SCRIPT)])
        # The job scripts, and anything a job wrote here: only captures are kept.
        for path in self.out.iterdir():
            if path.is_file() and path.name not in self.files:
                path.unlink()
        version = self.cluster.run(["sinfo", "--version"])
        manifest = {
            "captured": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "flavour": self.cluster.flavour,
            "scheduler": version.stdout.strip(),
            "sacct_format": SACCT_FORMAT,
            "jobs": self.jobs,
            "files": self.files,
        }
        (self.out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        self.log(f"wrote {len(self.files) + 1} files to {self.out}")


def main(argv: list[str]) -> int:
    out = Path(argv[1]) if len(argv) > 1 else WORK_ROOT / "capture"
    flavour = argv[2] if len(argv) > 2 else "24.11"
    Capture(SlurmCluster(flavour=flavour, workdir=out), out).run()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
