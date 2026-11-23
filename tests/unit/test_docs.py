"""Executable docs: the README and Sphinx pages cannot drift from the code.

- Every Python snippet in ``README.md`` and ``programmatic_api.rst`` must
  parse and compile; one whose first line is ``# doctest: local`` is run
  against the local scheduler in a scratch directory.
- Every TOML snippet that is a whole config file (has ``config_version``) or
  a set of whole sections must pass config validation.
- The configuration reference lists every job-layer key and every key each
  scheduler declares; the CLI docs list every ``hpc run`` option.
- The tables of the Slurm guide (the flag for each option, the scheduler
  settings, the ``${hpc.*}`` variables, the job states) say what the code
  does.
"""

from __future__ import annotations

import ast
import re
import time
from pathlib import Path

import pytest

from hpc_runner.cli.run import run as run_command
from hpc_runner.core.config import check_config
from hpc_runner.core.job import SCHEDULER_OPTIONS
from hpc_runner.core.result import JobResult, JobStatus
from hpc_runner.core.schema import FIELD_VALIDATORS
from hpc_runner.schedulers.local import LocalScheduler
from hpc_runner.schedulers.sge import SGEScheduler
from hpc_runner.schedulers.slurm import SlurmScheduler
from hpc_runner.schedulers.slurm.parser import state_to_status

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
DOCS = ROOT / "docs" / "source"
RUN_MARKER = "# doctest: local"


def _markdown_blocks(path: Path, lang: str) -> list[tuple[str, str]]:
    blocks = re.findall(rf"^```{lang}\n(.*?)^```", path.read_text(), re.M | re.S)
    return [(f"{path.name}#{i}", block) for i, block in enumerate(blocks, 1)]


def _rst_blocks(path: Path, lang: str) -> list[tuple[str, str]]:
    """The bodies of ``.. code-block:: <lang>`` directives, dedented."""
    blocks: list[tuple[str, str]] = []
    lines = path.read_text().splitlines()
    i = 0
    while i < len(lines):
        match = re.match(rf"^(\s*)\.\. code-block:: {lang}\s*$", lines[i])
        i += 1
        if not match:
            continue
        indent = len(match.group(1))
        while i < len(lines) and lines[i].strip().startswith(":"):
            i += 1  # directive options
        body: list[str] = []
        while i < len(lines) and (not lines[i].strip() or _indent(lines[i]) > indent):
            body.append(lines[i])
            i += 1
        text = "\n".join(body).strip("\n")
        width = min(_indent(line) for line in text.splitlines() if line.strip())
        dedented = "\n".join(line[width:] for line in text.splitlines())
        blocks.append((f"{path.name}#{len(blocks) + 1}", dedented + "\n"))
    return blocks


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


PYTHON = _markdown_blocks(README, "python") + _rst_blocks(DOCS / "programmatic_api.rst", "python")
TOML = _markdown_blocks(README, "toml") + [
    block for page in sorted(DOCS.glob("*.rst")) for block in _rst_blocks(page, "toml")
]
RUNNABLE = [(name, code) for name, code in PYTHON if code.startswith(RUN_MARKER)]


def test_snippets_found():
    assert len(PYTHON) >= 15 and len(TOML) >= 15
    assert "programmatic_api.rst" in {name.split("#")[0] for name, _ in RUNNABLE}


@pytest.mark.parametrize(("name", "code"), PYTHON, ids=[n for n, _ in PYTHON])
def test_python_snippet_compiles(name, code):
    ast.parse(code, filename=name)
    compile(code, name, "exec")


@pytest.fixture
def local_site(tmp_path, monkeypatch):
    """A scratch submit directory whose config selects nothing but a stub `module`."""
    stub = tmp_path / "modules-init.sh"
    stub.write_text("module() { :; }\n")
    (tmp_path / "hpc-runner.toml").write_text(
        f'config_version = 1\nmodule_init_script = "{stub}"\n'
    )
    for script in ("train.py", "preprocess.py", "evaluate.py"):
        (tmp_path / script).write_text(f"print('{script} done')\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HPC_SCHEDULER", "local")
    monkeypatch.delenv("HPC_RUNNER_CONFIG", raising=False)
    real_sleep = time.sleep
    monkeypatch.setattr(time, "sleep", lambda seconds: real_sleep(min(seconds, 0.02)))
    return tmp_path


