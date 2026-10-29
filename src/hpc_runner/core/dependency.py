"""Job dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DependencyType(str, Enum):
    """Job dependency types.

    These map to scheduler-specific dependency modes:
    - SGE: -hold_jid (afterany), with afterok emulated by an exit sentinel
    - Slurm: --dependency=afterok, afterany, after, afternotok
    """

    AFTEROK = "afterok"  # Run after all dependencies complete successfully
    AFTERANY = "afterany"  # Run after all dependencies complete (success or failure)
    AFTER = "after"  # Run after all dependencies start
    AFTERNOTOK = "afternotok"  # Run after any dependency fails

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class Dependency:
    """One parent job and how this job depends on it."""

    job_id: str
    type: DependencyType = DependencyType.AFTEROK
