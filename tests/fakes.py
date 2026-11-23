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
    tested over the captured fixtures. ``on_stdout_line`` gets each line of
    the answer's stdout before the call returns.
    """

    def fake(
        argv: Sequence[str],
        *,
        timeout: float | None,
        check: bool = True,
        capture: bool = True,
        cwd: Path | None = None,
        input: str | None = None,
        on_stdout_line: Callable[[str], None] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        args = list(argv)
        rc, out, err = respond(args)
        if on_stdout_line is not None:
            for line in out.splitlines(keepends=True):
                on_stdout_line(line)
        recorder.record(args, returncode=rc, stdout=out, stderr=err, elapsed=0.0)
        if check and rc != 0:
            raise SchedulerCommandError(args, rc, out, err)
        return subprocess.CompletedProcess(args, rc, out, err)

    return fake


def patch_seam(respond: Responder) -> Any:
    """Patch the seam with a fake built from *respond*; yields the MagicMock."""
    return patch(SEAM, side_effect=make_fake_run_command(respond))


class FakeClock:
    """A stand-in for the ``time`` module: ``sleep`` returns at once and moves the clock.

    ``sleeps`` is every sleep asked for, in order.
    """

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


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
# Captured scheduler output (tests/unit/fixtures/<scheduler>/)
# =============================================================================

FIXTURES_ROOT = Path(__file__).resolve().parent / "unit" / "fixtures"
#: Every SGE flavour with captures (``tests/unit/fixtures/sge/<flavour>/``).
SGE_FLAVOURS = tuple(
    sorted(d.name for d in (FIXTURES_ROOT / "sge").iterdir() if (d / "manifest.json").is_file())
)


#: Every Slurm flavour with captures (``tests/unit/fixtures/slurm/<flavour>/``).
SLURM_FLAVOURS = tuple(
    sorted(d.name for d in (FIXTURES_ROOT / "slurm").iterdir() if (d / "manifest.json").is_file())
)
#: Every ``(scheduler, flavour)`` with captures.
ALL_FLAVOURS = tuple(
    [("sge", flavour) for flavour in SGE_FLAVOURS]
    + [("slurm", flavour) for flavour in SLURM_FLAVOURS]
)


class Fixtures:
    """The texts one flavour's testbed wrote, captured by ``hpc-testbed capture``.

    ``manifest.json`` maps each scenario to the job id it had when captured,
    so tests never hard-code an id. The members below the constructor are
    what a test that is not about one scheduler asks, answered by
    :class:`SGEFixtures` and :class:`SlurmFixtures`.
    """

    scheduler: str = ""
    #: The first words of the argv that lists the live jobs.
    listing_command: tuple[str, ...] = ()
    #: The file holding the answer to that command in :func:`make_responder`.
    listing_file: str = ""
    #: The command that submits a script.
    submit_command: str = ""
    #: A directive line that a script has when ``queue = "all.q"`` is configured.
    queue_directive: str = ""
    #: Live jobs of :attr:`listing_file`: scenario to its label in ``hpc monitor``.
    labels: dict[str, str] = {}

    def __init__(self, flavour: str) -> None:
        self.flavour = flavour
        self.root = FIXTURES_ROOT / self.scheduler / flavour
        self.manifest: dict[str, Any] = json.loads((self.root / "manifest.json").read_text())
        self.jobs: dict[str, str] = self.manifest["jobs"]

    def text(self, name: str) -> str:
        return (self.root / name).read_text()

    def result(self, name: str) -> tuple[int, str, str]:
        """A captured ``(returncode, stdout, stderr)`` stored as JSON."""
        data = json.loads(self.text(name))
        return data["returncode"], data["stdout"], data["stderr"]

    def job(self, scenario: str) -> str:
        return self.jobs[scenario]

    def waited(self, scenario: str) -> str:
        """The scenario of the job of *scenario* submitted with the scheduler's own wait."""
        raise NotImplementedError

    def waited_answer(self, scenario: str) -> tuple[int, str, str]:
        """What the waiting submit command of :meth:`waited` returned."""
        raise NotImplementedError


