"""Scheduler registry and auto-detection."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from hpc_runner.core.config import load_config
from hpc_runner.core.exceptions import ConfigError
from hpc_runner.schedulers.detection import detect_scheduler

if TYPE_CHECKING:
    from hpc_runner.core.config import HPCConfig
    from hpc_runner.schedulers.base import BaseScheduler

_SCHEDULERS: dict[str, str] = {
    "sge": "hpc_runner.schedulers.sge:SGEScheduler",
    "slurm": "hpc_runner.schedulers.slurm:SlurmScheduler",
    "pbs": "hpc_runner.schedulers.pbs:PBSScheduler",
    "local": "hpc_runner.schedulers.local:LocalScheduler",
}


def get_scheduler(name: str | None = None, *, config: HPCConfig | None = None) -> BaseScheduler:
    """Get scheduler instance.

    Args:
        name: Scheduler name or None to auto-detect
        config: Configuration the scheduler reads its ``[schedulers.<name>]``
            settings from. None loads it afresh with ``load_config()``.

    Returns:
        Scheduler instance

    Raises:
        ConfigError: *name* is not a registered scheduler.
    """
    if name is None:
        name = detect_scheduler()

    if name not in _SCHEDULERS:
        available = ", ".join(_SCHEDULERS)
        raise ConfigError(f"unknown scheduler '{name}'; available: {available}")

    # Lazy import
    module_path, class_name = _SCHEDULERS[name].rsplit(":", 1)
    module = importlib.import_module(module_path)
    scheduler_class = getattr(module, class_name)

    if config is None:
        config = load_config()
    return scheduler_class(config)  # type: ignore[no-any-return]


def register_scheduler(name: str, import_path: str) -> None:
    """Register a custom scheduler.

    Args:
        name: Scheduler name
        import_path: Import path like "mypackage.scheduler:MyScheduler"
    """
    _SCHEDULERS[name] = import_path


def list_schedulers() -> list[str]:
    """List available scheduler names."""
    return list(_SCHEDULERS.keys())


__all__ = ["get_scheduler", "register_scheduler", "list_schedulers", "detect_scheduler"]
