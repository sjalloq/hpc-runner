"""Configuration loading and management."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


@dataclass
class HPCConfig:
    """Loaded configuration."""

    defaults: dict[str, Any] = field(default_factory=dict)
    tools: dict[str, dict[str, Any]] = field(default_factory=dict)
    types: dict[str, dict[str, Any]] = field(default_factory=dict)
    schedulers: dict[str, dict[str, Any]] = field(default_factory=dict)

    _source_path: Path | None = field(default=None, repr=False)

    def get_job_config(self, tool_or_type: str) -> dict[str, Any]:
        """Get merged configuration for a tool or type.

        Lookup order:
        1. Check types[tool_or_type]
        2. Check tools[tool_or_type]
        3. Fall back to defaults
        """
        config = self.defaults.copy()

        if tool_or_type in self.types:
            config = _merge(config, self.types[tool_or_type])
        elif tool_or_type in self.tools:
            config = _merge(config, self.tools[tool_or_type])

        return config

    def get_tool_config(self, command: str) -> dict[str, Any]:
        """Get configuration matching a command.

        Extracts tool name from command and looks up config.
        """
        # Extract tool name (first word, strip path)
        tool = command.split()[0]
        tool = Path(tool).name

        return self.get_job_config(tool)

    def get_scheduler_config(self, scheduler: str) -> dict[str, Any]:
        """Get scheduler-specific configuration."""
        return self.schedulers.get(scheduler, {})


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep merge with override taking precedence."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge(result[key], value)
        elif key in result and isinstance(result[key], list) and isinstance(value, list):
            # Check for list reset marker
            if value and value[0] == "-":
                result[key] = value[1:]
            else:
                result[key] = list(set(result[key] + value))
        else:
            result[key] = value
    return result


def find_config_file() -> Path | None:
    """Find configuration file in priority order.

    Search order:
    1. ./hpc-tools.toml (current directory)
    2. ./pyproject.toml [tool.hpc-tools] section
    3. Git repository root hpc-tools.toml
    4. ~/.config/hpc-tools/config.toml
    5. Package defaults
    """
    # Current directory
    cwd = Path.cwd()
    if (cwd / "hpc-tools.toml").exists():
        return cwd / "hpc-tools.toml"

    if (cwd / "pyproject.toml").exists():
        try:
            with open(cwd / "pyproject.toml", "rb") as f:
                pyproject = tomllib.load(f)
            if "tool" in pyproject and "hpc-tools" in pyproject["tool"]:
                return cwd / "pyproject.toml"
        except Exception:
            pass

    # Git root
    git_root = _find_git_root(cwd)
    if git_root and (git_root / "hpc-tools.toml").exists():
        return git_root / "hpc-tools.toml"

    # User config
    user_config = Path.home() / ".config" / "hpc-tools" / "config.toml"
    if user_config.exists():
        return user_config

    # Package defaults
    package_defaults = Path(__file__).parent.parent.parent.parent / "defaults" / "config.toml"
    if package_defaults.exists():
        return package_defaults

    return None


def _find_git_root(start: Path) -> Path | None:
    """Find git repository root."""
    current = start.resolve()
    while current != current.parent:
        if (current / ".git").exists():
            return current
        current = current.parent
    return None


def load_config(path: Path | str | None = None) -> HPCConfig:
    """Load configuration from file.

    Args:
        path: Explicit config path or None to auto-discover
    """
    if path is None:
        path = find_config_file()

    if path is None:
        return HPCConfig()  # Empty config, use defaults

    path = Path(path)

    with open(path, "rb") as f:
        data = tomllib.load(f)

    # Handle pyproject.toml
    if path.name == "pyproject.toml":
        data = data.get("tool", {}).get("hpc-tools", {})

    config = HPCConfig(
        defaults=data.get("defaults", {}),
        tools=data.get("tools", {}),
        types=data.get("types", {}),
        schedulers=data.get("schedulers", {}),
    )
    config._source_path = path

    return config


# Global config cache
_cached_config: HPCConfig | None = None


def get_config() -> HPCConfig:
    """Get the global configuration (cached)."""
    global _cached_config
    if _cached_config is None:
        _cached_config = load_config()
    return _cached_config


def reload_config(path: Path | str | None = None) -> HPCConfig:
    """Reload configuration (clears cache)."""
    global _cached_config
    _cached_config = load_config(path)
    return _cached_config
