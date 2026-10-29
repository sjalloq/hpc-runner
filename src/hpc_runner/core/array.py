"""Array job ranges."""

from __future__ import annotations

import re
from dataclasses import dataclass

_RANGE = re.compile(r"^(\d+)(?:-(\d+))?(?::(\d+))?(?:%(\d+))?$")


@dataclass(frozen=True)
class ArrayRange:
    """An array job's task range: ``start-end[:step][%max_concurrent]``."""

    start: int
    end: int
    step: int = 1
    max_concurrent: int | None = None

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < self.start:
            raise ValueError(f"invalid array range {self.start}-{self.end}")
        if self.step < 1:
            raise ValueError(f"invalid array step {self.step}")
        if self.max_concurrent is not None and self.max_concurrent < 1:
            raise ValueError(f"invalid array concurrency limit {self.max_concurrent}")

    @classmethod
    def parse(cls, text: str) -> ArrayRange:
        """Parse ``"1-100"``, ``"1-100:10"``, ``"1-100%5"``, ``"1-100:10%5"`` or ``"7"``.

        Raises:
            ValueError: *text* is not a range in that grammar.
        """
        match = _RANGE.match(text.strip())
        if match is None:
            raise ValueError(
                f"invalid array range {text!r}; expected START-END[:STEP][%MAX], e.g. 1-100:10%5"
            )
        start, end, step, limit = match.groups()
        return cls(
            start=int(start),
            end=int(end) if end is not None else int(start),
            step=int(step) if step is not None else 1,
            max_concurrent=int(limit) if limit is not None else None,
        )

    @property
    def range_str(self) -> str:
        """Format as a scheduler range string."""
        s = f"{self.start}-{self.end}"
        if self.step != 1:
            s += f":{self.step}"
        if self.max_concurrent:
            s += f"%{self.max_concurrent}"
        return s

    @property
    def indices(self) -> range:
        """The task indices."""
        return range(self.start, self.end + 1, self.step)

    @property
    def count(self) -> int:
        """Number of array tasks."""
        return len(self.indices)
