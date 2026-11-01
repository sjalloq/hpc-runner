"""Test doubles for the scheduler subprocess seam."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from unittest.mock import patch

from hpc_runner.core.config import HPCConfig
from hpc_runner.core.exceptions import SchedulerCommandError
from hpc_runner.core.host import Host
from hpc_runner.core.job import Job
from hpc_runner.core.resolve import resolve
from hpc_runner.core.spec import JobSpec
from hpc_runner.trace import recorder

SEAM = "hpc_runner.schedulers._subprocess.run_command"

Responder = Callable[[list[str]], tuple[int, str, str]]


def make_fake_run_command(respond: Responder) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Build a stand-in for ``run_command``.

    *respond* maps an argv to ``(returncode, stdout, stderr)`` (or raises a
    SchedulerError itself, e.g. ``SchedulerUnavailableError``).  The fake
    honours ``check`` the way the real seam does, so non-zero exits reach
    scheduler code as ``SchedulerCommandError``, and hands every answer to
    the trace recorder the way the real seam does, so ``hpc --trace`` can be
    tested over the captured fixtures.
    """

    def fake(
        argv: Sequence[str],
        *,
        timeout: float | None,
        check: bool = True,
        capture: bool = True,
        cwd: Path | None = None,
        input: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        args = list(argv)
        rc, out, err = respond(args)
        recorder.record(args, returncode=rc, stdout=out, stderr=err, elapsed=0.0)
        if check and rc != 0:
            raise SchedulerCommandError(args, rc, out, err)
        return subprocess.CompletedProcess(args, rc, out, err)

    return fake


def patch_seam(respond: Responder) -> Any:
    """Patch the seam with a fake built from *respond*; yields the MagicMock."""
    return patch(SEAM, side_effect=make_fake_run_command(respond))


def make_host(
    env: dict[str, str] | None = None, cwd: Path | None = None, user: str = "alice"
) -> Host:
    """A fixed submit host, so nothing depends on the test runner's shell."""
    return Host(env=env or {}, cwd=cwd or Path("/work"), user=user)


def make_job(
    command: str | list[str] = "echo hello",
    *,
    config: HPCConfig | None = None,
    host: Host | None = None,
    **spec_fields: Any,
) -> Job:
    """Resolve a JobSpec against an (empty by default) config and fixed host."""
    spec = JobSpec(command, **spec_fields)
    return resolve(spec, config or HPCConfig(), host or make_host())


# =============================================================================
# Captured scheduler output (tests/unit/fixtures/<family>/)
# =============================================================================

FIXTURES_ROOT = Path(__file__).resolve().parent / "unit" / "fixtures"


class SGEFixtures:
    """The SGE texts captured from the testbed by ``hpc-testbed capture``.

    ``manifest.json`` maps each scenario (``running``, ``held``, ``eqw``,
    ``array``, ``ok``, ``exit1``, ``signal``, ``lost_node``, ``array_done``,
    ``bob``) to the job id it had when captured, so tests never hard-code an
    id; see ``tests/integration/capture.py`` for what each file is.
    """

    def __init__(self, root: Path = FIXTURES_ROOT / "sge") -> None:
        self.root = root
        self.manifest: dict[str, Any] = json.loads((root / "manifest.json").read_text())
        self.jobs: dict[str, str] = self.manifest["jobs"]

    def text(self, name: str) -> str:
        return (self.root / name).read_text()

    def result(self, name: str) -> tuple[int, str, str]:
        """A captured ``(returncode, stdout, stderr)`` stored as JSON."""
        data = json.loads(self.text(name))
        return data["returncode"], data["stdout"], data["stderr"]

    def job(self, scenario: str) -> str:
        return self.jobs[scenario]

    @property
    def submitted_id(self) -> str:
        """The id in ``qsub.txt`` (what a mocked ``qsub`` reports)."""
        return _must_match(r"Your job (\d+)", self.text("qsub.txt"))

    @property
    def submitted_array_id(self) -> str:
        return _must_match(r"Your job-array (\d+)", self.text("qsub_array.txt"))

    def qacct_record(self, job_id: str, task: str | None = None) -> str | None:
        """The captured ``qacct -j`` text for *job_id* (``-t`` *task*), or None."""
        by_scenario = {
            self.job("ok"): "qacct_ok.txt",
            self.job("exit1"): "qacct_exit1.txt",
            self.job("signal"): "qacct_signal.txt",
            self.job("lost_node"): "qacct_lost_node.txt",
            self.job("array_done"): "qacct_array_base.txt",
        }
        if task is not None:
            if job_id == self.job("array_done") and task == "2":
                return self.text("qacct_array_task.txt")
            return None
        name = by_scenario.get(job_id)
        return self.text(name) if name else None


def _must_match(pattern: str, text: str) -> str:
    match = re.search(pattern, text)
    assert match, (pattern, text)
    return match.group(1)


def make_sge_responder(fixtures: SGEFixtures) -> Responder:
    """A responder that answers SGE argv the way the captured testbed did.

    It selects by the *whole* argv, not the command name: ``qstat -xml -u
    '*'`` is the mixed snapshot, ``qstat -j <id> -xml`` the running job's
    details for its id and ``unknown_jobs`` for any other, ``qacct -j <id>``
    the record for a captured finished job and ``job id not found`` for the
    rest, ``qsub <script>`` the plain or array qsub text depending on whether
    the script has a ``#$ -t`` directive. Anything else is an AssertionError
    so a test that strays off the captured paths says so.
    """

    def respond(argv: list[str]) -> tuple[int, str, str]:
        cmd, args = argv[0], argv[1:]
        if cmd == "qsub":
            script = Path(args[-1]) if args else None
            is_array = (
                script is not None
                and script.is_file()
                and any(line.startswith("#$ -t ") for line in script.read_text().splitlines())
            )
            return 0, fixtures.text("qsub_array.txt" if is_array else "qsub.txt"), ""
        if cmd == "qstat":
            if args[:1] == ["-j"]:
                if args[1] == fixtures.job("running"):
                    return 0, fixtures.text("qstat_j_running.xml"), ""
                return fixtures.result("qstat_j_missing.txt")
            if "-xml" in args:
                return 0, fixtures.text("qstat_mixed.xml"), ""
            return 0, fixtures.text("qstat_mixed.txt"), ""
        if cmd == "qacct":
            if args[:2] == ["-j", "-o"]:
                if args[2] == "bob":
                    return 0, fixtures.text("qacct_owner_bob.txt"), ""
                return 1, "", f"error: owner {args[2]} not found\n"
            if args == ["-j"]:
                records = (
                    fixtures.text(n)
                    for n in ("qacct_ok.txt", "qacct_exit1.txt", "qacct_signal.txt")
                )
                return 0, "".join(records), ""
            task = args[args.index("-t") + 1] if "-t" in args else None
            record = fixtures.qacct_record(args[1], task)
            if record is None:
                rc, out, err = fixtures.result("qacct_missing.txt")
                return rc, out, err.replace("999999", args[1])
            return 0, record, ""
        if cmd == "qdel":
            if args[-1] in fixtures.jobs.values():
                return 0, fixtures.text("qdel.txt"), ""
            return 1, "", f'denied: job "{args[-1]}" does not exist\n'
        if cmd == "qrsh":
            return 0, "", ""
        raise AssertionError(f"no captured answer for {argv}")

    return respond