class SGEFixtures(Fixtures):
    """One SGE flavour's captures; see ``tests/integration/capture.py`` for each file."""

    scheduler = "sge"
    listing_command = ("qstat", "-xml")
    listing_file = "qstat_mixed.xml"
    submit_command = "qsub"
    queue_directive = "#$ -q all.q"
    labels = {
        "running": "RUNNING",
        "held": "PENDING",
        "dependent": "PENDING",
        "eqw": "FAILED",
        "array": "RUNNING",
    }

    def __init__(self, flavour: str = "soge") -> None:
        super().__init__(flavour)

    def waited(self, scenario: str) -> str:
        return f"sync_{scenario}"

    def waited_answer(self, scenario: str) -> tuple[int, str, str]:
        return self.result(f"qsub_sync_{scenario}.json")

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
            self.job("timeout"): "qacct_timeout.txt",
            self.job("vmem"): "qacct_vmem.txt",
            self.job("requeued"): "qacct_requeued.txt",
            self.job("skipped"): "qacct_skipped.txt",
            **{
                self.job(f"sync_{scenario}"): f"qacct_sync_{scenario}.txt"
                for scenario in ("ok", "exit1", "exit137", "skipped", "array_partial")
            },
        }
        if task is not None:
            if job_id == self.job("array_done") and task == "2":
                return self.text("qacct_array_task.txt")
            if job_id == self.job("sync_array_partial") and task in ("1", "2", "3"):
                return self.text(f"qacct_sync_array_partial_task{task}.txt")
            return None
        name = by_scenario.get(job_id)
        return self.text(name) if name else None


def _must_match(pattern: str, text: str) -> str:
    match = re.search(pattern, text)
    assert match, (pattern, text)
    return match.group(1)


#: The jobs alive in ``qstat_mixed.xml``, each with a ``qstat_j_<scenario>.xml``.
MIXED_SNAPSHOT_JOBS = ("running", "held", "dependent", "eqw", "array")


def make_sge_responder(fixtures: SGEFixtures) -> Responder:
    """A responder that answers SGE argv the way the captured testbed did.

    It selects by the *whole* argv, not the command name: ``qstat -xml -u
    '*'`` is the mixed snapshot, ``qstat -j <id> -xml`` the details of a
    job alive in that snapshot and ``unknown_jobs`` for any other, ``qacct -j <id>``
    the record for a captured finished job and ``job id not found`` for the
    rest, ``qsub <script>`` the plain or array qsub text depending on whether
    the script has a ``#$ -t`` directive. Anything else is an AssertionError
    so a test that strays off the captured paths says so.
    """

    def respond(argv: list[str]) -> tuple[int, str, str]:
        cmd, args = argv[0], argv[1:]
        if cmd == "qsub" and args[:2] == ["-sync", "y"]:
            raise AssertionError(f"no captured answer for {argv}: the test names the waited job")
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
                # This responder is the moment of the mixed snapshot: only
                # the jobs alive in it have details. Other snapshots' jobs
                # finished later; their tests route their qstat -j themselves.
                for scenario in MIXED_SNAPSHOT_JOBS:
                    if args[1] == fixtures.job(scenario):
                        return 0, fixtures.text(f"qstat_j_{scenario}.xml"), ""
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
            if args == ["-j", fixtures.job("deleted_pending")]:
                return fixtures.result("qacct_deleted_pending.txt")
            task = args[args.index("-t") + 1] if "-t" in args else None
            record = fixtures.qacct_record(args[1], task)
            if record is None:
                rc, out, err = fixtures.result("qacct_missing.txt")
                return rc, out, err.replace("999999", args[1])
            return 0, record, ""
        if cmd == "qdel":
            if args[-1] == fixtures.job("deleting"):
                return 0, fixtures.text("qdel_deleting.txt"), ""
            if args[-1] == fixtures.job("deleted_pending"):
                return 0, fixtures.text("qdel_pending.txt"), ""
            if args[-1] in fixtures.jobs.values():
                return 0, fixtures.text("qdel.txt"), ""
            return 1, "", f'denied: job "{args[-1]}" does not exist\n'
        if cmd == "qrsh":
            return 0, "", ""
        raise AssertionError(f"no captured answer for {argv}")

    return respond


