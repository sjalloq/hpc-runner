"""``hpc trace``: replay and export a scheduler trace (docs/dev/trace/DESIGN.md)."""

from __future__ import annotations

import rich_click as click


@click.group()
def trace_cmd() -> None:
    """Inspect a scheduler trace recorded with ``hpc --trace DIR`` or ``[trace]``."""
