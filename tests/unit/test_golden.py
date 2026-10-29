"""Golden job scripts: every scheduler's preview of representative jobs.

Each case resolves a ``JobSpec`` against an in-memory ``HPCConfig`` and a
fixed ``Host``, renders it with ``scheduler.preview(job)`` and compares the
script to ``tests/unit/golden/<case>.sh``. After an intended template change,
regenerate with ``pytest tests/unit/test_golden.py --update-golden`` and review
the diff.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from hpc_runner.core.config import HPCConfig
from hpc_runner.core.dependency import Dependency, DependencyType
from hpc_runner.core.host import Host
from hpc_runner.core.resolve import resolve
from hpc_runner.core.spec import JobSpec
from hpc_runner.schedulers.base import BaseScheduler
from hpc_runner.schedulers.local import LocalScheduler
from hpc_runner.schedulers.sge import SGEScheduler
from tests.conftest import bash_syntax_error

GOLDEN_DIR = Path(__file__).parent / "golden"

#: Exit-sentinel directory rendered into SGE batch scripts (normally under $HOME).
EXIT_DIR = "/shared/hpc-runner/exit"

CONFIG = HPCConfig(
    defaults={
        "modules": ["gcc/12"],
        "modules_path": ["/site/modulefiles"],
        "env_unset": ["http_proxy"],
    },
    tools={
        "python": {"modules": ["python/3.11"], "env_vars": {"PYTHONUNBUFFERED": "1"}},
    },
    schedulers={
        "sge": {
            "parallel_environment": "smp",
            "memory_resource": "h_vmem",
            "time_resource": "h_rt",
            "memory_per": "slot",
            "module_init_script": "/site/modules/init.sh",
            "module_system": "modules",
        },
        "local": {"module_init_script": "/site/modules/init.sh", "module_system": "modules"},
    },
)


def _minimal() -> JobSpec:
    return JobSpec("echo hello")


def _full_environment() -> JobSpec:
    return JobSpec(
        ["python", "train.py", "--label", "my run"],
        name="train",
        cpu=4,
        mem="16G",
        time="4h",
        modules=["cuda/12.2"],
        modules_path_append=["/home/alice/modulefiles"],
        venv=".venv",
        env_prepend={"PATH": "/opt/tools/bin"},
        env_append={"PYTHONPATH": "/work/lib"},
        env_vars={"MAKEFLAGS": "-j${hpc.cpus}", "GREETING": 'it\'s $$5 "quoted"'},
        stdout="logs/${hpc.job_name}.${hpc.job_id}.out",
        stderr="logs/${hpc.job_name}.${hpc.job_id}.err",
    )


def _array_with_dependencies() -> JobSpec:
    return JobSpec(
        "./sim.sh --seed $SGE_TASK_ID",
        name="sweep",
        cpu=2,
        mem="8G",
        time="30m",
        array="1-10:2%3",
        dependencies=[
            Dependency("100", DependencyType.AFTEROK),
            Dependency("200", DependencyType.AFTERANY),
            Dependency("300", DependencyType.AFTERNOTOK, (1, 2, 3)),
        ],
    )


def _passthrough_resources_project() -> JobSpec:
    return JobSpec(
        "make sim",
        queue="batch.q",
        project="chip",
        priority=-10,
        inherit_env=False,
        env_passthrough=["DISPLAY"],
        resources=[{"name": "xcelium_lic", "value": 1}, {"name": "scratch", "value": "20G"}],
        scheduler_args={"sge": ["-l", "h=node01", "-binding", "linear:1"]},
    )


SPECS: dict[str, Callable[[], JobSpec]] = {
    "minimal": _minimal,
    "full_environment": _full_environment,
    "array_with_dependencies": _array_with_dependencies,
    "passthrough_resources_project": _passthrough_resources_project,
}

# (scheduler, mode) -> the cases that apply. Interactive and local jobs have
# no arrays or dependencies (both are refused), so that case is batch-only.
MODES: dict[str, tuple[str, bool, tuple[str, ...]]] = {
    "sge_batch": ("sge", False, tuple(SPECS)),
    "sge_interactive": (
        "sge",
        True,
        ("minimal", "full_environment", "passthrough_resources_project"),
    ),
    "local": ("local", False, ("minimal", "full_environment", "passthrough_resources_project")),
}

CASES = [f"{mode}_{case}" for mode, (_, _, cases) in MODES.items() for case in cases]


def _scheduler(name: str, monkeypatch, tmp_path: Path) -> BaseScheduler:
    if name == "local":
        return LocalScheduler(CONFIG)
    monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path / "exit"))
    scheduler = SGEScheduler(CONFIG)
    scheduler.exit_dir = EXIT_DIR
    return scheduler


def _render(case: str, monkeypatch, tmp_path: Path) -> str:
    mode = next(m for m in MODES if case.startswith(f"{m}_"))
    sched_name, interactive, _ = MODES[mode]
    spec = SPECS[case[len(mode) + 1 :]]()
    spec.workdir = "/work"
    if interactive:
        spec.interactive = True
    # The venv must exist on the submit host; host.cwd only anchors that check.
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "bin" / "activate").write_text("")
    host = Host(env={"DISPLAY": ":0"}, cwd=tmp_path, user="alice")
    job = resolve(spec, CONFIG, host)
    return _scheduler(sched_name, monkeypatch, tmp_path).preview(job)


@pytest.mark.parametrize("case", CASES)
def test_golden_script(case, request, monkeypatch, tmp_path):
    script = _render(case, monkeypatch, tmp_path)
    path = GOLDEN_DIR / f"{case}.sh"
    if request.config.getoption("--update-golden"):
        path.write_text(script)
        return
    assert path.exists(), f"missing {path}; run pytest --update-golden"
    assert script == path.read_text(), (
        f"{path.name} differs from the rendered script; if the change is intended, "
        "run pytest tests/unit/test_golden.py --update-golden and review the diff"
    )


def test_no_stray_golden_files():
    """Every checked-in golden file belongs to a case (renamed cases leave none behind)."""
    assert sorted(p.stem for p in GOLDEN_DIR.glob("*.sh")) == sorted(CASES)


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")
@pytest.mark.parametrize("path", sorted(GOLDEN_DIR.glob("*.sh")), ids=lambda p: p.name)
def test_golden_bash_syntax(path):
    assert bash_syntax_error(path.read_text()) is None
