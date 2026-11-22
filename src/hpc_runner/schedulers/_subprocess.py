"""The one place scheduler binaries are executed.

Every scheduler CLI call (``qsub``, ``qstat``, ``qdel``, ``qacct``, ``qrsh``,
``sbatch``, ``squeue``, ``sacct``, ``scontrol``, ``scancel``, ``srun``)
goes through :func:`run_command`.  It logs the argv at
DEBUG, applies a timeout, and converts every ``subprocess`` failure into a
:class:`~hpc_runner.core.exceptions.SchedulerError` subclass, so nothing
from ``subprocess`` escapes the scheduler layer.  Tests patch this function
instead of ``subprocess.run``.

When a trace is active (``hpc --trace DIR`` or ``[trace] enabled``), every
call, including one that timed out or found no binary, is handed to
:func:`hpc_runner.trace.record` (docs/dev/trace/DESIGN.md D-TR1).
"""

from __future__ import annotations

import errno
import logging
import os
import pty
import subprocess
import termios
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import IO

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
    on_stdout_line: Callable[[str], None] | None = None,
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
        on_stdout_line: Called with each line of stdout as the command
            prints it, for a command that says something and then goes on
            running (``sbatch --wait`` prints the job id, then waits for
            the job). stdout and stderr are captured as well; *capture*
            and *input* do not apply.

    Raises:
        SchedulerUnavailableError: ``argv[0]`` is not on PATH.
        SchedulerTimeoutError: The command exceeded *timeout*.
        SchedulerCommandError: Non-zero exit and *check* is true.
    """
    args = [str(a) for a in argv]
    logger.debug("run: %s", subprocess.list2cmdline(args))
    started = time.monotonic()
    try:
        if on_stdout_line is not None:
            result = _run_streaming(args, timeout=timeout, cwd=cwd, on_stdout_line=on_stdout_line)
        else:
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


def _run_streaming(
    args: list[str],
    *,
    timeout: float | None,
    cwd: Path | None,
    on_stdout_line: Callable[[str], None],
) -> subprocess.CompletedProcess[str]:
    """Run *args*, handing each stdout line to *on_stdout_line* as it arrives.

    The command's stdout is a pseudo-terminal, not a pipe: a command that
    writes through C stdio holds its lines back until it exits when its
    stdout is a pipe (``qsub -sync y`` printed the job id only when the job
    had ended), and prints each line at once to a terminal. stderr is a
    pipe.

    Raises:
        subprocess.TimeoutExpired: The command was killed after *timeout*
            seconds; carries what it had printed.
    """
    out: list[str] = []
    err: list[str] = []
    master, slave = pty.openpty()
    try:
        # No output processing: a newline stays a newline.
        attrs = termios.tcgetattr(slave)
        attrs[1] &= ~termios.OPOST
        termios.tcsetattr(slave, termios.TCSANOW, attrs)
        process = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=slave,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            cwd=cwd,
        )
    except BaseException:
        os.close(master)
        raise
    finally:
        os.close(slave)
    with process, os.fdopen(master, "rb", buffering=0) as stdout:
        assert process.stderr is not None
        # stderr is drained beside stdout so that its pipe never fills.
        drain = threading.Thread(target=err.extend, args=(process.stderr,), daemon=True)
        drain.start()
        timed_out = threading.Event()

        def kill() -> None:
            timed_out.set()
            process.kill()

        timer = threading.Timer(timeout, kill) if timeout is not None else None
        if timer is not None:
            timer.start()
        try:
            for line in _lines(stdout):
                out.append(line)
                on_stdout_line(line)
            process.wait()
        except BaseException:
            # As subprocess.run does: the command does not outlive an
            # interrupt or an error raised by the callback.
            process.kill()
            raise
        finally:
            if timer is not None:
                timer.cancel()
            drain.join()
    if timed_out.is_set():
        assert timeout is not None
        raise subprocess.TimeoutExpired(args, timeout, output="".join(out), stderr="".join(err))
    return subprocess.CompletedProcess(args, process.returncode, "".join(out), "".join(err))


def _lines(stream: IO[bytes]) -> Iterator[str]:
    """The lines written to a pseudo-terminal, each as soon as it is whole.

    Reading the master end fails with EIO once the command has closed its
    end, which is the end of the output.
    """
    pending = b""
    while True:
        try:
            chunk = stream.read(4096)
        except OSError as exc:
            if exc.errno != errno.EIO:
                raise
            chunk = b""
        if not chunk:
            break
        *whole, pending = (pending + chunk).split(b"\n")
        for line in whole:
            yield line.decode(errors="replace") + "\n"
    if pending:
        yield pending.decode(errors="replace")


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
