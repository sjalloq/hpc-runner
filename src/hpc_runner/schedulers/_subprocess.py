"""The one place scheduler binaries are executed.

Every scheduler CLI call (``qsub``, ``qstat``, ``qdel``, ``qacct``, ``qrsh``,
detection probes) goes through :func:`run_command`.  It logs the argv at
DEBUG, applies a timeout, and converts every ``subprocess`` failure into a
:class:`~hpc_runner.core.exceptions.SchedulerError` subclass, so nothing
from ``subprocess`` escapes the scheduler layer.  Tests patch this function
instead of ``subprocess.run``.
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Sequence
from pathlib import Path

from hpc_runner.core.exceptions import (
    SchedulerCommandError,
    SchedulerTimeoutError,
    SchedulerUnavailableError,
)

logger = logging.getLogger("hpc_runner.schedulers")


def run_command(
    argv: Sequence[str],
    *,
    timeout: float | None,
    check: bool = True,
    capture: bool = True,
    cwd: Path | None = None,
    input: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a scheduler command.

    Args:
        argv: Command and arguments.
        timeout: Seconds before giving up, or None to wait forever
            (interactive sessions).
        check: Raise ``SchedulerCommandError`` on non-zero exit.
        capture: Capture stdout/stderr; False inherits the terminal.
        cwd: Working directory for the command.
        input: Text fed to stdin.

    Raises:
        SchedulerUnavailableError: ``argv[0]`` is not on PATH.
        SchedulerTimeoutError: The command exceeded *timeout*.
        SchedulerCommandError: Non-zero exit and *check* is true.
    """
    args = [str(a) for a in argv]
    logger.debug("run: %s", subprocess.list2cmdline(args))
    try:
        result = subprocess.run(
            args,
            capture_output=capture,
            text=True,
            errors="replace",
            timeout=timeout,
            cwd=cwd,
            input=input,
            check=False,
        )
    except FileNotFoundError as exc:
        raise SchedulerUnavailableError(f"{args[0]} not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise SchedulerTimeoutError(args, timeout) from exc
    logger.debug("exit %d: %s", result.returncode, args[0])

    if check and result.returncode != 0:
        raise SchedulerCommandError(
            args,
            result.returncode,
            result.stdout or "",
            result.stderr or "",
        )
    return result
