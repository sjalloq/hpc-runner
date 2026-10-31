# ruff: noqa: E501
"""Pytest configuration and fixtures."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import jinja2
import pytest

from tests.fakes import SGEFixtures, make_host, make_sge_responder, patch_seam


def pytest_addoption(parser):
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="Rewrite tests/unit/golden/*.sh from the current templates instead of comparing",
    )


# Every job script rendered anywhere in the suite is syntax-checked with
# `bash -n` once (by content): a syntax error in a template fails the test that
# rendered it, not only the golden tests. (Quoting is a different matter: an
# unquoted value is still valid shell; TestModuleArgumentsRoundTrip and
# TestQuotingRoundTrip run scripts to prove values arrive intact.)
_BASH = shutil.which("bash")
_CHECKED_SCRIPTS: set[str] = set()


def bash_syntax_error(script: str) -> str | None:
    """`bash -n`'s complaint about *script*, or None when it parses."""
    assert _BASH is not None
    proc = subprocess.run([_BASH, "-n"], input=script, text=True, capture_output=True)
    if proc.returncode == 0:
        return None
    return proc.stderr or f"exit {proc.returncode}"


@pytest.fixture(autouse=True)
def _plain_terminal(monkeypatch):
    """Make CLI output plain text.

    rich-click forces styled output whenever FORCE_COLOR, PY_COLORS or
    GITHUB_ACTIONS is set (so CI logs are coloured), and rich keeps bold/dim
    even under NO_COLOR. A dumb terminal is the one setting that yields no
    escape codes at all, so substring assertions on CLI output hold everywhere.
    """
    monkeypatch.setenv("TERM", "dumb")


@pytest.fixture(autouse=True)
def _bash_check_rendered_scripts(monkeypatch):
    if _BASH is None:
        yield
        return
    original = jinja2.Template.render

    def render(self, *args, **kwargs):
        text = original(self, *args, **kwargs)
        if text.startswith("#!/bin/bash") and text not in _CHECKED_SCRIPTS:
            error = bash_syntax_error(text)
            assert error is None, f"rendered job script fails bash -n: {error}\n{text}"
            _CHECKED_SCRIPTS.add(text)
        return text

    monkeypatch.setattr(jinja2.Template, "render", render)
    yield


@pytest.fixture
def host(tmp_path):
    """A fixed submit host: empty environment, user 'alice'."""
    return make_host(cwd=tmp_path)


@pytest.fixture
def temp_dir():
    """Create a temporary directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_sge_commands(sge_fixtures):
    """SGE commands at the subprocess seam, answered from the captured fixtures.

    See :func:`tests.fakes.make_sge_responder`: answers depend on the whole
    argv, and ids come from ``sge_fixtures.job(<scenario>)``.
    """
    with patch_seam(make_sge_responder(sge_fixtures)) as mock_run:
        yield mock_run


@pytest.fixture(scope="session")
def sge_fixtures() -> SGEFixtures:
    """The SGE texts captured from the testbed (tests/unit/fixtures/sge/)."""
    return SGEFixtures()


@pytest.fixture
def sample_config(temp_dir):
    """Create a sample configuration file."""
    config_content = """config_version = 1

[defaults]
cpu = 2
mem = "8G"
time = "2:00:00"

[schedulers.sge]
parallel_environment = "mpi"
memory_resource = "h_vmem"

[tools.python]
cpu = 4
modules = ["python/3.11"]

[types.gpu]
queue = "gpu"
resources = [{name = "gpu", value = 1}]
"""
    config_file = temp_dir / "hpc-runner.toml"
    config_file.write_text(config_content)
    return config_file


@pytest.fixture
def clean_env():
    """Clean environment variables that might affect tests."""
    env_vars = ["HPC_SCHEDULER", "SGE_ROOT", "PBS_CONF_FILE"]
    old_values = {var: os.environ.get(var) for var in env_vars}

    for var in env_vars:
        if var in os.environ:
            del os.environ[var]

    yield

    for var, value in old_values.items():
        if value is not None:
            os.environ[var] = value
        elif var in os.environ:
            del os.environ[var]
