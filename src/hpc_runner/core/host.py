"""Submit-side facts that job resolution depends on."""

from __future__ import annotations

import getpass
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


def current_user(env: Mapping[str, str] | None = None) -> str:
    """The user this process runs as: ``$USER`` if set, else the account name.

    ``docker exec``, cron and some ``sudo`` configurations leave ``USER``
    unset, so anything that filters a scheduler's listing by user must not
    read the variable alone (it once fell back to the string ``"unknown"``,
    which matched nobody's jobs).
    """
    source = os.environ if env is None else env
    return source.get("USER") or getpass.getuser()


@dataclass(frozen=True)
class Host:
    """The submitting host: its environment, working directory and user.

    ``resolve()`` reads these instead of ``os.environ`` / ``Path.cwd()`` so a
    test (or a caller resolving on behalf of someone else) can inject them.
    """

    env: Mapping[str, str]
    cwd: Path
    user: str

    @property
    def virtual_env(self) -> str | None:
        """The active virtualenv (``$VIRTUAL_ENV``), or None."""
        return self.env.get("VIRTUAL_ENV") or None

    @classmethod
    def capture(cls) -> Host:
        """Snapshot the current process: ``os.environ``, cwd and user."""
        env = dict(os.environ)
        user = current_user(env)
        return cls(env=env, cwd=Path.cwd(), user=user)
