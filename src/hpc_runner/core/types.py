"""Type aliases for hpc-runner."""

from pathlib import Path
from typing import TypeAlias

# Path types
PathLike: TypeAlias = str | Path

# Command types
Command: TypeAlias = str | list[str]
