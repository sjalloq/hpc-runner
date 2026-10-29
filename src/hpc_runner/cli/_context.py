"""Shared CLI context object, kept apart from ``main`` to avoid import cycles."""

from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import rich_click as click

from hpc_runner.core.config import HPCConfig, load_config


class Context:
    """State passed from the ``hpc`` group to its subcommands."""

    def __init__(self) -> None:
        self.config_path: Path | None = None
        self.scheduler: str | None = None
        self.verbose: bool = False
        self._config: HPCConfig | None = None

    @property
    def config(self) -> HPCConfig:
        """The configuration, loaded and validated on first use.

        Lazy so ``hpc config check`` can report a broken config itself;
        every other command refuses one before touching the scheduler.

        Raises:
            ConfigError: The configuration is invalid.
        """
        if self._config is None:
            self._config = load_config(self.config_path)
        return self._config


F = TypeVar("F", bound=Callable[..., object])
pass_context: Callable[[F], F] = click.make_pass_decorator(Context, ensure=True)  # type: ignore[assignment]
