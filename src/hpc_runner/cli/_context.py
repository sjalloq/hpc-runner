"""Shared CLI context object, kept apart from ``main`` to avoid import cycles."""

from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import rich_click as click


class Context:
    """State passed from the ``hpc`` group to its subcommands."""

    def __init__(self) -> None:
        self.config_path: Path | None = None
        self.scheduler: str | None = None
        self.verbose: bool = False


F = TypeVar("F", bound=Callable[..., object])
pass_context: Callable[[F], F] = click.make_pass_decorator(Context, ensure=True)  # type: ignore[assignment]
