"""Parsers for Slurm command output."""

from __future__ import annotations

import re

_SUBMITTED = re.compile(r"^Submitted batch job (\d+)", re.MULTILINE)


def parse_sbatch_output(output: str) -> str | None:
    """The job id in ``sbatch``'s ``Submitted batch job <id>`` line, or None.

    An array job is submitted under one id, printed the same way.
    """
    match = _SUBMITTED.search(output)
    return match.group(1) if match else None
