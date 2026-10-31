"""The one place scheduler binaries are executed.

Every scheduler CLI call (``qsub``, ``qstat``, ``qdel``, ``qacct``, ``qrsh``,
detection probes) goes through :func:`run_command`.  It logs the argv at
DEBUG, applies a timeout, and converts every ``subprocess`` failure into a
:class:`~hpc_runner.core.exceptions.SchedulerError` subclass, so nothing
from ``subprocess`` escapes the scheduler layer.  Tests patch this function
instead of ``subprocess.run``.

When a trace is active (``hpc --trace DIR`` or ``[trace] enabled``), every
call, including one that timed out or found no binary, is handed to
:func:`hpc_runner.trace.record` (docs/dev/trace/DESIGN.md D-TR1).
"""

from __future__ import annotations

import logging
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path

from hpc_runner.core.exceptions import (
    SchedulerCommandError,
    SchedulerTimeoutError,
    SchedulerUnavailableError,
)
from hpc_runner.trace import recorder

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
    started = time.monotonic()
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
        _record(args, None, None, None, started, error=f"{args[0]} not found on PATH")
        raise SchedulerUnavailableError(f"{args[0]} not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout
        err = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr
        _record(args, None, out, err, started, error=f"timed out after {timeout}s")
        raise SchedulerTimeoutError(args, timeout) from exc
    logger.debug("exit %d: %s", result.returncode, args[0])
    _record(args, result.returncode, result.stdout, result.stderr, started)

    if check and result.returncode != 0:
        raise SchedulerCommandError(
            args,
            result.returncode,
            result.stdout or "",
            result.stderr or "",
        )
    return result


def _record(
    args: list[str],
    returncode: int | None,
    stdout: str | None,
    stderr: str | None,
    started: float,
    *,
    error: str | None = None,
) -> None:
    recorder.record(
        args,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        elapsed=time.monotonic() - started,
        error=error,
    )
