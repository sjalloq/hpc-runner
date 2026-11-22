"""Slurm argument renderers.

Each class renders one job attribute as the words ``sbatch`` and ``srun``
take. A batch job carries them as one ``#SBATCH`` line of the script, an
interactive one as arguments to ``srun``. The output-file options are never
sent to ``srun`` and are declared ``interactive_ok=False``.
"""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Mapping, Sequence
from typing import TypeVar

from hpc_runner.core.array import ArrayRange
from hpc_runner.core.dependency import Dependency
from hpc_runner.core.descriptors import SchedulerArg
from hpc_runner.core.exceptions import SubmissionError
from hpc_runner.core.units import Duration, Memory

T = TypeVar("T")

#: Accepted values of ``[schedulers.slurm] memory_per``.
MEMORY_PER = ("job", "cpu")


class SlurmArg(SchedulerArg[T]):
    """Base class for Slurm arguments: :meth:`words` gives both forms."""

    @abstractmethod
    def words(self, value: T) -> list[str]:
        """The command-line words for *value* (never None)."""

    def to_args(self, value: T | None) -> list[str]:
        return [] if value is None else self.words(value)

    def to_directive(self, value: T | None) -> str | None:
        if value is None:
            return None
        words = self.words(value)
        return "#SBATCH " + " ".join(words) if words else None


class SlurmOptionArg(SlurmArg[str]):
    """One long option with a value: ``--<flag>=<value>``."""

    def words(self, value: str) -> list[str]:
        return [f"--{self.flag}={value}"]


class SlurmJobNameArg(SlurmOptionArg):
    """Job name: ``--job-name=name``"""

    def __init__(self) -> None:
        super().__init__("job-name", doc="Job name")


class SlurmPartitionArg(SlurmOptionArg):
    """Queue: ``--partition=name``"""

    def __init__(self) -> None:
        super().__init__("partition", doc="Partition")


class SlurmAccountArg(SlurmOptionArg):
    """Project: ``--account=name``"""

    def __init__(self) -> None:
        super().__init__("account", doc="Account")


class SlurmChdirArg(SlurmOptionArg):
    """Working directory: ``--chdir=path``"""

    def __init__(self) -> None:
        super().__init__("chdir", doc="Working directory")


class SlurmOutputArg(SlurmOptionArg):
    """Stdout path: ``--output=path``"""

    def __init__(self) -> None:
        super().__init__("output", doc="Stdout file path", interactive_ok=False)


class SlurmErrorArg(SlurmOptionArg):
    """Stderr path: ``--error=path``"""

    def __init__(self) -> None:
        super().__init__("error", doc="Stderr file path", interactive_ok=False)


class SlurmExportArg(SlurmOptionArg):
    """Environment: ``--export=ALL``, ``--export=NONE`` or ``--export=NAME=value,...``

    The value is built by :func:`export_value`.
    """

    def __init__(self) -> None:
        super().__init__("export", doc="Environment passed to the job")


def export_value(inherit_env: bool, env_passthrough: Mapping[str, str]) -> str:
    """The value of ``--export`` for a job.

    ``ALL`` with *inherit_env*; without it ``NONE``, or the variables of
    *env_passthrough* as ``NAME=value,...``.

    Raises:
        SubmissionError: A passed-through value contains ``,``, which
            separates the variables of the list.
    """
    if inherit_env:
        return "ALL"
    if not env_passthrough:
        return "NONE"
    for name, value in env_passthrough.items():
        if "," in value:
            raise SubmissionError(
                f"env_passthrough: the value of {name} contains ',' ({value!r}); "
                "--export=NAME=value,... separates variables with it"
            )
    return ",".join(f"{name}={value}" for name, value in env_passthrough.items())


class SlurmCpuArg(SlurmArg[int]):
    """CPUs of the job's one task: ``--cpus-per-task=N --ntasks=1``"""

    def __init__(self) -> None:
        super().__init__("cpus-per-task", doc="CPUs for the job's one task")

    def words(self, value: int) -> list[str]:
        return [f"--cpus-per-task={value}", "--ntasks=1"]


