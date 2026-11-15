"""Trace: record what an ``hpc`` call ran, read and printed.

The recorder (:mod:`.recorder`) hooks the one subprocess seam and writes a
JSON file per scheduler command, and the CLI adds one per ``hpc`` command
line. Design and decisions: ``docs/dev/trace/DESIGN.md``.
"""

from hpc_runner.trace.recorder import (
    DEFAULT_TRACE_DIR,
    TRACE_KEYS,
    Invocation,
    Tracer,
    activate,
    activate_from_config,
    current,
    deactivate,
    disable,
    note_script,
    record,
)

__all__ = [
    "DEFAULT_TRACE_DIR",
    "TRACE_KEYS",
    "Invocation",
    "Tracer",
    "activate",
    "activate_from_config",
    "current",
    "deactivate",
    "disable",
    "note_script",
    "record",
]
