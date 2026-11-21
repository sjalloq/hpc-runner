"""Configuration loading and management."""

from __future__ import annotations

import logging
import os
import shlex
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[import-not-found]

from hpc_runner.core.exceptions import ConfigError
from hpc_runner.core.expand import bind_config_dir, expand
from hpc_runner.core.schema import EXPANDED_FIELDS, find_line, validate_file

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

    Each job layer holds its keys as written; :func:`~hpc_runner.resolve`
    combines the layers in :meth:`layers_for` order.
    """

    #: The top-level ``scheduler`` key: the one scheduler this installation
    #: submits to. ``HPC_SCHEDULER`` overrides it (``get_scheduler``).
    scheduler: str | None = None
    #: The top-level ``module_init_script`` key: the script every job sources
    #: to define ``module``; empty means the standard locations are tried.
    module_init_script: str = ""
    defaults: dict[str, Any] = field(default_factory=dict)
    tools: dict[str, dict[str, Any]] = field(default_factory=dict)
    types: dict[str, dict[str, Any]] = field(default_factory=dict)
    schedulers: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: The ``[trace]`` section (``enabled``, ``dir``);
    #: read by ``get_scheduler`` to start recording scheduler calls.
    trace: dict[str, Any] = field(default_factory=dict)

    #: Non-fatal problems found while loading (e.g. a section for a
    #: scheduler this package does not have).
    warnings: list[str] = field(default_factory=list)

    _source_paths: list[Path] = field(default_factory=list, repr=False)

    def layers_for(self, spec: JobSpec) -> list[tuple[str, dict[str, Any]]]:
        """The ordered config layers for *spec*, least specific first.

        Each entry is ``(layer name, table)``. The chain is fixed (D2)::

            no spec.type:  defaults -> tools.<tool> -> tools.<tool>.options.<first match>
            spec.type=T:   defaults -> tools.<each of types.T.tools> -> types.T

        ``<tool>`` is the entry :meth:`tool_for` finds for the command's
        first word. With a type named, the tool inferred from the command is
        not consulted and referenced tools' ``options`` are not applied.

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
                layers.append((f"tools.{tool}", _without(self.tools[tool], "options", "commands")))
            layers.append((f"types.{spec.type}", _without(entry, "tools")))
            return layers

        command = spec.command if isinstance(spec.command, str) else shlex.join(spec.command)
        parts = command.split()
        tool = self.tool_for(Path(parts[0]).name) if parts else None
        if tool is None:
            return layers
        tool_section = self.tools[tool]
        layers.append((f"tools.{tool}", _without(tool_section, "options", "commands")))

        # Option specialisation: first matching key wins.
        options = tool_section.get("options")
        if options and len(parts) > 1:
            cmd_tokens = _normalise_tokens(parts[1:])
            for option_key, option_config in options.items():
                if _match_contiguous(cmd_tokens, _normalise_tokens(option_key)):
                    layers.append((f"tools.{tool}.options.{option_key}", dict(option_config)))
                    break

        return layers

    def tool_for(self, command: str) -> str | None:
        """The ``[tools.<name>]`` entry that matches *command* (a bare name), or None.

        An entry with a ``commands`` list matches exactly those names (and
        its own only if listed); one without matches its own name. Files
        are checked at load so that no name belongs to two entries; a config
        built directly takes the first entry, in definition order.
        """
        for name, entry in self.tools.items():
            if command in entry.get("commands", [name]):
                return name
        return None

    def get_scheduler_config(self, scheduler: str) -> dict[str, Any]:
        """Get scheduler-specific configuration."""
        return self.schedulers.get(scheduler, {})


