"""Value types for a job's memory and time limit (D10).

hpc-runner owns the format: a value is parsed once (in ``resolve()``) and
rendered per scheduler, so ``16GiB``, ``1d`` and ``90m`` mean the same thing
on every cluster.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

_KIB = 1024
# Binary units, largest first: SGE's G is 2**30, as is ours.
_UNITS: tuple[tuple[str, int], ...] = (
    ("T", _KIB**4),
    ("G", _KIB**3),
    ("M", _KIB**2),
    ("K", _KIB),
)
_UNIT_BYTES = dict(_UNITS)

_MEMORY = re.compile(r"(\d+(?:\.\d+)?)([KMGT](?:I?B)?)?", re.IGNORECASE)


def _ceil_div(a: int, b: int) -> int:
    return -(-a // b)


@dataclass(frozen=True)
class Memory:
    """An amount of memory, in bytes.

    ``mem`` means per job (D10); a scheduler that requests memory per slot
    divides it by the slot count when rendering.
    """

    bytes_: int

    @classmethod
    def parse(cls, text: str) -> Memory:
        """Parse ``16G``, ``16GB``, ``16GiB``, ``4096M``, ``1.5G``, ``512K``, ``2T``.

        Suffixes are case-insensitive binary units (``G`` = 2**30, as SGE
        reads it); ``B``/``iB`` after the unit changes nothing. A bare
        integer is bytes. A fraction is rounded up to the byte.

        Raises:
            ValueError: *text* is not one of those forms, or is zero.
        """
        match = _MEMORY.fullmatch(text.strip())
        if match is None:
            raise ValueError(
                f"invalid memory {text!r}; use a number with a unit, e.g. 16G, 4096M or 1.5GiB"
            )
        number, suffix = match.groups()
        if suffix is None:
            if "." in number:
                raise ValueError(f"invalid memory {text!r}; a value without a unit is whole bytes")
            value = int(number)
        else:
            unit = _UNIT_BYTES[suffix[0].upper()]
            value = math.ceil(float(number) * unit) if "." in number else int(number) * unit
        if value <= 0:
            raise ValueError(f"invalid memory {text!r}; must be greater than zero")
        return cls(value)

    def _exact(self, units: tuple[tuple[str, int], ...]) -> tuple[int, str] | None:
        """The largest unit that divides the value exactly, as ``(count, unit)``."""
        for unit, size in units:
            if self.bytes_ % size == 0:
                return self.bytes_ // size, unit
        return None

    def to_sge(self) -> str:
        """``16G``: the largest exact binary unit, else rounded up in M."""
        exact = self._exact(_UNITS)
        if exact is not None:
            return f"{exact[0]}{exact[1]}"
        return f"{_ceil_div(self.bytes_, _UNIT_BYTES['M'])}M"

    def to_slurm_mb(self) -> int:
        """Whole megabytes (MiB), rounded up: Slurm's ``--mem`` unit."""
        return _ceil_div(self.bytes_, _UNIT_BYTES["M"])

    def to_pbs(self) -> str:
        """``16gb``: as :meth:`to_sge`, in PBS's lower-case ``kb``/``mb``/``gb``/``tb``."""
        return f"{self.to_sge().lower()}b"

    def __truediv__(self, slots: int) -> Memory:
        """The share of one of *slots* slots, rounded up to the byte."""
        return Memory(_ceil_div(self.bytes_, slots))

    def __str__(self) -> str:
        """``16 GiB (17179869184 bytes)``."""
        exact = self._exact(_UNITS)
        if exact is None:
            return f"{self.bytes_} bytes"
        return f"{exact[0]} {exact[1]}iB ({self.bytes_} bytes)"


_COLON = re.compile(r"(?:(\d+)-)?(\d+(?::\d+){0,2})")
_UNIT_DURATION = re.compile(r"(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?")
_DAY = 86400


