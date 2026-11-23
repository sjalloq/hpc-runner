"""Job: a fully resolved, immutable job, ready for a scheduler to render."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from hpc_runner.core.array import ArrayRange
from hpc_runner.core.dependency import Dependency
from hpc_runner.core.units import Duration, Memory

# Order in which schedulers render the simple per-attribute directives.
RENDER_ORDER: tuple[str, ...] = (
    "inherit_env",
    "name",
    "cpu",
    "mem",
    "time",
    "queue",
    "project",
    "stdout",
    "stderr",
)


#: The options a scheduler has to place, as opposed to those the job script
#: carries the same way everywhere (modules, ``env_*``, venv). Every
#: scheduler says, for a batch and for an interactive job, whether it maps
#: or rejects each one (``BaseScheduler.BATCH_OPTIONS``). ``scheduler_args``
#: is among them: a scheduler passes the raw arguments on, or has nothing
#: to pass them to.
SCHEDULER_OPTIONS: tuple[str, ...] = (
    "name",
    "cpu",
    "mem",
    "time",
    "queue",
    "project",
    "stdout",
    "stderr",
    "inherit_env",
    "env_passthrough",
    "workdir",
    "licenses",
    "devices",
    "features",
    "array",
    "dependencies",
    "scheduler_args",
)


@dataclass(frozen=True, kw_only=True)
class Job:
    """A resolved job.

    Built only by :func:`hpc_runner.resolve`, which applies config and
    package defaults to a :class:`~hpc_runner.JobSpec`. Schedulers render a
    ``Job`` and never mutate it.

    ``command`` and ``command_words`` hold the command as written, its
    ``${hpc.*}`` references included; a scheduler turns them into the
    script's command line (``BaseScheduler.render_command``).

    ``provenance`` records where each value came from. Layer names are
    ``"defaults"``, ``"tools.<name>"``, ``"tools.<name>.options.<key>"``,
    ``"types.<name>"``, ``"spec"``, ``"package"`` and ``"host"``.

    - A field that overrides (every field but the three below) maps to the
      one layer name that supplied its value, lists and tables included:
      the whole value is that layer's.
    - ``env_passthrough`` maps to a tuple of ``(name, layer)`` pairs, every
      pair with the one layer that set the list. It lists the names even
      when ``inherit_env`` makes them redundant and the field itself is
      empty.
    - ``modules`` adds up across layers and maps to a tuple of
      ``(module, layer)`` pairs, one per module in order.
    - ``licenses`` and ``devices`` add up too and map to a tuple of
      ``((name, count), layer)`` pairs: the layer that set that count.
    - ``venv`` is ``"captured from VIRTUAL_ENV"``, the layer that set the
      path, ``"<layer> (disabled)"`` for ``venv = false``, or ``"package"``.
    """

    # The command as one string: what is shown, traced and named after.
    command: str
    # The words of a command given as a list, None for one given as a
    # string. The scheduler renders the script's command line from these.
    command_words: tuple[str, ...] | None
    type: str | None
    name: str

    cpu: int | None
    mem: Memory | None  # per job (D10)
    time: Duration | None
    queue: str | None
    project: str | None
    # Output path patterns (D9), relative to workdir; may contain ${hpc.*}.
    # stderr None means merged into stdout.
    stdout: str
    stderr: str | None
    inherit_env: bool
    workdir: str  # absolute
    venv: str | None

    env_vars: Mapping[str, str]
    env_prepend: Mapping[str, str]
    env_append: Mapping[str, str]
    env_unset: tuple[str, ...]
    # NAME -> value captured from the submit host; empty with inherit_env.
    env_passthrough: Mapping[str, str]

    modules: tuple[str, ...]
    modules_path: tuple[str, ...]
    modules_path_append: tuple[str, ...]

    # What the job asks the scheduler for by name: licences and devices
    # with a count (a whole number, 1 or more), features by name alone.
    licenses: Mapping[str, int]
    devices: Mapping[str, int]
    features: tuple[str, ...]
    # Raw arguments for the scheduler; the local scheduler ignores them.
    scheduler_args: tuple[str, ...]
    dependencies: tuple[Dependency, ...]
    array: ArrayRange | None
    interactive: bool

    provenance: Mapping[str, str | tuple[tuple[Any, str], ...]]

    @property
    def merge_output(self) -> bool:
        """Whether stderr is merged into stdout (no separate stderr given)."""
        return self.stderr is None
