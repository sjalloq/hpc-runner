"""Tests for the exception hierarchy's messages and attributes."""

from pathlib import Path

from hpc_runner.core.exceptions import (
    ConfigError,
    HPCToolsError,
    SchedulerCommandError,
    SchedulerError,
    SubmissionError,
)


def test_config_error_location_prefix():
    assert str(ConfigError("bad")) == "bad"
    assert str(ConfigError("bad", path=Path("a.toml"))) == "a.toml: bad"
    err = ConfigError("bad", path=Path("a.toml"), line=7)
    assert str(err) == "a.toml:7: bad"
    assert err.path == Path("a.toml") and err.line == 7


def test_command_error_message_fallback():
    err = SchedulerCommandError(["qstat", "-j", "1"], 2, "", "  \n")
    assert str(err) == "qstat exited with status 2"
    assert isinstance(err, SchedulerError) and isinstance(err, HPCToolsError)


def test_submission_error_headline_and_full_stderr():
    err = SubmissionError("\nfirst\nsecond\n", script="#!/bin/bash\n")
    assert str(err) == "first"
    assert err.stderr == "\nfirst\nsecond\n"
    assert err.script == "#!/bin/bash\n"
