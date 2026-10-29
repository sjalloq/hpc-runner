"""Scheduler argument renderers."""

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

T = TypeVar("T")


# =============================================================================
# Scheduler Argument Base Class
# =============================================================================


class SchedulerArg(ABC, Generic[T]):
    """Base class for scheduler-specific argument renderers.

    Each scheduler backend (SGE, Slurm, PBS) will have subclasses that know
    how to render job attribute values into that scheduler's syntax.

    Subclasses must implement:
    - to_args(value) -> list of command-line arguments
    - to_directive(value) -> script directive string or None

    Example:
        class SGEJobNameArg(SchedulerArg[str]):
            def to_args(self, value):
                return ["-N", value] if value else []

            def to_directive(self, value):
                return f"#$ -N {value}" if value else None
    """

    def __init__(
        self,
        flag: str,
        *,
        doc: str = "",
        interactive_ok: bool = True,
    ):
        self.flag = flag
        self.doc = doc
        # False when the scheduler's interactive command (qrsh, srun) rejects
        # this flag; interactive argv builders skip such renderers.
        self.interactive_ok = interactive_ok

    @abstractmethod
    def to_args(self, value: T | None) -> list[str]:
        """Convert value to command-line arguments.

        Args:
            value: The job attribute value (may be None)

        Returns:
            List of command-line argument strings, empty list if value is None
        """

    @abstractmethod
    def to_directive(self, value: T | None) -> str | None:
        """Convert value to a script directive.

        Args:
            value: The job attribute value (may be None)

        Returns:
            Directive string (e.g., "#$ -N jobname") or None if value is None
        """

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} flag='{self.flag}'>"
