"""The table of scheduler against option.

Every option a scheduler has to place (``SCHEDULER_OPTIONS``) has a cell
for every implemented scheduler, for a batch job and for an interactive
one: the scheduler maps it, rejects it, or says why there is nothing to
map. Nothing is silently ignored. A new option, or a new scheduler, fails
this module until its cells are filled in and behave as they say.
"""

from __future__ import annotations

import dataclasses
import importlib.util
from pathlib import Path
from typing import Any

import pytest

from hpc_runner.core.config import HPCConfig
from hpc_runner.core.dependency import Dependency, DependencyType
from hpc_runner.core.exceptions import NotSupportedError
from hpc_runner.core.job import SCHEDULER_OPTIONS, Job
from hpc_runner.core.spec import JobSpec
from hpc_runner.schedulers import _SCHEDULERS, get_scheduler
from hpc_runner.schedulers.base import MAPS, REJECTS, BaseScheduler, NotApplicable
from tests.fakes import make_host, make_job

#: Carried by the job script the same way on every scheduler (the shared
#: environment block), so no scheduler has to map or reject them.
SCRIPT_OPTIONS = {
    "venv",
    "env_vars",
    "env_prepend",
    "env_append",
    "env_unset",
    "modules",
    "modules_path",
    "modules_path_append",
}
#: Not options: what to run, which config entry, which kind of job, and the
#: raw flags that are the way out of this table.
NOT_OPTIONS = {"command", "type", "interactive", "scheduler_args"}
IMPLEMENTED = sorted(
    name for name, path in _SCHEDULERS.items() if importlib.util.find_spec(path.split(":")[0])
)
KINDS = ("batch", "interactive")

#: A value for each option that differs from what a job has without it.
#: ``env_passthrough`` only matters with ``inherit_env`` off.
SAMPLES: dict[str, dict[str, Any]] = {
    "name": {"name": "sample_name"},
    "cpu": {"cpu": 3},
    "mem": {"mem": "2G"},
    "time": {"time": "1h"},
    "queue": {"queue": "sample.q"},
    "project": {"project": "sample_project"},
    "stdout": {"stdout": "sample.log"},
    "stderr": {"stderr": "sample.err"},
    "inherit_env": {"inherit_env": False},
    "env_passthrough": {"inherit_env": False, "env_passthrough": ["SAMPLE_VAR"]},
    "workdir": {"workdir": "/sample/dir"},
    "licenses": {"licenses": {"sample_lic": 2}},
    "devices": {"devices": {"sample_dev": 2}},
    "features": {"features": ["sample_feat"]},
    "array": {"array": "1-3"},
    "dependencies": {"dependencies": [Dependency("7", DependencyType.AFTERANY)]},
}
#: What the job is compared with: the sample minus the option itself.
BASELINES: dict[str, dict[str, Any]] = {"env_passthrough": {"inherit_env": False}}


@pytest.fixture
def scheduler(request, monkeypatch, tmp_path) -> BaseScheduler:
    monkeypatch.setenv("HPC_SCRIPT_DIR", str(tmp_path / "scripts"))
    monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path / "exit"))
    return get_scheduler(request.param, config=HPCConfig())


def _table(scheduler: BaseScheduler, kind: str) -> Any:
    return scheduler.INTERACTIVE_OPTIONS if kind == "interactive" else scheduler.BATCH_OPTIONS


def _job(kind: str, **fields: Any) -> Job:
    host = make_host(env={"SAMPLE_VAR": "1"})
    return make_job(host=host, interactive=kind == "interactive", **fields)


def test_every_jobspec_field_is_placed() -> None:
    """A new JobSpec field must be put in one of the three groups."""
    fields = {f.name for f in dataclasses.fields(JobSpec)}
    groups = [set(SCHEDULER_OPTIONS), SCRIPT_OPTIONS, NOT_OPTIONS]
    assert set().union(*groups) == fields
    assert sum(len(g) for g in groups) == len(fields), "a field is in two groups"
    assert set(SAMPLES) == set(SCHEDULER_OPTIONS)


@pytest.mark.parametrize("scheduler", IMPLEMENTED, indirect=True)
@pytest.mark.parametrize("kind", KINDS)
def test_table_is_complete(scheduler: BaseScheduler, kind: str) -> None:
    table = _table(scheduler, kind)
    assert set(table) == set(SCHEDULER_OPTIONS)
    for option, support in table.items():
        assert support in (MAPS, REJECTS) or (
            isinstance(support, NotApplicable) and support.reason
        ), (option, support)