class SlurmFixtures(Fixtures):
    """One Slurm flavour's captures; see ``tests/integration/capture_slurm.py`` for each file."""

    scheduler = "slurm"
    listing_command = ("squeue", "-h")
    listing_file = "squeue_all.txt"
    submit_command = "sbatch"
    queue_directive = "#SBATCH --partition=all.q"
    labels = {
        "running": "RUNNING",
        "suspended": "SUSPEND",
        "held": "PENDING",
        "pending_resources": "PENDING",
        "filler": "RUNNING",
    }

    def sacct(self, job_ids: list[str]) -> str:
        """The settled ``sacct`` rows of each id in *job_ids* that is a scenario's job."""
        by_id = {job_id: scenario for scenario, job_id in self.jobs.items()}
        text = ""
        for job_id in job_ids:
            name = f"sacct_{by_id.get(job_id, '')}.txt"
            if name in self.manifest["files"]:
                text += self.text(name)
        return text

    def scontrol(self, job_id: str) -> tuple[int, str, str]:
        """``scontrol show job`` of *job_id*: the settled record of a scenario's job."""
        base = job_id.partition("_")[0]
        by_id = {job: scenario for scenario, job in self.jobs.items()}
        name = f"scontrol_{by_id.get(base, '')}.txt"
        if name not in self.manifest["files"]:
            return self.result("scontrol_missing.json")
        return 0, self.text(name), ""

    def waited(self, scenario: str) -> str:
        return f"wait_{scenario}"

    def waited_answer(self, scenario: str) -> tuple[int, str, str]:
        return self.result(f"sbatch_wait_{scenario}.json")


def make_slurm_responder(fixtures: SlurmFixtures) -> Responder:
    """A responder that answers Slurm argv the way the captured testbed did.

    By the whole argv: ``squeue -h [-u <user>] -o <format>`` is the snapshot
    of live jobs (``squeue_all.txt``); ``sacct ... -j <ids>`` the settled
    rows of each id that is a scenario's job, and nothing for another id, as
    ``sacct`` answers; ``sacct ... -S <time>`` the history capture;
    ``scontrol show job <id>`` a scenario job's settled record, and the
    captured refusal for another id; ``scancel <id>`` exits 0 in silence, as
    every captured ``scancel`` of one's own job did; ``sbatch <script>`` the
    plain or the array submission, by whether the script has an
    ``--array`` directive. Anything else is an AssertionError.
    """

    def respond(argv: list[str]) -> tuple[int, str, str]:
        cmd, args = argv[0], argv[1:]
        if cmd == "sbatch" and args[:1] != ["--wait"]:
            script = Path(args[-1])
            is_array = script.is_file() and "#SBATCH --array=" in script.read_text()
            return fixtures.result("sbatch_array.json" if is_array else "sbatch_ok.json")
        if cmd == "squeue" and args[:1] == ["-h"] and "-j" not in args:
            return 0, fixtures.text("squeue_all.txt"), ""
        if cmd == "sacct" and "-j" in args:
            return 0, fixtures.sacct(args[args.index("-j") + 1].split(",")), ""
        if cmd == "sacct" and "-S" in args:
            return 0, fixtures.text("sacct_history.txt"), ""
        if cmd == "scontrol" and args[:2] == ["show", "job"]:
            return fixtures.scontrol(args[2])
        if cmd == "scancel":
            return fixtures.result("scancel_cancelled_running.json")
        if cmd == "srun":
            return 0, "", ""
        raise AssertionError(f"no captured answer for {argv}")

    return respond


def make_fixtures(scheduler: str, flavour: str) -> Fixtures:
    """The captures of one scheduler's flavour."""
    return {"sge": SGEFixtures, "slurm": SlurmFixtures}[scheduler](flavour)


def make_responder(fixtures: Fixtures) -> Responder:
    """The responder for *fixtures*' scheduler (see the two ``make_*_responder``)."""
    if isinstance(fixtures, SGEFixtures):
        return make_sge_responder(fixtures)
    assert isinstance(fixtures, SlurmFixtures)
    return make_slurm_responder(fixtures)