@pytest.mark.parametrize(("name", "code"), RUNNABLE, ids=[n for n, _ in RUNNABLE])
def test_local_snippet_runs(name, code, local_site):
    namespace: dict[str, object] = {"__name__": "__doc_snippet__"}
    exec(compile(code, name, "exec"), namespace)
    result = namespace.get("result")
    if isinstance(result, JobResult):
        assert result.wait(poll_interval=0.02) is JobStatus.COMPLETED
        assert result.returncode == 0


def _is_whole_file(text: str) -> bool:
    first = next(line for line in text.splitlines() if line.strip() and not line.startswith("#"))
    return "config_version" in text or first.startswith("[")


@pytest.mark.parametrize(("name", "text"), TOML, ids=[n for n, _ in TOML])
def test_toml_snippet_validates(name, text, tmp_path):
    if not _is_whole_file(text):
        pytest.skip("fragment (keys without their section)")
    if "config_version" not in text:
        text = "config_version = 1\n" + text
    path = tmp_path / "hpc-runner.toml"
    path.write_text(text)
    _, errors = check_config(path)
    assert errors == []


def _section(text: str, title: str) -> str:
    start = text.index(f"\n{title}\n")
    end = re.search(r"\n[^\n]+\n-{3,}\n", text[start + len(title) + 2 :])
    return text[start : start + len(title) + 2 + (end.start() if end else len(text))]


def test_configuration_reference_lists_every_job_key():
    reference = _section((DOCS / "configuration.rst").read_text(), "Job settings reference")
    listed = set(re.findall(r"^   \* - ``(\w+)``", reference, re.M))
    assert listed == set(FIELD_VALIDATORS)


SCHEDULER_KEYS = {
    SGEScheduler: [
        "parallel_environment",
        "memory_resource",
        "time_resource",
        "memory_per",
        "emulate_afterok",
        "sentinel_ttl_days",
    ],
    SlurmScheduler: ["memory_per", "kill_invalid_dependents"],
    LocalScheduler: [],
}


@pytest.mark.parametrize("scheduler", list(SCHEDULER_KEYS), ids=lambda s: s.name)
def test_configuration_reference_lists_every_scheduler_key(scheduler):
    """Each scheduler's keys are listed under its own ``[schedulers.<name>]`` heading."""
    assert list(scheduler.CONFIG_KEYS) == SCHEDULER_KEYS[scheduler]
    reference = _section((DOCS / "configuration.rst").read_text(), "Scheduler settings reference")
    assert f"``[schedulers.{scheduler.name}]``" in reference
    own = reference.split(f"``[schedulers.{scheduler.name}]``", 1)[1].split("``[schedulers.", 1)[0]
    for key in scheduler.CONFIG_KEYS:
        assert f"``{key}``" in own, key


@pytest.mark.parametrize(
    "option",
    sorted(opt for param in run_command.params for opt in param.opts if opt.startswith("--")),
)
def test_cli_docs_list_every_run_option(option):
    listed = rf"(-\w, )?{option}\b"
    assert re.search(rf"``{listed}", (DOCS / "cli.rst").read_text()), "docs/source/cli.rst"


# =============================================================================
# The Slurm guide
# =============================================================================


def _list_table(section: str) -> list[list[str]]:
    """The body rows of the first ``list-table`` in *section*, each a list of cell texts."""
    rows: list[list[str]] = []
    for line in section.splitlines():
        if line.startswith("   * - "):
            rows.append([line[7:].strip()])
        elif not rows:
            continue
        elif line.startswith("     -"):
            rows[-1].append(line[6:].strip())
        elif line.startswith("       ") and line.strip():
            rows[-1][-1] += " " + line.strip()
        elif line.strip() and not line.startswith(" "):
            break
    return rows[1:]


def _slurm_table(title: str) -> list[list[str]]:
    return _list_table(_section((DOCS / "slurm.rst").read_text(), title))