@pytest.fixture
def sched(monkeypatch, tmp_path):
    """A scheduler by name, with its script and exit directories under tmp_path."""
    monkeypatch.setenv("HPC_SCRIPT_DIR", str(tmp_path / "scripts"))
    monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path / "exit"))
    return lambda name: get_scheduler(name, config=HPCConfig())


@pytest.fixture
def launched(monkeypatch) -> dict[str, Any]:
    """What the local scheduler starts a job with, in place of starting it."""
    import subprocess
    from types import SimpleNamespace

    seen: dict[str, Any] = {}

    def start(argv, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(returncode=0, poll=lambda: 0, wait=lambda: 0, pid=1)

    monkeypatch.setattr(subprocess, "run", start)
    monkeypatch.setattr(subprocess, "Popen", start)
    return seen


SCHEDULER_OPTIONS_DOC = Path(__file__).resolve().parents[3] / "docs/dev/SCHEDULER_OPTIONS.md"
#: The three words of the rules table, and the value a scheduler class
#: declares for each.
PASSES_ON, REFUSES, IGNORES = "passes on", "refuses", "ignores"


def _rules() -> dict[tuple[str, str, str], str]:
    """The table under "The rules" in docs/dev/SCHEDULER_OPTIONS.md, as
    (scheduler, kind, option) -> "passes on" | "refuses" | "ignores"."""
    text = SCHEDULER_OPTIONS_DOC.read_text()
    section = text.split("## The rules", 1)[1].split("\n## ", 1)[0]
    lines = [line for line in section.splitlines() if line.startswith("|")]
    header, _, *rows = ([cell.strip() for cell in line.strip("|").split("|")] for line in lines)
    cells = {}
    for option, *values in rows:
        for column, value in zip(header[1:], values, strict=True):
            scheduler, kind = column.lower().split()
            cells[scheduler, kind, option.strip("`")] = value
    return cells


RULES = _rules()


def test_the_rules_cover_every_option_and_use_three_words() -> None:
    assert {option for _, _, option in RULES} == set(SCHEDULER_OPTIONS)
    assert set(RULES.values()) <= {PASSES_ON, REFUSES, IGNORES}


def _script_change(scheduler: BaseScheduler, kind: str, option: str) -> tuple[list[str], list[str]]:
    """The lines the script gains and loses when *option* is set."""
    before = scheduler.preview(_job(kind, **BASELINES.get(option, {}))).splitlines()
    after = scheduler.preview(_job(kind, **SAMPLES[option])).splitlines()
    return [x for x in after if x not in before], [x for x in before if x not in after]


def _qrsh_words(scheduler: BaseScheduler, **fields: Any) -> str:
    """qrsh's arguments between ``qrsh -now no`` and the script."""
    argv = scheduler._build_qrsh_command(_job("interactive", **fields), "SCRIPT")  # type: ignore[attr-defined]
    assert argv[:3] == ["qrsh", "-now", "no"] and argv[-1] == "SCRIPT"
    return " ".join(argv[3:-1])


#: SGE batch, option set as in SAMPLES: (lines the script gains, lines it loses).
SGE_BATCH: dict[str, tuple[list[str], list[str]]] = {
    "name": (["#$ -N sample_name"], ["#$ -N alice_echo"]),
    "cpu": (["#$ -pe smp 3"], []),
    "mem": (["#$ -l mem_free=2G"], []),
    "time": (["#$ -l h_rt=1:00:00"], []),
    "queue": (["#$ -q sample.q"], []),
    "project": (["#$ -P sample_project"], []),
    "stdout": (["#$ -o /work/sample.log"], ["#$ -o /work/$JOB_NAME.$JOB_ID.out"]),
    "stderr": (["#$ -e /work/sample.err"], ["#$ -j y"]),
    "inherit_env": ([], ["#$ -V"]),
    "env_passthrough": (["#$ -v SAMPLE_VAR=1"], []),
    # The job runs in workdir: qsub is started there with -cwd
    # (test_sge.py, test_qsub_runs_in_workdir); the script shows it in -o.
    "workdir": (
        ["#$ -o /sample/dir/$JOB_NAME.$JOB_ID.out"],
        ["#$ -o /work/$JOB_NAME.$JOB_ID.out"],
    ),
    "licenses": (["#$ -l sample_lic=2"], []),
    "devices": (["#$ -l sample_dev=2"], []),
    "features": (["#$ -l sample_feat=1"], []),
    "array": (
        ["#$ -o /work/$JOB_NAME.$JOB_ID.$TASK_ID.out", "#$ -t 1-3"],
        ["#$ -o /work/$JOB_NAME.$JOB_ID.out"],
    ),
    "dependencies": (["#$ -hold_jid 7"], []),
}
#: SGE interactive: qrsh's arguments with the option set as in SAMPLES.
#: With nothing set they are ``-cwd -V -N alice_echo``.
SGE_INTERACTIVE: dict[str, str] = {
    "name": "-cwd -V -N sample_name",
    "cpu": "-cwd -V -N alice_echo -pe smp 3",
    "mem": "-cwd -V -N alice_echo -l mem_free=2G",
    "time": "-cwd -V -N alice_echo -l h_rt=1:00:00",
    "queue": "-cwd -V -N alice_echo -q sample.q",
    "project": "-cwd -V -N alice_echo -P sample_project",
    "inherit_env": "-cwd -N alice_echo",
    "env_passthrough": "-cwd -N alice_echo -v SAMPLE_VAR=1",
    "licenses": "-cwd -V -N alice_echo -l sample_lic=2",
    "devices": "-cwd -V -N alice_echo -l sample_dev=2",
    "features": "-cwd -V -N alice_echo -l sample_feat=1",
}
#: Local batch: (lines the script gains, lines it loses).
LOCAL_BATCH: dict[str, tuple[list[str], list[str]]] = {
    "name": (
        ["exec > /work/sample_name.'<job_id>'.out 2>&1"],
        ["exec > /work/alice_echo.'<job_id>'.out 2>&1"],
    ),
    "stdout": (["exec > /work/sample.log 2>&1"], ["exec > /work/alice_echo.'<job_id>'.out 2>&1"]),
    "stderr": (
        [
            "# Redirect stdout and stderr to separate files",
            "exec > /work/alice_echo.'<job_id>'.out 2> /work/sample.err",
        ],
        [
            "# Redirect stdout and stderr (merged) to file",
            "exec > /work/alice_echo.'<job_id>'.out 2>&1",
        ],
    ),
}
#: Options whose effect is on how the job is started, not on the script;
#: each has its own test below.
AT_LAUNCH = {
    ("sge", "interactive", "workdir"),
    ("local", "batch", "cpu"),
    ("local", "interactive", "cpu"),
    *(("local", kind, option) for kind in KINDS for option in ("inherit_env", "env_passthrough")),
    ("local", "batch", "workdir"),
    ("local", "interactive", "workdir"),
}


def _cells(rule: str) -> list[tuple[str, str, str]]:
    return [cell for cell in sorted(RULES) if RULES[cell] == rule and cell[0] in IMPLEMENTED]


@pytest.mark.parametrize(("name", "kind", "option"), _cells(PASSES_ON))
def test_passes_on(name: str, kind: str, option: str, sched) -> None:
    """A "passes on" cell: the scheduler is handed exactly the flag written here."""
    scheduler = sched(name)
    if (name, kind, option) in AT_LAUNCH:
        return
    if (name, kind) == ("sge", "batch"):
        assert _script_change(scheduler, kind, option) == SGE_BATCH[option]
    elif (name, kind) == ("sge", "interactive"):
        assert _qrsh_words(scheduler, **SAMPLES[option]) == SGE_INTERACTIVE[option]
    else:
        assert (name, kind) == ("local", "batch")
        assert _script_change(scheduler, kind, option) == LOCAL_BATCH[option]


@pytest.mark.parametrize(("name", "kind", "option"), _cells(IGNORES))
def test_ignores(name: str, kind: str, option: str, sched) -> None:
    """An "ignores" cell: the job is exactly the job without the option."""
    scheduler = sched(name)
    assert _script_change(scheduler, kind, option) == ([], [])
    if (name, kind) == ("sge", "interactive"):
        assert _qrsh_words(scheduler, **SAMPLES[option]) == "-cwd -V -N alice_echo"


@pytest.mark.parametrize(("name", "kind", "option"), _cells(REFUSES))
def test_refuses(name: str, kind: str, option: str, sched) -> None:
    """A "refuses" cell: no job is made, and the error names what was asked."""
    scheduler = sched(name)
    with pytest.raises(NotSupportedError, match=option.rstrip("s").replace("ie", "")):
        scheduler.submit(_job(kind, **SAMPLES[option]))


def test_the_tables_above_cover_every_passes_on_cell() -> None:
    written = (
        {("sge", "batch", option) for option in SGE_BATCH}
        | {("sge", "interactive", option) for option in SGE_INTERACTIVE}
        | {("local", "batch", option) for option in LOCAL_BATCH}
        | AT_LAUNCH
    )
    assert written == set(_cells(PASSES_ON))


class TestAtLaunch:
    """Options that change how the job is started."""

    def test_sge_interactive_runs_in_workdir(self, sched, tmp_path):
        from tests.fakes import patch_seam

        with patch_seam(lambda argv: (0, "", "")) as seam:
            sched("sge").submit(_job("interactive", workdir=str(tmp_path)))
        assert seam.call_args.kwargs["cwd"] == tmp_path
        assert "-cwd" in seam.call_args.args[0]

    @pytest.mark.parametrize("kind", KINDS)
    def test_local_gives_the_job_its_cpu_count(self, sched, kind):
        job = _job(kind, cpu=3, env_vars={"MAKEFLAGS": "-j${hpc.cpus}"})
        assert "export MAKEFLAGS=-j3" in sched("local").preview(job).splitlines()

    @pytest.mark.parametrize("kind", KINDS)
    @pytest.mark.parametrize(
        ("fields", "env"),
        [
            ({"inherit_env": False}, {}),
            ({"inherit_env": False, "env_passthrough": ["SAMPLE_VAR"]}, {"SAMPLE_VAR": "1"}),
        ],
    )
    def test_local_starts_the_job_with_only_the_named_environment(
        self, sched, launched, tmp_path, kind, fields, env
    ):
        sched("local").submit(_job(kind, workdir=str(tmp_path), **fields))
        assert launched["env"] == env

    @pytest.mark.parametrize("kind", KINDS)
    def test_local_runs_in_workdir(self, sched, launched, tmp_path, kind):
        sched("local").submit(_job(kind, workdir=str(tmp_path)))
        assert launched["cwd"] == tmp_path


class TestCheckOptions:
    """The error for an option a scheduler cannot honour."""

    @pytest.fixture
    def picky(self, monkeypatch, tmp_path) -> BaseScheduler:
        """SGE with ``cpu`` rejected on batch jobs and ``queue`` on interactive ones."""
        monkeypatch.setenv("HPC_SCRIPT_DIR", str(tmp_path / "scripts"))
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path / "exit"))
        scheduler = get_scheduler("sge", config=HPCConfig())
        monkeypatch.setattr(
            type(scheduler), "BATCH_OPTIONS", {**scheduler.BATCH_OPTIONS, "cpu": REJECTS}
        )
        monkeypatch.setattr(
            type(scheduler),
            "INTERACTIVE_OPTIONS",
            {**scheduler.INTERACTIVE_OPTIONS, "queue": REJECTS},
        )
        return scheduler

    def test_names_the_option_and_points_to_scheduler_args(self, picky):
        with pytest.raises(NotSupportedError) as err:
            picky.preview(make_job(cpu=4))
        assert str(err.value) == (
            "the sge scheduler cannot honour 'cpu' on a batch job; "
            "ask for it with the scheduler's own flags in scheduler_args (-S)"
        )

    def test_nothing_is_submitted(self, picky):
        from tests.fakes import patch_seam

        with patch_seam(lambda argv: (0, "", "")) as seam, pytest.raises(NotSupportedError):
            picky.submit(make_job(cpu=4))
        assert not seam.called

    def test_an_unset_option_is_not_refused(self, picky):
        assert "#$ -q all.q" in picky.preview(make_job(queue="all.q"))

    def test_the_job_kind_picks_the_table(self, picky):
        picky.preview(make_job(cpu=4, interactive=True))  # cpu is rejected for batch only
        with pytest.raises(NotSupportedError, match="'queue' on an interactive job"):
            picky.preview(make_job(queue="all.q", interactive=True))


@pytest.mark.parametrize("name", IMPLEMENTED)
@pytest.mark.parametrize("kind", KINDS)
def test_the_scheduler_declares_what_the_rules_say(name: str, kind: str) -> None:
    """``check_options`` reads these declarations to refuse a job."""
    words = {MAPS: PASSES_ON, REJECTS: REFUSES}
    table = _table(get_scheduler(name, config=HPCConfig()), kind)
    declared = {
        option: words.get(value, IGNORES) if isinstance(value, str) else IGNORES
        for option, value in table.items()
    }
    assert declared == {option: RULES[name, kind, option] for option in SCHEDULER_OPTIONS}
