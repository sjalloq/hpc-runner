"""Configuration loading and management."""

from __future__ import annotations

import logging
import os
import re
import shlex
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[import-not-found]

from hpc_runner.core.exceptions import ConfigError
from hpc_runner.core.merge import compose, is_list_value, key_for
from hpc_runner.core.schema import find_line, validate_file

if TYPE_CHECKING:
    from hpc_runner.core.spec import JobSpec

logger = logging.getLogger("hpc_runner.config")

# Environment variable for site/system config
HPC_CONFIG_ENV_VAR = "HPC_RUNNER_CONFIG"


def _config_error(path: Path, message: str) -> ConfigError:
    return ConfigError(message, path=path)


@dataclass
class HPCConfig:
    """Loaded, validated configuration.

    List settings hold their layer value as written: a plain list (append),
    :class:`~hpc_runner.core.merge.Set` or :class:`~hpc_runner.core.merge.Edit`;
    :func:`~hpc_runner.resolve` applies them in :meth:`layers_for` order.
    """

    defaults: dict[str, Any] = field(default_factory=dict)
    tools: dict[str, dict[str, Any]] = field(default_factory=dict)
    types: dict[str, dict[str, Any]] = field(default_factory=dict)
    schedulers: dict[str, dict[str, Any]] = field(default_factory=dict)

    #: Non-fatal problems found while loading (e.g. a section for a
    #: scheduler this package does not have).
    warnings: list[str] = field(default_factory=list)

    _source_paths: list[Path] = field(default_factory=list, repr=False)

    def layers_for(self, spec: JobSpec) -> list[tuple[str, dict[str, Any]]]:
        """The ordered config layers for *spec*, least specific first.

        Each entry is ``(layer name, table)``. The chain is fixed (D2)::

            no spec.type:  defaults -> tools.<argv0> -> tools.<argv0>.options.<first match>
            spec.type=T:   defaults -> tools.<each of types.T.tools> -> types.T

        With a type named, the tool inferred from the command is not
        consulted and referenced tools' ``options`` are not applied.

        Raises:
            ConfigError: *spec* names a type that is not defined, or the
                type lists a tool that is not defined.
        """
        layers: list[tuple[str, dict[str, Any]]] = [("defaults", dict(self.defaults))]

        if spec.type is not None:
            entry = self.types.get(spec.type)
            if entry is None:
                known = ", ".join(sorted(self.types)) or "none defined"
                raise ConfigError(f"unknown job type '{spec.type}' (types: {known})")
            for tool in entry.get("tools", []):
                if tool not in self.tools:
                    raise ConfigError(
                        f"[types.{spec.type}] tools: no tool '{tool}' is defined under [tools]"
                    )
                layers.append((f"tools.{tool}", _without(self.tools[tool], "options")))
            layers.append((f"types.{spec.type}", _without(entry, "tools")))
            return layers

        command = spec.command if isinstance(spec.command, str) else shlex.join(spec.command)
        parts = command.split()
        tool = Path(parts[0]).name if parts else ""
        tool_section = self.tools.get(tool)
        if tool_section is None:
            return layers
        layers.append((f"tools.{tool}", _without(tool_section, "options")))

        # Option specialisation: first matching key wins.
        options = tool_section.get("options")
        if options and len(parts) > 1:
            cmd_tokens = _normalise_tokens(parts[1:])
            for option_key, option_config in options.items():
                if _match_contiguous(cmd_tokens, _normalise_tokens(option_key)):
                    layers.append((f"tools.{tool}.options.{option_key}", dict(option_config)))
                    break

        return layers

    def get_scheduler_config(self, scheduler: str) -> dict[str, Any]:
        """Get scheduler-specific configuration."""
        return self.schedulers.get(scheduler, {})


def _without(table: dict[str, Any], key: str) -> dict[str, Any]:
    return {k: v for k, v in table.items() if k != key}


def _normalise_tokens(args: str | list[str]) -> list[str]:
    """Tokenise and normalise command arguments.

    Splits --flag=value into separate tokens. Does not attempt to
    interpret short options or combined flags.
    """
    if isinstance(args, str):
        tokens = args.split()
    else:
        tokens = list(args)

    normalised: list[str] = []
    for token in tokens:
        if token.startswith("--") and "=" in token:
            flag, _, value = token.partition("=")
            normalised.append(flag)
            normalised.append(value)
        else:
            normalised.append(token)
    return normalised


