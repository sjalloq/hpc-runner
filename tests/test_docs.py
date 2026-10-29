"""Executable docs: the README and Sphinx pages cannot drift from the code.

- Every Python snippet in ``README.md`` and ``programmatic_api.rst`` must
  parse and compile; one whose first line is ``# doctest: local`` is run
  against the local scheduler in a scratch directory.
- Every TOML snippet that is a whole config file (has ``config_version``) or
  a set of whole sections must pass config validation.
- The configuration reference lists every job-layer key and every key each
  scheduler declares; the CLI docs list every ``hpc run`` option.
"""

from __future__ import annotations

import ast
import re
import time
from pathlib import Path

import pytest

from hpc_runner.cli.run import run as run_command
from hpc_runner.core.config import check_config
from hpc_runner.core.result import JobResult, JobStatus
from hpc_runner.core.schema import FIELD_VALIDATORS
from hpc_runner.schedulers.local import LocalScheduler
from hpc_runner.schedulers.sge import SGEScheduler

ROOT = Path(__file__).resolve().parents[1]
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
    assert {name.split("#")[0] for name, _ in RUNNABLE} == {"README.md", "programmatic_api.rst"}


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
        f'config_version = 1\n\n[schedulers.local]\nmodule_init_script = "{stub}"\n'
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


@pytest.mark.parametrize("scheduler", [SGEScheduler, LocalScheduler], ids=lambda s: s.name)
def test_configuration_reference_lists_every_scheduler_key(scheduler):
    reference = _section((DOCS / "configuration.rst").read_text(), "Scheduler settings reference")
    for key in scheduler.CONFIG_KEYS:
        assert f"``{key}``" in reference, key


@pytest.mark.parametrize(
    "option",
    sorted(opt for param in run_command.params for opt in param.opts if opt.startswith("--")),
)
def test_cli_docs_list_every_run_option(option):
    listed = rf"(-\w, )?{option}\b"
    assert re.search(rf"``{listed}", (DOCS / "cli.rst").read_text()), "docs/source/cli.rst"
    assert re.search(rf"^  {listed}", README.read_text(), re.M), "README.md CLI reference"
