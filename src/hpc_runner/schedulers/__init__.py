"""Scheduler registry and auto-detection."""

from __future__ import annotations

import importlib
import importlib.util
from collections.abc import Mapping
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


def scheduler_config_keys(name: str) -> Mapping[str, type] | None:
    """The ``[schedulers.<name>]`` keys backend *name* declares, key -> type.

    Imports only that backend, on demand. Returns None when the registry
    does not know *name* or its module is not part of this installation.
    """
    if name not in _SCHEDULERS:
        return None
    module_path, class_name = _SCHEDULERS[name].rsplit(":", 1)
    try:
        if importlib.util.find_spec(module_path) is None:
            return None
    except ModuleNotFoundError:  # a parent package is missing
        return None
    scheduler_class = getattr(importlib.import_module(module_path), class_name)
    keys: Mapping[str, type] = scheduler_class.CONFIG_KEYS
    return keys


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


__all__ = [
    "get_scheduler",
    "register_scheduler",
    "list_schedulers",
    "detect_scheduler",
    "scheduler_config_keys",
]