def _match_contiguous(haystack: list[str], needle: list[str]) -> bool:
    """Check if needle tokens appear as a contiguous sequence in haystack."""
    if not needle:
        return False
    needle_len = len(needle)
    for i in range(len(haystack) - needle_len + 1):
        if haystack[i : i + needle_len] == needle:
            return True
    return False


def _merge(base: dict[str, Any], override: dict[str, Any], parent: str = "") -> dict[str, Any]:
    """Merge two config files' tables: dicts recurse, lists compose per D1.

    ``resources`` lists dedupe by name; the per-scheduler lists under
    ``scheduler_args`` (*parent*) are argv and do not dedupe. Scalars:
    later wins.
    """
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge(result[key], value, key)
        elif key in result and is_list_value(result[key]) and is_list_value(value):
            result[key] = compose(
                result[key],
                value,
                key=key_for(key),
                dedupe=parent != "scheduler_args",
            )
        else:
            result[key] = value
    return result


def _expand_env_vars(path_str: str, env: Mapping[str, str] | None = None) -> str:
    """Expand environment variables in a path string.

    Supports ${VAR} and $VAR syntax. Variables are looked up in *env*
    (default ``os.environ``); undefined ones are left as written.
    """
    lookup: Mapping[str, str] = os.environ if env is None else env

    # Handle ${VAR} syntax
    def replace_braced(match: re.Match[str]) -> str:
        var_name = match.group(1)
        return lookup.get(var_name, match.group(0))

    result = re.sub(r"\$\{([^}]+)\}", replace_braced, path_str)

    # Handle $VAR syntax (word characters only)
    def replace_simple(match: re.Match[str]) -> str:
        var_name = match.group(1)
        return lookup.get(var_name, match.group(0))

    result = re.sub(r"\$([A-Za-z_][A-Za-z0-9_]*)", replace_simple, result)

    return result


def _read_toml(path: Path) -> tuple[str, dict[str, Any]]:
    """Read and parse *path*; returns ``(text, data)``.

    Raises:
        ConfigError: The file cannot be read or is not valid TOML.
    """
    try:
        text = path.read_text()
    except OSError as e:
        raise _config_error(path, f"could not read file — {e}") from e
    except UnicodeDecodeError as e:
        raise _config_error(path, f"invalid TOML — {e}") from e
    try:
        return text, tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise _config_error(path, f"invalid TOML — {e}") from e


def _resolve_extends(config_path: Path, chain: list[Path] | None = None) -> list[Path]:
    """Resolve the ``extends`` chain of *config_path*.

    Returns the paths in merge order (base first).

    Raises:
        ConfigError: A file cannot be read or parsed, ``extends`` names a
            file that does not exist, or the chain is a cycle.
    """
    chain = [] if chain is None else chain
    config_path = config_path.resolve()

    if config_path in chain:
        cycle = " -> ".join(str(p) for p in [*chain[chain.index(config_path) :], config_path])
        raise _config_error(config_path, f"extends cycle: {cycle}")
    chain.append(config_path)

    text, data = _read_toml(config_path)
    extends = data.get("extends")
    if not isinstance(extends, str) or not extends:
        # A non-string extends is reported by validation.
        return [config_path]

    # Expand env vars and resolve relative to config file's directory
    extends_path = Path(_expand_env_vars(extends))
    if not extends_path.is_absolute():
        extends_path = config_path.parent / extends_path
    extends_path = extends_path.resolve()

    if not extends_path.is_file():
        raise ConfigError(
            f"extends: file not found: {extends_path}",
            path=config_path,
            line=find_line(text, ["extends"]),
        )

    return _resolve_extends(extends_path, chain) + [config_path]


