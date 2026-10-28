"""Test doubles for the scheduler subprocess seam."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from unittest.mock import patch

from hpc_runner.core.exceptions import SchedulerCommandError

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
