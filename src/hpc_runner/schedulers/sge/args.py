"""SGE-specific argument renderers.

Each class knows how to render a single job attribute to SGE syntax,
both as a script directive (#$ ...) for batch jobs and as command-line
arguments for qrsh. Renderers for flags qrsh rejects (-S, -o, -e, -j, -N)
are declared ``interactive_ok=False``.
"""

from hpc_runner.core.descriptors import SchedulerArg


class SGEArg(SchedulerArg[str]):
    """Base class for SGE arguments.

    SGE uses:
    - Directives: #$ -flag value
    - CLI args: -flag value
    """

    def to_args(self, value: str | None) -> list[str]:
        if value is None:
            return []
        return [f"-{self.flag}", str(value)]

    def to_directive(self, value: str | None) -> str | None:
        if value is None:
            return None
        return f"#$ -{self.flag} {value}"


# =============================================================================
# Simple Flag Arguments
# =============================================================================


class SGEJobNameArg(SGEArg):
    """Job name: -N name"""

    def __init__(self) -> None:
        super().__init__("N", doc="Job name", interactive_ok=False)


class SGEQueueArg(SGEArg):
    """Queue selection: -q queue_name"""

    def __init__(self) -> None:
        super().__init__("q", doc="Queue/partition name")


class SGEOutputArg(SGEArg):
    """Stdout path: -o path"""

    def __init__(self) -> None:
        super().__init__("o", doc="Stdout file path", interactive_ok=False)


class SGEErrorArg(SGEArg):
    """Stderr path: -e path"""

    def __init__(self) -> None:
        super().__init__("e", doc="Stderr file path", interactive_ok=False)


class SGEPriorityArg(SGEArg):
    """Job priority: -p priority"""

    def __init__(self) -> None:
        super().__init__("p", doc="Job priority (-1023 to 1024)")


class SGEProjectArg(SGEArg):
    """Project: -P project"""

    def __init__(self) -> None:
        super().__init__("P", doc="Project")


class SGEShellArg(SGEArg):
    """Shell selection: -S /path/to/shell"""

    def __init__(self) -> None:
        super().__init__("S", doc="Shell path", interactive_ok=False)


# =============================================================================
# Boolean Flag Arguments (no value, just presence)
# =============================================================================


class SGECwdArg(SchedulerArg[bool]):
    """Use current working directory: -cwd"""

    def __init__(self) -> None:
        super().__init__("cwd", doc="Execute in current working directory")

    def to_args(self, value: bool | None) -> list[str]:
        return ["-cwd"] if value else []

    def to_directive(self, value: bool | None) -> str | None:
        return "#$ -cwd" if value else None


class SGEInheritEnvArg(SchedulerArg[bool]):
    """Inherit environment: -V"""

    def __init__(self) -> None:
        super().__init__("V", doc="Inherit environment variables")

    def to_args(self, value: bool | None) -> list[str]:
        return ["-V"] if value else []

    def to_directive(self, value: bool | None) -> str | None:
        return "#$ -V" if value else None


class SGEMergeOutputArg(SchedulerArg[bool]):
    """Merge stdout and stderr: -j y"""

    def __init__(self) -> None:
        super().__init__("j", doc="Join stdout and stderr", interactive_ok=False)

    def to_args(self, value: bool | None) -> list[str]:
        return ["-j", "y"] if value else []

    def to_directive(self, value: bool | None) -> str | None:
        return "#$ -j y" if value else None


# =============================================================================
# Resource Arguments (configurable resource names)
# =============================================================================


class SGECpuArg(SchedulerArg[int]):
    """Parallel environment slots: -pe <pe_name> <slots>

    The PE name is configurable per-cluster (e.g., 'smp', 'mpi', 'orte').
    """

    def __init__(self, pe_name: str = "smp"):
        super().__init__("pe", doc=f"Parallel environment ({pe_name})")
        self.pe_name = pe_name

    def to_args(self, value: int | None) -> list[str]:
        if value is None:
            return []
        return ["-pe", self.pe_name, str(value)]

    def to_directive(self, value: int | None) -> str | None:
        if value is None:
            return None
        return f"#$ -pe {self.pe_name} {value}"


class SGEMemArg(SchedulerArg[str]):
    """Memory request: -l <resource>=<value>

    The resource name is configurable (e.g., 'mem_free', 'h_vmem', 'mem').
    """

    def __init__(self, resource_name: str = "mem_free"):
        super().__init__("l", doc=f"Memory ({resource_name})")
        self.resource_name = resource_name

    def to_args(self, value: str | None) -> list[str]:
        if value is None:
            return []
        return ["-l", f"{self.resource_name}={value}"]

    def to_directive(self, value: str | None) -> str | None:
        if value is None:
            return None
        return f"#$ -l {self.resource_name}={value}"


class SGETimeArg(SchedulerArg[str]):
    """Time limit: -l <resource>=<HH:MM:SS>

    The resource name is configurable (e.g., 'h_rt', 's_rt').
    """

    def __init__(self, resource_name: str = "h_rt"):
        super().__init__("l", doc=f"Time limit ({resource_name})")
        self.resource_name = resource_name

    def to_args(self, value: str | None) -> list[str]:
        if value is None:
            return []
        return ["-l", f"{self.resource_name}={value}"]

    def to_directive(self, value: str | None) -> str | None:
        if value is None:
            return None
        return f"#$ -l {self.resource_name}={value}"


# =============================================================================
# Array Job Arguments
# =============================================================================


class SGEArrayArg(SchedulerArg[str]):
    """Array job range: -t range

    Range formats: 1-100, 1-100:10, 1,2,3,4
    """

    def __init__(self) -> None:
        super().__init__("t", doc="Array job range")

    def to_args(self, value: str | None) -> list[str]:
        if value is None:
            return []
        return ["-t", value]

    def to_directive(self, value: str | None) -> str | None:
        if value is None:
            return None
        return f"#$ -t {value}"


# =============================================================================
# Dependency Arguments
# =============================================================================


class SGEHoldArg(SchedulerArg[str]):
    """Job dependency: -hold_jid job_id[,job_id,...]"""

    def __init__(self) -> None:
        super().__init__("hold_jid", doc="Hold until jobs complete")

    def to_args(self, value: str | None) -> list[str]:
        if value is None:
            return []
        return ["-hold_jid", value]

    def to_directive(self, value: str | None) -> str | None:
        if value is None:
            return None
        return f"#$ -hold_jid {value}"