class SlurmMemArg(SlurmArg[Memory]):
    """Memory in whole megabytes, rounded up: ``--mem=MB`` or ``--mem-per-cpu=MB``.

    ``mem`` is per job and is requested as ``--mem``. With ``memory_per =
    "cpu"`` :meth:`for_job` divides it by the job's CPUs and it is requested
    as ``--mem-per-cpu``. A ``Memory`` is at least one byte, so the number
    is never 0.
    """

    def __init__(self, memory_per: str = "job"):
        super().__init__(
            "mem-per-cpu" if memory_per == "cpu" else "mem", doc=f"Memory (per {memory_per})"
        )
        self.memory_per = memory_per

    def for_job(self, mem: Memory, cpu: int) -> Memory:
        """The amount to request for a job of *mem* on *cpu* CPUs."""
        return mem / cpu if self.memory_per == "cpu" else mem

    def words(self, value: Memory) -> list[str]:
        return [f"--{self.flag}={value.to_slurm_mb()}"]


class SlurmTimeArg(SlurmArg[Duration]):
    """Time limit: ``--time=D-HH:MM:SS``"""

    def __init__(self) -> None:
        super().__init__("time", doc="Time limit")

    def words(self, value: Duration) -> list[str]:
        return [f"--time={value.to_slurm()}"]


class SlurmArrayArg(SlurmArg[ArrayRange]):
    """Array tasks: ``--array=START-END[:STEP][%MAX]``"""

    def __init__(self) -> None:
        super().__init__("array", doc="Array task range", interactive_ok=False)

    def words(self, value: ArrayRange) -> list[str]:
        limit = f"%{value.max_concurrent}" if value.max_concurrent is not None else ""
        return [f"--array={value.task_range}{limit}"]


class SlurmDependencyArg(SlurmArg[Sequence[Dependency]]):
    """Dependencies: ``--dependency=afterok:1:2,afterany:3``

    The parents of one type share an element, types in the order they first
    appear. With *kill_invalid* ``--kill-on-invalid-dep=yes`` follows.
    """

    def __init__(self, kill_invalid: bool = True) -> None:
        super().__init__("dependency", doc="Job dependencies", interactive_ok=False)
        self.kill_invalid = kill_invalid

    def words(self, value: Sequence[Dependency]) -> list[str]:
        if not value:
            return []
        by_type: dict[str, dict[str, None]] = {}
        for dep in value:
            by_type.setdefault(dep.type.value, {})[dep.job_id] = None
        spec = ",".join(":".join([kind, *ids]) for kind, ids in by_type.items())
        words = [f"--dependency={spec}"]
        if self.kill_invalid:
            words.append("--kill-on-invalid-dep=yes")
        return words


class SlurmLicensesArg(SlurmArg[Mapping[str, int]]):
    """Licences, all in one option: ``-L name:N,name:N``"""

    def __init__(self) -> None:
        super().__init__("L", doc="Licences")

    def words(self, value: Mapping[str, int]) -> list[str]:
        if not value:
            return []
        return ["-L", ",".join(f"{name}:{count}" for name, count in value.items())]


class SlurmDevicesArg(SlurmArg[Mapping[str, int]]):
    """Devices, all in one option: ``--gres=name:N,name:N``"""

    def __init__(self) -> None:
        super().__init__("gres", doc="Devices")

    def words(self, value: Mapping[str, int]) -> list[str]:
        if not value:
            return []
        return ["--gres=" + ",".join(f"{name}:{count}" for name, count in value.items())]


class SlurmFeaturesArg(SlurmArg[Sequence[str]]):
    """Features, all in one option: ``-C name&name``"""

    def __init__(self) -> None:
        super().__init__("C", doc="Features")

    def words(self, value: Sequence[str]) -> list[str]:
        if not value:
            return []
        return ["-C", "&".join(value)]