def _without(table: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {k: v for k, v in table.items() if k not in keys}


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


def _combine_named(
    project: dict[str, dict[str, Any]],
    local: dict[str, dict[str, Any]],
    combine: Callable[[Mapping[str, Any], Mapping[str, Any]], dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """``[tools.*]`` or ``[types.*]`` of the project file with the local file's on top.

    An entry in both files becomes one layer, combined per key by *combine*
    (:func:`~hpc_runner.core.resolve.combine_layers`), local after project;
    so does each ``options`` sub-entry of a tool that both files hold.
    """
    result = dict(project)
    for name, entry in local.items():
        if name not in result:
            result[name] = entry
            continue
        merged = combine(_without(result[name], "options"), _without(entry, "options"))
        options = dict(result[name].get("options", {}))
        for pattern, option in entry.get("options", {}).items():
            options[pattern] = combine(options[pattern], option) if pattern in options else option
        if options:
            merged["options"] = options
        result[name] = merged
    return result


def _bind_config_dir(converted: dict[str, Any], config_dir: str) -> None:
    """Bind ``${CONFIG_DIR}`` in one file's job layers to *config_dir*, in place.

    Done per file, before merging, because the merged config no longer
    knows which file set a value (D5).
    """

    def bind(value: Any) -> Any:
        if isinstance(value, str):
            return bind_config_dir(value, config_dir)
        if isinstance(value, list):
            return [bind(v) for v in value]
        if isinstance(value, dict):
            return {k: bind(v) for k, v in value.items()}
        return value

    def bind_layer(layer: dict[str, Any]) -> None:
        for key in EXPANDED_FIELDS & layer.keys():
            layer[key] = bind(layer[key])

    if "defaults" in converted:
        bind_layer(converted["defaults"])
    for tool in converted.get("tools", {}).values():
        bind_layer(tool)
        for option in tool.get("options", {}).values():
            bind_layer(option)
    for job_type in converted.get("types", {}).values():
        bind_layer(job_type)


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


def _discover() -> tuple[Path | None, Path | None]:
    """The ``(project, local)`` config files of the two-level discovery.

    1. **Project file:** ``$HPC_RUNNER_CONFIG`` if set and the file exists,
       otherwise ``<git-root>/hpc-runner.toml``. These are mutually
       exclusive: the env var exists for submodule scenarios where a parent
       project's config should win over the submodule's git root.
    2. **Local file:** ``./hpc-runner.toml`` in the current working
       directory, when it is a different file from the project file.

    With no project file, ``./hpc-runner.toml`` is returned as the project
    file: it is the only config and is read as a whole one.
    """
    project: Path | None = None
    env_config = os.environ.get(HPC_CONFIG_ENV_VAR)
    if env_config:
        env_path = Path(
            expand(env_config, host_env=os.environ, extra={}, where=f"${HPC_CONFIG_ENV_VAR}")
        )
        if env_path.exists():
            project = env_path.resolve()
    else:
        git_root = _find_git_root(Path.cwd())
        if git_root and (git_root / "hpc-runner.toml").exists():
            project = (git_root / "hpc-runner.toml").resolve()

    cwd_config = Path.cwd() / "hpc-runner.toml"
    local = cwd_config.resolve() if cwd_config.exists() else None
    if project is None:
        return local, None
    return project, (local if local != project else None)


def find_config_files() -> list[Path]:
    """The configuration files read, in order: the project file, then the local file.

    See :func:`_discover` for where each is looked for.
    """
    return [path for path in _discover() if path is not None]


def find_config_file() -> Path | None:
    """Find the highest priority configuration file.

    Returns the last file of the two-level discovery (the project file at
    the git root or ``$HPC_RUNNER_CONFIG``, then the local
    ``./hpc-runner.toml``).
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
        ConfigError: A file cannot be read or parsed; nothing further can
            be checked.
    """
    # Imported here: resolve, which owns the rules for combining layers,
    # imports this module.
    from hpc_runner.core.resolve import combine_layers

    project: Path | None
    if path is not None:
        project, local = Path(path).resolve(), None
    else:
        project, local = _discover()
    config_files = [p for p in (project, local) if p is not None]

    scheduler: str | None = None
    module_init_script = ""
    merged: dict[str, Any] = {
        "defaults": {},
        "tools": {},
        "types": {},
        "schedulers": {},
        "trace": {},
    }
    errors: list[ConfigError] = []
    warnings: list[str] = []
    texts: dict[Path, str] = {}
    # Which file last set [types.T] tools / [tools.X] commands, for locating
    # structural errors.
    type_tools_origin: dict[str, Path] = {}
    tool_commands_origin: dict[str, Path] = {}

    for config_path in config_files:
        text, data = _read_toml(config_path)
        texts[config_path] = text
        # The local file holds [tools.*] and [types.*] only; validation
        # refuses anything else there, so the rest is the project file's.
        converted, file_errors, file_warnings = validate_file(
            config_path, text, data, _scheduler_config_keys, local=config_path is local
        )
        errors.extend(file_errors)
        warnings.extend(file_warnings)
        _bind_config_dir(converted, str(config_path.parent))
        for name, entry in converted.get("types", {}).items():
            if "tools" in entry:
                type_tools_origin[name] = config_path
        for name, entry in converted.get("tools", {}).items():
            if "commands" in entry:
                tool_commands_origin[name] = config_path
        for key in ("tools", "types"):
            merged[key] = _combine_named(merged[key], converted.get(key, {}), combine_layers)
        for key in ("defaults", "schedulers", "trace"):
            merged[key] = converted.get(key, merged[key])
        scheduler = converted.get("scheduler", scheduler)
        module_init_script = converted.get("module_init_script", module_init_script)

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

    # Structural: a command name belongs to one tool entry. An entry without
    # `commands` owns its own name. The error is reported at the `commands`
    # line that caused the clash.
    owner: dict[str, str] = {}
    for name, entry in merged["tools"].items():
        for command in entry.get("commands", [name]):
            other = owner.setdefault(command, name)
            if other == name:
                continue
            listed = name if "commands" in entry else other
            rival = other if listed == name else name
            how = (
                f"is also listed by [tools.{rival}]"
                if "commands" in merged["tools"][rival]
                else f"is also [tools.{rival}]"
            )
            origin = tool_commands_origin[listed]
            errors.append(
                ConfigError(
                    f"[tools.{listed}] commands: '{command}' {how}; "
                    "a command name belongs to one tool entry",
                    path=origin,
                    line=find_line(texts[origin], ["tools", listed, "commands"]),
                )
            )

    config = HPCConfig(
        scheduler=scheduler,
        module_init_script=module_init_script,
        defaults=merged["defaults"],
        tools=merged["tools"],
        types=merged["types"],
        schedulers=merged["schedulers"],
        trace=merged["trace"],
        warnings=warnings,
    )
    config._source_paths = config_files
    return config, errors


def load_config(path: Path | str | None = None) -> HPCConfig:
    """Load, validate and merge configuration from all discovered files.

    Args:
        path: Explicit config path, or None to discover the project file
            and the local file.

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
