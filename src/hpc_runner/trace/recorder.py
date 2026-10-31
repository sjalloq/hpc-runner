"""Record scheduler calls as JSON files (docs/dev/trace/DESIGN.md D-TR1..D-TR7).

A :class:`Tracer` is installed once per process with :func:`activate`
(``hpc --trace DIR``) or :func:`activate_from_config` (``[trace] enabled =
true``, called by ``get_scheduler``). The subprocess seam calls
:func:`record` after every scheduler command; with no tracer active that is
a dictionary lookup and nothing else.

Each record is one file, ``<dir>/<YYYY-MM-DD>/<HHMMSS.mmm>-<pid>-<cmd>.json``,
with the keys the JSON fixtures under ``tests/unit/fixtures/`` use
(``argv``, ``returncode``, ``stdout``, ``stderr``) plus ``time``,
``elapsed``, ``pid``, ``cwd``, ``version`` and ``error``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from hpc_runner.core.exceptions import ConfigError

logger = logging.getLogger("hpc_runner.trace")

DEFAULT_TRACE_DIR = "~/.cache/hpc-runner/trace"
DEFAULT_MAX_DAYS = 14

#: ``[trace]`` keys and their types, for the config schema.
TRACE_KEYS: Mapping[str, type] = {
    "enabled": bool,
    "dir": str,
    "max_days": int,
    "dedupe": bool,
}

_DAY_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_active: Tracer | None = None


@dataclass
class Tracer:
    """Writes one JSON record per scheduler call under *root*."""

    root: Path
    dedupe: bool = True
    #: Last record written per argv, as a comparable tuple (D-TR5).
    _last: dict[tuple[str, ...], tuple[Any, ...]] = field(default_factory=dict, repr=False)

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
        """Write the record; return its path, or None when deduplicated or unwritable."""
        key = tuple(argv)
        signature = (returncode, stdout, stderr, error)
        if self.dedupe and self._last.get(key) == signature:
            return None
        self._last[key] = signature

        now = datetime.now()
        day = self.root / now.strftime("%Y-%m-%d")
        stem = f"{now.strftime('%H%M%S')}.{now.microsecond // 1000:03d}-{os.getpid()}-{_name(argv)}"
        path = day / f"{stem}.json"
        data = {
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
        try:
            day.mkdir(parents=True, exist_ok=True)
            # A second call in the same millisecond from the same pid is possible
            # (dedupe skips the common case); never overwrite.
            n = 0
            while path.exists():
                n += 1
                path = day / f"{stem}-{n}.json"
            path.write_text(json.dumps(data, indent=2) + "\n")
        except OSError as exc:
            logger.warning("trace: cannot write %s: %s", path, exc)
            return None
        return path

    def sweep(self, max_days: int) -> list[Path]:
        """Remove day directories older than *max_days*; return what was removed (D-TR6)."""
        cutoff = time.time() - max_days * 86400
        removed: list[Path] = []
        try:
            children = list(self.root.iterdir())
        except OSError:
            return removed
        for child in children:
            if not child.is_dir() or not _DAY_DIR.match(child.name):
                continue
            try:
                stamp = datetime.strptime(child.name, "%Y-%m-%d").timestamp()
            except ValueError:
                continue
            # The directory holds records written during that day; it is old
            # once the whole day is past the cutoff.
            if stamp + 86400 < cutoff:
                shutil.rmtree(child, ignore_errors=True)
                removed.append(child)
        return removed


def _name(argv: Sequence[str]) -> str:
    base = Path(argv[0]).name if argv else "cmd"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", base) or "cmd"


def _version() -> str:
    from hpc_runner import __version__

    return str(__version__)


def current() -> Tracer | None:
    """The active tracer, or None."""
    return _active


def activate(
    directory: str | Path, *, max_days: int = DEFAULT_MAX_DAYS, dedupe: bool = True
) -> Tracer:
    """Install a tracer writing under *directory* (``~`` expanded); sweep old days.

    Replaces any active tracer: the CLI flag calls this before the config is
    read, and :func:`activate_from_config` then leaves it alone (D-TR2).
    """
    global _active
    root = Path(directory).expanduser()
    tracer = Tracer(root=root, dedupe=dedupe)
    removed = tracer.sweep(max_days)
    if removed:
        logger.debug(
            "trace: swept %d day director%s", len(removed), "y" if len(removed) == 1 else "ies"
        )
    _active = tracer
    logger.debug("trace: recording to %s", root)
    return tracer


def deactivate() -> None:
    """Remove the active tracer (tests)."""
    global _active
    _active = None


def activate_from_config(trace: Mapping[str, Any]) -> Tracer | None:
    """Install a tracer from a validated ``[trace]`` table, unless one is active.

    Returns the tracer now active (possibly the pre-existing one), or None
    when the table does not enable tracing.

    Raises:
        ConfigError: ``max_days`` is below 1.
    """
    if _active is not None:
        return _active
    if not trace.get("enabled", False):
        return None
    max_days = int(trace.get("max_days", DEFAULT_MAX_DAYS))
    if max_days < 1:
        raise ConfigError(f"[trace] max_days: must be at least 1, got {max_days}")
    return activate(
        trace.get("dir", DEFAULT_TRACE_DIR),
        max_days=max_days,
        dedupe=bool(trace.get("dedupe", True)),
    )


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
