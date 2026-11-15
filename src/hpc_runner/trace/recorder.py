"""Record what one ``hpc`` call did, as JSON files (docs/dev/trace/DESIGN.md).

A :class:`Tracer` is installed once per process with :func:`activate`
(``hpc --trace DIR``) or :func:`activate_from_config` (``[trace] enabled =
true``, called by ``get_scheduler``). Two kinds of record are written under
``<dir>/<YYYY-MM-DD>/``:

- One per scheduler command, ``<HHMMSS.mmm>-<pid>-<cmd>.json``. The
  subprocess seam calls :func:`record` after every command; with no tracer
  active that is a dictionary lookup and nothing else. Keys: the ones the
  JSON fixtures under ``tests/unit/fixtures/`` use (``argv``,
  ``returncode``, ``stdout``, ``stderr``) plus ``time``, ``elapsed``,
  ``pid``, ``cwd``, ``version``, ``error`` and, for the command that
  submitted a job script, ``script`` (its path and text).
- One per ``hpc`` command line, ``<HHMMSS.mmm>-<pid>-hpc.json``, written by
  the CLI when the process ends (:class:`Invocation`): the command line,
  the config files read, the whole environment, what ``hpc`` printed,
  the error it raised and its exit code.

Nothing is scrubbed: a trace holds real user, host and job names, and every
environment variable, including any that hold a token or password.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import traceback
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

logger = logging.getLogger("hpc_runner.trace")

DEFAULT_TRACE_DIR = "~/.cache/hpc-runner/trace"

#: ``[trace]`` keys and their types, for the config schema.
TRACE_KEYS: Mapping[str, type] = {
    "enabled": bool,
    "dir": str,
}

#: Terminal control sequences (colours, cursor moves, hyperlinks) removed
#: from recorded output.
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")

#: Recorded output of one ``hpc`` call is cut at this many characters.
OUTPUT_LIMIT = 1_000_000

_active: Tracer | None = None

#: Set by :func:`disable` (``hpc monitor``): no trace starts in this process.
_disabled = False

#: Job scripts written by a scheduler and not yet submitted: path -> text.
#: The record of the command whose argv names the path takes the text.
_scripts: dict[str, str] = {}


@dataclass
class Tracer:
    """Writes one JSON record per scheduler call under *root*."""

    root: Path
    #: Records written by this process, for the closing line on stderr.
    written: int = 0

    def record(
        self,
        argv: Sequence[str],
        *,
        returncode: int | None,
        stdout: str | None,
        stderr: str | None,
        elapsed: float,
        error: str | None = None,
    ) -> Path | None:
        """Write the record; return its path, or None when unwritable."""
        now = datetime.now()
        data: dict[str, Any] = {
            "argv": list(argv),
            "returncode": returncode,
            "stdout": stdout,
            "stderr": stderr,
            "error": error,
            "time": now.isoformat(timespec="milliseconds"),
            "elapsed": round(elapsed, 3),
            "pid": os.getpid(),
            "cwd": os.getcwd(),
            "version": _version(),
        }
        for arg in argv:
            if arg in _scripts:
                data["script"] = {"path": arg, "text": _scripts.pop(arg)}
                break
        return self.write(now, _name(argv), data)

    def write(self, when: datetime, name: str, data: Mapping[str, Any]) -> Path | None:
        """Write *data* as ``<day>/<time>-<pid>-<name>.json``; None when unwritable."""
        day = self.root / when.strftime("%Y-%m-%d")
        stem = f"{when.strftime('%H%M%S')}.{when.microsecond // 1000:03d}-{os.getpid()}-{name}"
        path = day / f"{stem}.json"
        try:
            day.mkdir(parents=True, exist_ok=True)
            # A second call in the same millisecond from the same pid is
            # possible; never overwrite.
            n = 0
            while path.exists():
                n += 1
                path = day / f"{stem}-{n}.json"
            path.write_text(json.dumps(data, indent=2) + "\n")
        except OSError as exc:
            logger.warning("trace: cannot write %s: %s", path, exc)
            return None
        self.written += 1
        return path


def _name(argv: Sequence[str]) -> str:
    base = Path(argv[0]).name if argv else "cmd"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", base) or "cmd"


def _version() -> str:
    from hpc_runner import __version__

    return str(__version__)


def current() -> Tracer | None:
    """The active tracer, or None."""
    return _active


def activate(directory: str | Path) -> Tracer:
    """Install a tracer writing under *directory* (``~`` expanded).

    Replaces any active tracer: the CLI flag calls this before the config is
    read, and :func:`activate_from_config` then leaves it alone. Nothing
    under *directory* is ever removed: the records are the user's to keep
    or delete.
    """
    global _active
    root = Path(directory).expanduser()
    tracer = Tracer(root=root)
    _active = tracer
    logger.debug("trace: recording to %s", root)
    return tracer


def deactivate() -> None:
    """Remove the active tracer and any :func:`disable` (tests)."""
    global _active, _disabled
    _active = None
    _disabled = False
    _scripts.clear()


def disable() -> None:
    """Record nothing in this process, whatever ``[trace]`` says.

    Called for ``hpc monitor``: a trace is for debugging what a command
    submitted or reported once, and the monitor asks the scheduler the same
    question every few seconds for as long as it is open.
    """
    global _active, _disabled
    _active = None
    _disabled = True
    _scripts.clear()


def activate_from_config(trace: Mapping[str, Any]) -> Tracer | None:
    """Install a tracer from a validated ``[trace]`` table, unless one is active.

    Returns the tracer now active (possibly the pre-existing one), or None
    when the table does not enable tracing.
    """
    if _disabled:
        return None
    if _active is not None:
        return _active
    if not trace.get("enabled", False):
        return None
    return activate(trace.get("dir", DEFAULT_TRACE_DIR))


def record(
    argv: Sequence[str],
    *,
    returncode: int | None,
    stdout: str | None,
    stderr: str | None,
    elapsed: float,
    error: str | None = None,
) -> None:
    """Record one scheduler call on the active tracer, if any (called by the seam)."""
    if _active is not None:
        _active.record(
            argv,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            elapsed=elapsed,
            error=error,
        )


def note_script(path: str | Path, text: str) -> None:
    """Remember a job script a scheduler is about to submit.

    The record of the command whose argv names *path* carries the text, so
    the trace shows the directives and module loads the scheduler saw even
    though the file deletes itself. Nothing is kept when no trace is active.
    """
    if _active is not None:
        _scripts[str(path)] = text


class _Tee:
    """A text stream that passes everything on to *stream* and keeps a copy."""

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream
        self._parts: list[str] = []
        self._size = 0
        self.truncated = False

    def write(self, text: str) -> int:
        if not isinstance(text, str):
            # A text stream, like the one wrapped: click probes with bytes
            # to tell the two apart.
            raise TypeError(f"write() argument must be str, not {type(text).__name__}")
        if self._size < OUTPUT_LIMIT:
            self._parts.append(text[: OUTPUT_LIMIT - self._size])
            self._size += len(text)
            self.truncated = self._size > OUTPUT_LIMIT
        else:
            self.truncated = self.truncated or bool(text)
        return self._stream.write(text)

    def text(self) -> str:
        return _ANSI.sub("", "".join(self._parts))

    def __getattr__(self, name: str) -> Any:
        # isatty, fileno, flush, encoding: the terminal's own answers, so
        # colour and width detection do not change under a trace.
        return getattr(self._stream, name)


class Invocation:
    """One ``hpc`` command line, recorded when the process ends.

    The CLI entry point creates one, runs the command inside
    :meth:`capture` and calls :meth:`finish`. The record is written only if
    a tracer is active by then (the flag or ``[trace]``), so an untraced
    call costs two stream wrappers and nothing else.
    """

    def __init__(self, argv: Sequence[str]) -> None:
        self.argv = [str(a) for a in argv]
        self.started = datetime.now()
        self._clock = time.monotonic()
        self._stdout: _Tee | None = None
        self._stderr: _Tee | None = None

    def capture(self) -> None:
        """Start copying what is written to ``sys.stdout`` and ``sys.stderr``."""
        self._stdout = _Tee(sys.stdout)
        self._stderr = _Tee(sys.stderr)
        sys.stdout = self._stdout
        sys.stderr = self._stderr

    def release(self) -> None:
        """Put the real streams back (before a full-screen UI, and at the end)."""
        if self._stdout is not None and sys.stdout is self._stdout:
            sys.stdout = self._stdout._stream
        if self._stderr is not None and sys.stderr is self._stderr:
            sys.stderr = self._stderr._stream

    def finish(
        self,
        *,
        exit_code: int,
        error: BaseException | None = None,
        config_files: Sequence[Path] = (),
    ) -> Path | None:
        """Release the streams and write the record; None when no trace is active.

        Args:
            exit_code: What the process is about to exit with.
            error: The exception that ended the command, if one did. An
                ``HPCToolsError`` is recorded by type and message; anything
                else is a bug and is recorded with its traceback.
            config_files: The config files read, in merge order.
        """
        self.release()
        tracer = _active
        if tracer is None:
            return None
        data: dict[str, Any] = {
            "kind": "invocation",
            "argv": self.argv,
            "exit_code": exit_code,
            "stdout": self._stdout.text() if self._stdout is not None else None,
            "stderr": self._stderr.text() if self._stderr is not None else None,
            "output_truncated": bool(
                (self._stdout is not None and self._stdout.truncated)
                or (self._stderr is not None and self._stderr.truncated)
            ),
            "error": _describe(error),
            "config": [_read(path) for path in config_files],
            # All of it: a scheduler's own variables (SBATCH_*, SGE_ROOT, ...)
            # change what a command does, and no list of prefixes stays complete.
            "env": dict(sorted(os.environ.items())),
            "time": self.started.isoformat(timespec="milliseconds"),
            "elapsed": round(time.monotonic() - self._clock, 3),
            "pid": os.getpid(),
            "cwd": os.getcwd(),
            "version": _version(),
        }
        path = tracer.write(self.started, "hpc", data)
        if path is not None:
            # Said every time, on the real stderr (after the record, so not
            # in it): a trace left on in a config is otherwise silent, and
            # nothing removes what it writes.
            noun = "record" if tracer.written == 1 else "records"
            print(f"trace: {tracer.written} {noun} written to {path.parent}", file=sys.stderr)
        return path


def _describe(error: BaseException | None) -> dict[str, str] | None:
    if error is None:
        return None
    from hpc_runner.core.exceptions import HPCToolsError

    described = {"type": type(error).__name__, "message": str(error)}
    if not isinstance(error, HPCToolsError):
        described["traceback"] = "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        )
    return described


def _read(path: Path) -> dict[str, str | None]:
    try:
        return {"path": str(path), "text": path.read_text()}
    except OSError as exc:
        return {"path": str(path), "text": None, "error": str(exc)}