#: option -> the flag the Slurm guide gives for it (docs/dev/SCHEDULER_OPTIONS.md, "Flags").
SLURM_FLAGS = {
    "name": "``--job-name=NAME``",
    "cpu": "``--cpus-per-task=N --ntasks=1``",
    "mem": "``--mem=MB``",
    "time": "``--time=D-HH:MM:SS``",
    "queue": "``--partition=NAME``",
    "project": "``--account=NAME``",
    "workdir": "``--chdir=PATH``",
    "stdout": "``--output=PATH``",
    "stderr": "``--error=PATH``",
    "inherit_env": "``--export=ALL`` when true, ``--export=NONE`` when false",
    "env_passthrough": "``--export=NAME=value,...``",
    "licenses": "``-L name:N,name:N``",
    "devices": "``--gres=name:N,name:N``",
    "features": "``-C name&name``",
    "array": "``--array=START-END[:STEP][%MAX]``",
    "dependencies": "``--dependency=TYPE:ID[:ID...][,TYPE:ID...]``",
    "scheduler_args": "the arguments as given",
}


def test_slurm_guide_gives_the_flag_for_every_option():
    rows = _slurm_table("How each option reaches Slurm")
    assert {row[0].strip("`"): row[1] for row in rows} == SLURM_FLAGS
    assert set(SLURM_FLAGS) == set(SCHEDULER_OPTIONS)


def test_slurm_guide_lists_the_scheduler_settings_with_their_defaults():
    rows = _slurm_table("Scheduler settings")
    assert {row[0].strip("`"): row[2] for row in rows} == {
        "memory_per": '``"job"``',
        "kill_invalid_dependents": "``true``",
    }
    assert [row[0].strip("`") for row in rows] == list(SlurmScheduler.CONFIG_KEYS)


def test_slurm_guide_hpc_variables_are_the_ones_rendered():
    rows = _slurm_table("``${hpc.*}`` variables")
    assert {row[0]: (row[1], row[2]) for row in rows} == {
        "``${hpc.cpus}``": ("``$SLURM_CPUS_PER_TASK``", "not allowed"),
        "``${hpc.job_id}``": (
            "``${SLURM_ARRAY_JOB_ID:-$SLURM_JOB_ID}``",
            "``%j``; ``%A`` for an array",
        ),
        "``${hpc.task_id}``": ("``$SLURM_ARRAY_TASK_ID``", "``%a``"),
        "``${hpc.job_name}``": ("``$SLURM_JOB_NAME``", "``%x``"),
        "``${hpc.tmpdir}``": ("``$TMPDIR``", "not allowed"),
        "``${hpc.host}``": ("``$SLURMD_NODENAME``", "``%N``"),
    }
    for name, in_script, in_path in rows:
        key = name.strip("`")[len("${hpc.") : -1]
        assert in_script.strip("`") == SlurmScheduler.HPC_VARS[key].strip('"')
        if key in SlurmScheduler.HPC_PATH_VARS:
            assert in_path.startswith(f"``{SlurmScheduler.HPC_PATH_VARS[key]}``")
        else:
            assert in_path == "not allowed"


#: Slurm state -> hpc-runner status, as the Slurm guide's table gives them.
SLURM_STATES = {
    "PENDING": "PENDING",
    "REQUEUED": "PENDING",
    "REQUEUE_HOLD": "PENDING",
    "REQUEUE_FED": "PENDING",
    "RESV_DEL_HOLD": "PENDING",
    "RUNNING": "RUNNING",
    "CONFIGURING": "RUNNING",
    "COMPLETING": "RUNNING",
    "SIGNALING": "RUNNING",
    "STAGE_OUT": "RUNNING",
    "RESIZING": "RUNNING",
    "SUSPENDED": "SUSPENDED",
    "STOPPED": "SUSPENDED",
    "COMPLETED": "COMPLETED",
    "FAILED": "FAILED",
    "NODE_FAIL": "FAILED",
    "BOOT_FAIL": "FAILED",
    "SPECIAL_EXIT": "FAILED",
    "CANCELLED by <uid>": "CANCELLED",
    "PREEMPTED": "CANCELLED",
    "REVOKED": "CANCELLED",
    "CANCELLED": "SKIPPED",
    "TIMEOUT": "TIMEOUT",
    "DEADLINE": "TIMEOUT",
    "OUT_OF_MEMORY": "KILLED",
}


def test_slurm_guide_state_table_is_the_one_the_parser_applies():
    rows = _slurm_table("Job status")
    listed = {
        state: status.strip("`")
        for states, status in rows
        for state in re.findall(r"``([^`]+)``", states)
    }
    assert listed == SLURM_STATES
    for state, status in SLURM_STATES.items():
        assert state_to_status(state) is JobStatus[status], state
    assert rows[-1] == ["anything else", "``UNKNOWN``"]
    assert state_to_status("SOMETHING_NEW") is JobStatus.UNKNOWN
