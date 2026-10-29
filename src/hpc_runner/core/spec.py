"""JobSpec: exactly what the caller asked for, before any config is applied."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import KW_ONLY, dataclass, field
from typing import TYPE_CHECKING, Any

from hpc_runner.core.dependency import Dependency, DependencyType
from hpc_runner.core.merge import Edit, Set
from hpc_runner.core.resources import ResourceSet

if TYPE_CHECKING:
    from hpc_runner.core.result import JobResult


@dataclass
class JobSpec:
    """A job request as the caller wrote it.

    ``None`` means "not specified": ``resolve()`` fills it from config, then
    from the package default. List fields merge with config exactly as a
    config layer does (D1): a plain list appends to the config's list,
    ``Set([...])`` replaces it, ``Edit(remove=[...], add=[...])`` edits it.
    A spec does no config or environment access and no validation beyond
    types; it may be built up incrementally.

    Example::

        spec = JobSpec("python train.py", cpu=4, mem="16G")
        job = resolve(spec, config, Host.capture())
    """

    command: str | Sequence[str]
    _: KW_ONLY
    type: str | None = None

    name: str | None = None
    cpu: int | None = None
    mem: str | None = None
    time: str | None = None
    queue: str | None = None
    priority: int | None = None
    project: str | None = None
    nodes: int | None = None
    tasks: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    inherit_env: bool | None = None
    workdir: str | None = None
    shell: str | None = None
    use_cwd: bool | None = None

    # None = unspecified (capture the submit host's venv), str = that venv,
    # False = no venv.
    venv: str | bool | None = None

    env_vars: dict[str, str] | None = None
    env_prepend: dict[str, str] | None = None
    env_append: dict[str, str] | None = None
    env_unset: list[str] | Set | Edit | None = None
    # Submit-host variables passed explicitly when inherit_env is false.
    env_passthrough: list[str] | Set | Edit | None = None

    modules: list[str] | Set | Edit | None = None
    modules_path: list[str] | Set | Edit | None = None
    modules_path_append: list[str] | Set | Edit | None = None

    resources: ResourceSet | list[dict[str, Any]] | Set | Edit | None = None

    # Raw per-scheduler passthrough, e.g. {"sge": ["-l", "gpu=2"]}; each list
    # merges with config's per D1. Only the active scheduler's list is used.
    scheduler_args: dict[str, list[str] | Set | Edit] | None = None

    dependencies: list[Dependency] = field(default_factory=list)
    array: str | None = None  # "1-100", "1-100:10", "1-100%5"; arrays are always batch
    interactive: bool | None = None

    def after(
        self,
        *results: JobResult,
        type: DependencyType = DependencyType.AFTEROK,
    ) -> JobSpec:
        """Depend on *results* with dependency *type*; returns self for chaining.

        Each call appends, so ``spec.after(a).after(b, type=AFTERANY)`` keeps
        ``a`` afterok and makes ``b`` afterany.
        """
        for result in results:
            if result.job_id is None:
                raise ValueError("cannot depend on an interactive run: it has no job id")
            self.dependencies.append(Dependency(result.job_id, type))
        return self
