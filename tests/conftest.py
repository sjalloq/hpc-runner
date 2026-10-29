# ruff: noqa: E501
"""Pytest configuration and fixtures."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import jinja2
import pytest

from tests.fakes import make_host, patch_seam


def pytest_addoption(parser):
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="Rewrite tests/unit/golden/*.sh from the current templates instead of comparing",
    )


# Every job script rendered anywhere in the suite is syntax-checked with
# `bash -n` once (by content): a quoting bug in a template fails the test that
# rendered it, not only the golden tests.
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
def mock_sge_commands():
    """Mock SGE commands (qsub, qstat, etc.) at the subprocess seam."""

    def respond(cmd):
        stdout = ""

        if cmd[0] == "qsub":
            stdout = 'Your job 12345 ("test_job") has been submitted\n'
        elif cmd[0] == "qstat":
            if "-xml" in cmd:
                # XML output for list_active_jobs
                stdout = """<?xml version='1.0'?>
<job_info>
  <queue_info>
    <job_list state="running">
      <JB_job_number>12345</JB_job_number>
      <JB_name>running_job</JB_name>
      <JB_owner>testuser</JB_owner>
      <state>r</state>
      <queue_name>batch.q@node1</queue_name>
      <slots>4</slots>
      <JB_submission_time>1704110400</JB_submission_time>
      <JAT_start_time>1704110460</JAT_start_time>
    </job_list>
  </queue_info>
  <job_info>
    <job_list state="pending">
      <JB_job_number>12346</JB_job_number>
      <JB_name>pending_job</JB_name>
      <JB_owner>testuser</JB_owner>
      <state>qw</state>
      <slots>2</slots>
      <JB_submission_time>1704110500</JB_submission_time>
    </job_list>
    <job_list state="pending">
      <JB_job_number>12347</JB_job_number>
      <JB_name>other_user_job</JB_name>
      <JB_owner>otheruser</JB_owner>
      <state>qw</state>
      <queue_name>gpu.q@node2</queue_name>
      <slots>8</slots>
    </job_list>
  </job_info>
</job_info>
"""
            elif "-j" in cmd:
                stdout = "job_number: 12345\n"
            else:
                stdout = """job-ID  prior   name       user         state submit/start at     queue                          slots ja-task-ID
-----------------------------------------------------------------------------------------------------------------
12345   0.55500 test_job   user         r     01/01/2024 10:00:00 all.q@node1                    1
"""
        elif cmd[0] == "qdel":
            stdout = "user has deleted job 12345\n"
        elif cmd[0] == "qacct":
            stdout = """==============================================================
qname        all.q
hostname     node1
owner        user
jobname      test_job
jobnumber    12345
exit_status  0
"""

        return 0, stdout, ""

    with patch_seam(respond) as mock_run:
        yield mock_run


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
