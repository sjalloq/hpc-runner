"""Fixtures of the unit suite only."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest

from tests.fakes import FakeClock


@pytest.fixture(autouse=True)
def slurm_clock() -> Iterator[FakeClock]:
    """The clock of the Slurm backend: no unit test waits for ``sacct`` or ``scontrol``.

    The backend asks a command again after a sleep; here the sleep returns
    at once and is recorded (``slurm_clock.sleeps``).
    """
    clock = FakeClock()
    with patch("hpc_runner.schedulers.slurm.scheduler.time", clock):
        yield clock