def find_config_files() -> list[Path]:
    """Find all configuration files to merge.

    Returns list of paths in merge order (first = lowest priority).

    Two-level discovery model:

    1. **Project config (level 1):** ``$HPC_RUNNER_CONFIG`` if set and the file
       exists, otherwise ``<git-root>/hpc-runner.toml``.  These are mutually
       exclusive — the env var exists for submodule scenarios where a parent
       project's config should win over the submodule's git root.
    2. **Local override (level 2):** ``./hpc-runner.toml`` in the current
       working directory, when it resolves to a different file from level 1.
       Allows subdirectories to override project defaults.

    Both levels go through ``add_config`` → ``_resolve_extends`` so extends
    chains are preserved.
    """
    configs: list[Path] = []
    seen: set[Path] = set()

    def add_config(path: Path) -> None:
        """Add config and its extends chain, avoiding duplicates."""
        resolved = path.resolve()
        if resolved in seen:
            return

        # Resolve extends chain
        chain = _resolve_extends(path)
        for p in chain:
            if p not in seen:
                seen.add(p)
                configs.append(p)

    # Level 1: Project config
    # If $HPC_RUNNER_CONFIG is set and exists, use it.
    # Otherwise fall back to <git-root>/hpc-runner.toml.
    env_config = os.environ.get(HPC_CONFIG_ENV_VAR)
    if env_config:
        env_path = Path(_expand_env_vars(env_config))
        if env_path.exists():
            add_config(env_path)
    else:
        cwd = Path.cwd()
        git_root = _find_git_root(cwd)
        if git_root:
            git_config = git_root / "hpc-runner.toml"
            if git_config.exists():
                add_config(git_config)

    # Level 2: Local override
    # ./hpc-runner.toml in cwd, if it's a different file from level 1.
    cwd = Path.cwd()
    if (cwd / "hpc-runner.toml").exists():
        add_config(cwd / "hpc-runner.toml")

    return configs


def find_config_file() -> Path | None:
    """Find the highest priority configuration file.

    Returns the last (highest priority) config from the two-level discovery
    (project config at git root or ``$HPC_RUNNER_CONFIG``, then local
    ``./hpc-runner.toml`` override).
    """
    configs = find_config_files()
    return configs[-1] if configs else None


def _find_git_root(start: Path) -> Path | None:
    """Find git repository root."""
    current = start.resolve()
    while current != current.parent:
        if (current / ".git").exists():
            return current
        current = current.parent
    return None


def _scheduler_config_keys(name: str) -> Mapping[str, type] | None:
    # Imported here: the scheduler registry imports this module.
    from hpc_runner.schedulers import scheduler_config_keys

    return scheduler_config_keys(name)


def check_config(path: Path | str | None = None) -> tuple[HPCConfig, list[ConfigError]]:
    """Load configuration and return every problem instead of raising on them.

    Args:
        path: Explicit config path, or None to auto-discover.

    Unlike :func:`load_config`, warnings are returned, not logged.

    Returns:
        ``(config, errors)``. ``config.warnings`` holds the warnings and
        ``config._source_paths`` the files read. *config* is only usable
        when *errors* is empty.

    Raises:
        ConfigError: A file cannot be read or parsed, or its ``extends``
            chain is broken; nothing further can be checked.
    """
    config_files = _resolve_extends(Path(path)) if path is not None else find_config_files()

    merged: dict[str, Any] = {"defaults": {}, "tools": {}, "types": {}, "schedulers": {}}
    errors: list[ConfigError] = []
    warnings: list[str] = []
    texts: dict[Path, str] = {}
    # Which file last set [types.T] tools, for locating structural errors.
    type_tools_origin: dict[str, Path] = {}

    for config_path in config_files:
        text, data = _read_toml(config_path)
        texts[config_path] = text
        converted, file_errors, file_warnings = validate_file(
            config_path, text, data, _scheduler_config_keys
        )
        errors.extend(file_errors)
        warnings.extend(file_warnings)
        for name, entry in converted.get("types", {}).items():
            if "tools" in entry:
                type_tools_origin[name] = config_path
        for key in merged:
            if key in converted:
                merged[key] = _merge(merged[key], converted[key])

    # Structural: a type may only reference tools that exist (D2).
    for name, entry in merged["types"].items():
        for tool in entry.get("tools", []):
            if tool not in merged["tools"]:
                origin = type_tools_origin[name]
                errors.append(
                    ConfigError(
                        f"[types.{name}] tools: no tool '{tool}' is defined under [tools]",
                        path=origin,
                        line=find_line(texts[origin], ["types", name, "tools"]),
                    )
                )

    config = HPCConfig(
        defaults=merged["defaults"],
        tools=merged["tools"],
        types=merged["types"],
        schedulers=merged["schedulers"],
        warnings=warnings,
    )
    config._source_paths = config_files
    return config, errors


def load_config(path: Path | str | None = None) -> HPCConfig:
    """Load, validate and merge configuration from all discovered files.

    Args:
        path: Explicit config path (with its ``extends`` chain), or None to
            auto-discover and merge all.

    Raises:
        ConfigError: Any file is unreadable, invalid TOML, fails the schema
            or a structural check. Every problem found is reported in one
            error; ``.errors`` lists them individually.
    """
    config, errors = check_config(path)
    if errors:
        raise ConfigError.collect(errors)
    for warning in config.warnings:
        logger.warning(warning)
    return config
