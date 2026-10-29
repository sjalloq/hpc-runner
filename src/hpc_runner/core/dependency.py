"""Job dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hpc_runner.core.result import JobResult


class DependencyType(str, Enum):
    """Job dependency types.

    These map to scheduler-specific dependency modes:
    - SGE: ``-hold_jid`` (afterany); afterok and afternotok are emulated by
      an exit-code sentinel guard; ``after`` cannot be expressed
    - Slurm: --dependency=afterok, afterany, after, afternotok
    """

    AFTEROK = "afterok"  # Run after all dependencies complete successfully
    AFTERANY = "afterany"  # Run after all dependencies complete (success or failure)
    AFTER = "after"  # Run after all dependencies start
    AFTERNOTOK = "afternotok"  # Run after the dependencies fail

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class Dependency:
    """One parent job and how this job depends on it.

    ``tasks`` is the parent's array task indices when the parent is an array
    job (``None`` otherwise); a dependent needs them to check every task's
    exit sentinel.
    """

    job_id: str
    type: DependencyType = DependencyType.AFTEROK
    tasks: tuple[int, ...] | None = None


def dependency_on(result: JobResult, type: DependencyType = DependencyType.AFTEROK) -> Dependency:
    """A dependency on a submitted job's *result*, recording an array's tasks.

    Raises:
        ValueError: *result* is a finished interactive run (it has no job id).
    """
    from hpc_runner.core.result import ArrayJobResult

    if result.job_id is None:
        raise ValueError("cannot depend on an interactive run: it has no job id")
    tasks = tuple(result.array.indices) if isinstance(result, ArrayJobResult) else None
    return Dependency(result.job_id, type, tasks)


def parse_dependency_string(text: str) -> list[Dependency]:
    """Parse Slurm's dependency grammar into a list of :class:`Dependency`.

    ``TYPE:ID[:ID...][,TYPE:ID...]``; an element that is a bare ``ID`` (no
    type) is ``afterok``. For example ``afterok:1:2,afterany:3`` is three
    dependencies.

    A string carries only job ids, so a parent that is an array job is
    recorded without its task list (``tasks=None``); see the comment below
    for how such a parent is checked.

    Raises:
        ValueError: *text* is not in that grammar or names an unknown type.
    """
    # A parent named by bare id may be an array job. Its guard on SGE first
    # looks for <id>.rc; if that is absent after the bounded wait it collects
    # every <exit_dir>/<id>.*.rc that exists, requires at least one, and
    # applies the parent's test to every file found. -hold_jid guarantees every
    # task has exited and published before the dependent starts, so the glob
    # is complete when it runs.
    valid = ", ".join(t.value for t in DependencyType)

    def bad(reason: str) -> ValueError:
        return ValueError(
            f"{reason} in {text!r}; expected ID or TYPE:ID[:ID...][,TYPE:ID...] "
            f"with TYPE one of {valid}"
        )

    if not text or any(c.isspace() for c in text):
        raise bad("empty value or whitespace")
    dependencies: list[Dependency] = []
    for element in text.split(","):
        parts = element.split(":")
        if len(parts) == 1:
            if not parts[0]:
                raise bad("empty element")
            if parts[0] in {t.value for t in DependencyType}:
                raise bad(f"no job id after {parts[0]!r}")
            dependencies.append(Dependency(parts[0], DependencyType.AFTEROK))
            continue
        try:
            dep_type = DependencyType(parts[0])
        except ValueError:
            raise bad(f"unknown dependency type {parts[0]!r}") from None
        ids = parts[1:]
        if not all(ids):
            raise bad("empty job id")
        dependencies.extend(Dependency(job_id, dep_type) for job_id in ids)
    return dependencies
