"""Test doubles for the scheduler subprocess seam."""

from __future__ import annotations

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

SEAM = "hpc_runner.schedulers._subprocess.run_command"

Responder = Callable[[list[str]], tuple[int, str, str]]


def make_fake_run_command(respond: Responder) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Build a stand-in for ``run_command``.

    *respond* maps an argv to ``(returncode, stdout, stderr)`` (or raises a
    SchedulerError itself, e.g. ``SchedulerUnavailableError``).  The fake
    honours ``check`` the way the real seam does, so non-zero exits reach
    scheduler code as ``SchedulerCommandError``.
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
