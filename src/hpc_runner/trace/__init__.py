"""Scheduler trace: record every scheduler call, replay and export it.

The recorder (:mod:`.recorder`) hooks the one subprocess seam and writes a
JSON file per call. ``hpc trace compare`` (:mod:`.compare`) runs those
outputs back through the parsers; ``hpc trace bundle`` (:mod:`.bundle`)
scrubs and packs them for an issue report. Design and decisions:
``docs/dev/trace/DESIGN.md``.
"""

from hpc_runner.trace.recorder import (
    DEFAULT_MAX_DAYS,
    DEFAULT_TRACE_DIR,
    TRACE_KEYS,
    Tracer,
    activate,
    activate_from_config,
    current,
    deactivate,
    record,
)

__all__ = [
    "DEFAULT_MAX_DAYS",
    "DEFAULT_TRACE_DIR",
    "TRACE_KEYS",
    "Tracer",
    "activate",
    "activate_from_config",
    "current",
    "deactivate",
    "record",
]
