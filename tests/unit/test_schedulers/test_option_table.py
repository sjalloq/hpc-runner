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
#: Replaced by ``licenses``, ``devices`` and ``features`` in the next step of
#: docs/dev/slurm/PLAN.md ("Order of work"); those join SCHEDULER_OPTIONS then.
REPLACED_NEXT = {"resources"}

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


def _placed(scheduler: BaseScheduler, job: Job) -> str:
    """Everything *scheduler* would hand over for *job*: the script, the
    interactive command line, and what the launch itself carries (the
    directory it is started in, the environment it is given)."""
    parts = [scheduler.preview(job), f"cwd={job.workdir}"]
    if scheduler.name == "sge" and job.interactive:
        parts.append(" ".join(scheduler._build_qrsh_command(job, "SCRIPT")))  # type: ignore[attr-defined]
    if scheduler.name == "local":
        parts.append(f"env={'inherited' if job.inherit_env else dict(job.env_passthrough)}")
    return "\n".join(parts)


def test_every_jobspec_field_is_placed() -> None:
    """A new JobSpec field must be put in one of the four groups."""
    fields = {f.name for f in dataclasses.fields(JobSpec)}
    groups = [set(SCHEDULER_OPTIONS), SCRIPT_OPTIONS, NOT_OPTIONS, REPLACED_NEXT]
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


@pytest.mark.parametrize("scheduler", IMPLEMENTED, indirect=True)
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("option", SCHEDULER_OPTIONS)
def test_cell_behaves_as_it_says(scheduler: BaseScheduler, kind: str, option: str) -> None:
    support = _table(scheduler, kind)[option]
    try:
        job = _job(kind, **SAMPLES[option])
    except NotSupportedError as exc:
        # resolve() itself refuses an interactive array, on every scheduler.
        assert (support, kind, option) == (REJECTS, "interactive", "array"), exc
        return
    if support == REJECTS:
        with pytest.raises(NotSupportedError, match=option.rstrip("s")):
            scheduler.preview(job)
        with pytest.raises(NotSupportedError, match=option.rstrip("s")):
            scheduler.submit(job)
        return
    without = _placed(scheduler, _job(kind, **BASELINES.get(option, {})))
    if support == MAPS:
        assert _placed(scheduler, job) != without
    else:
        assert _placed(scheduler, job) == without


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
