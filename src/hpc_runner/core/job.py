"""Job: a fully resolved, immutable job, ready for a scheduler to render."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from hpc_runner.core.array import ArrayRange
from hpc_runner.core.dependency import Dependency
from hpc_runner.core.resources import ResourceSet

# Order in which schedulers render the simple per-attribute directives.
RENDER_ORDER: tuple[str, ...] = (
    "shell",
    "use_cwd",
    "inherit_env",
    "name",
    "cpu",
    "mem",
    "time",
    "queue",
    "priority",
    "project",
    "nodes",
    "tasks",
    "stdout",
    "stderr",
)


@dataclass(frozen=True, kw_only=True)
class Job:
    """A resolved job.

    Built only by :func:`hpc_runner.resolve`, which applies config and
    package defaults to a :class:`~hpc_runner.JobSpec`. Schedulers render a
    ``Job`` and never mutate it.

    ``provenance`` records where each value came from. Layer names are
    ``"defaults"``, ``"tools.<name>"``, ``"tools.<name>.options.<key>"``,
    ``"types.<name>"``, ``"spec"``, ``"package"`` and ``"host"``.

    - Scalar and dict fields map to the one layer name that supplied the
      value (for dicts, the most specific layer that set any key).
    - List fields (``modules``, ``modules_path``, ``modules_path_append``,
      ``env_unset``, ``resources``) map to a tuple of ``(item, layer)``
      pairs, one per item in order; a resource item is its ``Resource``.
    - ``scheduler_args`` maps to a tuple of ``((scheduler, arg), layer)``
      pairs, schedulers in first-seen order.
    """

    command: str
    type: str | None
    name: str

    cpu: int | None
    mem: str | None
    time: str | None
    queue: str | None
    priority: int | None
    project: str | None
    nodes: int | None
    tasks: int | None
    stdout: str | None
    stderr: str | None
    inherit_env: bool
    workdir: str | None
    shell: str
    use_cwd: bool
    venv: str | None

    env_vars: Mapping[str, str]
    env_prepend: Mapping[str, str]
    env_append: Mapping[str, str]
    env_unset: tuple[str, ...]

    modules: tuple[str, ...]
    modules_path: tuple[str, ...]
    modules_path_append: tuple[str, ...]

    resources: ResourceSet
    scheduler_args: Mapping[str, tuple[str, ...]]
    dependencies: tuple[Dependency, ...]
    array: ArrayRange | None
    interactive: bool

    provenance: Mapping[str, str | tuple[tuple[Any, str], ...]]

    @property
    def merge_output(self) -> bool:
        """Whether stderr is merged into stdout (no separate stderr given)."""
        return self.stderr is None