@dataclass(frozen=True)
class Duration:
    """A time limit, in seconds."""

    seconds: int

    @classmethod
    def parse(cls, text: str) -> Duration:
        """Parse a duration.

        Accepted: ``HH:MM:SS``, ``MM:SS``, ``D-HH:MM:SS``, ``D-HH:MM``,
        ``D-HH``, and units ``90s``, ``30m``, ``4h``, ``2d`` or a compound
        of them in that order (``1h30m``, ``1d2h``). The leading field of a
        colon form is unbounded (``100:00:00``); the others are 0-59.

        A bare integer is an error: SGE reads it as seconds, Slurm as
        minutes.

        Raises:
            ValueError: *text* is not one of those forms, or is zero.
        """
        stripped = text.strip()
        if stripped.isdigit():
            raise ValueError(f"invalid time {text!r}: ambiguous: use 90s or 90m")
        seconds = _parse_colon(stripped) if ":" in stripped or "-" in stripped else None
        if seconds is None:
            match = _UNIT_DURATION.fullmatch(stripped)
            if match is None or not any(match.groups()):
                raise ValueError(
                    f"invalid time {text!r}; use HH:MM:SS, D-HH:MM:SS or units "
                    "such as 90s, 30m, 4h, 2d, 1h30m"
                )
            days, hours, minutes, secs = (int(g) if g else 0 for g in match.groups())
            seconds = days * _DAY + hours * 3600 + minutes * 60 + secs
        if seconds <= 0:
            raise ValueError(f"invalid time {text!r}; must be greater than zero")
        return cls(seconds)

    def _split(self) -> tuple[int, int, int, int]:
        """``(days, hours, minutes, seconds)``."""
        days, rest = divmod(self.seconds, _DAY)
        hours, rest = divmod(rest, 3600)
        minutes, secs = divmod(rest, 60)
        return days, hours, minutes, secs

    def to_sge(self) -> str:
        """``H:MM:SS``, hours unbounded (``36:00:00``)."""
        hours, rest = divmod(self.seconds, 3600)
        minutes, secs = divmod(rest, 60)
        return f"{hours}:{minutes:02d}:{secs:02d}"

    def to_slurm(self) -> str:
        """``D-HH:MM:SS``."""
        days, hours, minutes, secs = self._split()
        return f"{days}-{hours:02d}:{minutes:02d}:{secs:02d}"

    def to_pbs(self) -> str:
        """``HH:MM:SS``, hours unbounded."""
        hours, rest = divmod(self.seconds, 3600)
        minutes, secs = divmod(rest, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def __str__(self) -> str:
        """``4:00:00 (14400 s)``."""
        return f"{self.to_sge()} ({self.seconds} s)"


def _parse_colon(text: str) -> int | None:
    """Seconds for a colon / day-dash form, or None if *text* is not one.

    Raises:
        ValueError: A field after the first is 60 or more.
    """
    match = _COLON.fullmatch(text)
    if match is None:
        return None
    day_text, clock = match.groups()
    fields = [int(f) for f in clock.split(":")]
    if day_text is not None:
        # D-HH, D-HH:MM, D-HH:MM:SS: the clock starts at hours.
        fields += [0] * (3 - len(fields))
        if fields[0] >= 24:
            raise ValueError(f"invalid time {text!r}; hours after 'D-' must be 0-23")
    elif len(fields) == 2:
        fields = [0, *fields]  # MM:SS
    elif len(fields) == 1:
        return None  # a bare integer, rejected by the caller
    hours, minutes, secs = fields
    if (day_text is not None or len(clock.split(":")) == 3) and minutes >= 60:
        raise ValueError(f"invalid time {text!r}; minutes must be 0-59")
    if len(clock.split(":")) > 1 and secs >= 60:
        raise ValueError(f"invalid time {text!r}; seconds must be 0-59")
    days = int(day_text) if day_text is not None else 0
    return days * _DAY + hours * 3600 + minutes * 60 + secs
