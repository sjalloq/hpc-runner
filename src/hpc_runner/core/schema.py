"""Config schema: which keys each config section accepts, and their types (D11).

The keys allowed in a job layer (``[defaults]``, ``[tools.x]``,
``[tools.x.options."..."]``, ``[types.x]``) are the :class:`JobSpec` fields
minus the per-invocation ones; :data:`FIELD_VALIDATORS` gives each its
expected TOML type. ``modules_path`` and ``modules_path_append`` are accepted
in ``[defaults]`` only. Three structural keys sit beside the job keys:
``options`` and ``commands`` under ``[tools.x]``, ``tools`` under
``[types.x]``.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from hpc_runner.core.exceptions import ConfigError
from hpc_runner.core.units import Duration, Memory

#: The config format this package reads.
CONFIG_VERSION = 1

#: JobSpec fields that only make sense per invocation, never in config.
PER_INVOCATION_FIELDS = frozenset({"command", "type", "dependencies", "array"})

#: Top-level keys of a config file.
TOP_LEVEL_KEYS = (
    "config_version",
    "scheduler",
    "module_init_script",
    "defaults",
    "tools",
    "types",
    "schedulers",
    "trace",
)

#: The top-level keys a local file (``./hpc-runner.toml`` read after a
#: project file) may hold.
LOCAL_FILE_KEYS = ("config_version", "tools", "types")

#: Keys that used to live under ``[schedulers.<name>]``, and what to do now.
RETIRED_SCHEDULER_KEYS = {
    "module_init_script": "moved to the top level of the file; set it outside any section",
    "module_system": "removed; Environment Modules is the module system, and "
    "MODULES_VERBOSITY=concise is always exported",
}


class _Invalid(Exception):
    """A value failed validation; *subkey* narrows the location (a table key)."""

    def __init__(self, message: str, subkey: str | None = None):
        super().__init__(message)
        self.message = message
        self.subkey = subkey


def _describe(value: Any) -> str:
    if isinstance(value, bool):
        return f"boolean {str(value).lower()}"
    if isinstance(value, int):
        return f"integer {value}"
    if isinstance(value, float):
        return f"float {value}"
    if isinstance(value, str):
        return f'string "{value}"'
    if isinstance(value, list):
        return "an array"
    if isinstance(value, dict):
        return "a table"
    return type(value).__name__


def _int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _Invalid(f"expected an integer, got {_describe(value)}")
    return value


def _str(value: Any) -> str:
    if not isinstance(value, str):
        raise _Invalid(f"expected a string, got {_describe(value)}")
    return value


def _bool(value: Any) -> bool:
    if not isinstance(value, bool):
        raise _Invalid(f"expected true or false, got {_describe(value)}")
    return value


def _parsed(parse: Callable[[str], Any]) -> Callable[[Any], str]:
    """A string that must parse as a unit value (D10), kept as the string.

    The string stays in the layer because ``resolve()`` expands it (D5)
    before parsing; one containing ``$`` can only be checked then.
    """

    def validate(value: Any) -> str:
        text = _str(value)
        if "$" not in text:
            try:
                parse(text)
            except ValueError as exc:
                raise _Invalid(str(exc)) from None
        return text

    return validate


def _output_path(empty: str) -> Callable[[Any], str]:
    """An output pattern (D9): a non-empty string."""

    def validate(value: Any) -> str:
        text = _str(value)
        if text == "":
            raise _Invalid(empty)
        return text

    return validate


def _venv(value: Any) -> str | bool:
    if isinstance(value, str) or value is False:
        return value
    raise _Invalid(f"expected a path or false, got {_describe(value)}")


#: An environment variable name, as the job script can set or unset it.
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _env_dict(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise _Invalid(f"expected a table of strings, got {_describe(value)}")
    for key, item in value.items():
        if not ENV_NAME.fullmatch(key):
            raise _Invalid("not a valid environment variable name", subkey=key)
        if not isinstance(item, str):
            raise _Invalid(f"expected a string, got {_describe(item)}", subkey=key)
    return dict(value)


def count_problem(value: Any) -> str | None:
    """Why *value* is not a count (a whole number, 1 or more); None if it is one."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return f"expected a whole number, 1 or more, got {_describe(value)}"
    return None


def _count_map(value: Any) -> dict[str, int]:
    """A table of ``name = count``: what ``licenses`` and ``devices`` hold."""
    if not isinstance(value, dict):
        raise _Invalid(f"expected a table of name = count, got {_describe(value)}")
    for key, item in value.items():
        if not isinstance(key, str) or key == "":
            raise _Invalid(f"expected a name, got {_describe(key)}")
        if problem := count_problem(item):
            raise _Invalid(problem, subkey=key)
    return dict(value)


def _list_of(item: Callable[[Any, int], Any], what: str) -> Callable[[Any], list[Any]]:
    """A validator for a list whose entries each pass *item*."""

    def validate(value: Any) -> list[Any]:
        if not isinstance(value, list):
            raise _Invalid(f"expected a list of {what}, got {_describe(value)}")
        return [item(entry, i) for i, entry in enumerate(value)]

    return validate


def _str_item(value: Any, index: int) -> str:
    if not isinstance(value, str):
        raise _Invalid(f"entry {index + 1}: expected a string, got {_describe(value)}")
    return value


def _env_name_item(value: Any, index: int) -> str:
    name = _str_item(value, index)
    if not ENV_NAME.fullmatch(name):
        raise _Invalid(f"entry {index + 1}: {name!r} is not a valid environment variable name")
    return name


_str_list = _list_of(_str_item, "strings")
_env_name_list = _list_of(_env_name_item, "environment variable names")


#: Every config-settable JobSpec field and its validator. A test asserts this
#: covers exactly ``fields(JobSpec) - PER_INVOCATION_FIELDS``.
FIELD_VALIDATORS: dict[str, Callable[[Any], Any]] = {
    "name": _str,
    "cpu": _int,
    "mem": _parsed(Memory.parse),
    "time": _parsed(Duration.parse),
    "queue": _str,
    "project": _str,
    "stdout": _output_path("empty; omit the key to use the default"),
    "stderr": _output_path("empty; omit the key to merge into stdout"),
    "inherit_env": _bool,
    "workdir": _str,
    "venv": _venv,
    "env_vars": _env_dict,
    "env_prepend": _env_dict,
    "env_append": _env_dict,
    "env_unset": _env_name_list,
    "env_passthrough": _env_name_list,
    "modules": _str_list,
    "modules_path": _str_list,
    "modules_path_append": _str_list,
    "licenses": _count_map,
    "devices": _count_map,
    "features": _str_list,
    "scheduler_args": _str_list,
    "interactive": _bool,
}

#: Job keys accepted in ``[defaults]`` only (and on a ``JobSpec``): where the
#: site's modules are read from is said once.
DEFAULTS_ONLY_FIELDS = frozenset({"modules_path", "modules_path_append"})

#: Settings whose strings are expanded per D5 (:mod:`hpc_runner.core.expand`).
#: ``command`` is deliberately absent: it is the user's shell.
EXPANDED_FIELDS = frozenset(
    {
        "name",
        "mem",
        "time",
        "queue",
        "project",
        "stdout",
        "stderr",
        "workdir",
        "venv",
        "env_vars",
        "env_prepend",
        "env_append",
        "modules",
        "modules_path",
        "modules_path_append",
        "scheduler_args",
    }
)


def env_conflicts(layer: Mapping[str, Any]) -> list[tuple[str, str]]:
    """``(field, key)`` for each key a layer both sets and prepends/appends (D5).

    Setting a variable and prepending or appending to it in the same layer
    is contradictory; across layers it is allowed (the later layer's
    ``env_vars`` simply wins, since sets render last).
    """
    sets = layer.get("env_vars") or {}
    return [
        (field, key)
        for field in ("env_prepend", "env_append")
        for key in (layer.get(field) or {})
        if key in sets
    ]


def _tool_commands(value: Any) -> list[str]:
    # The command names a tool entry matches instead of its own name.
    if not isinstance(value, list):
        raise _Invalid(f"expected a list of command names, got {_describe(value)}")
    names = [_str_item(v, i) for i, v in enumerate(value)]
    for i, name in enumerate(names):
        if name == "" or "/" in name or any(ch.isspace() for ch in name):
            raise _Invalid(
                f"entry {i + 1}: {name!r} is not a command name (the first word of a "
                'command with any path stripped, e.g. "xrun")'
            )
    return names


def _type_tools(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise _Invalid(f"expected a list of tool names, got {_describe(value)}")
    return [_str_item(v, i) for i, v in enumerate(value)]


# ---------------------------------------------------------------------------
# Line lookup (tomllib reports no positions)
# ---------------------------------------------------------------------------

_KEY_PART = r'(?:[A-Za-z0-9_-]+|"(?:[^"\\]|\\.)*"|\'[^\']*\')'
_DOTTED = rf"{_KEY_PART}(?:\s*\.\s*{_KEY_PART})*"
_HEADER_RE = re.compile(rf"^\s*\[\s*({_DOTTED})\s*\]\s*(?:#.*)?$")
_KEY_RE = re.compile(rf"^\s*({_DOTTED})\s*=")


def _split_dotted(text: str) -> tuple[str, ...]:
    parts = re.findall(_KEY_PART, text)
    return tuple(p[1:-1] if p[:1] in ("'", '"') else p for p in parts)


def find_line(text: str, path: Sequence[str]) -> int | None:
    """The 1-based line in *text* that sets the dotted key *path*.

    Matches a ``[header]`` or ``key =`` line whose full dotted path equals
    *path*; failing that, the first line inside it (a ``[path.sub]``
    header); failing that, the line for the longest prefix of *path* (the
    enclosing table's header or inline-table key); else None.
    """
    target = tuple(path)
    header: tuple[str, ...] = ()
    inside: int | None = None
    best: tuple[int, int] | None = None  # (prefix length, line)
    for number, line in enumerate(text.splitlines(), 1):
        match = _HEADER_RE.match(line)
        if match:
            header = _split_dotted(match.group(1))
            full = header
        else:
            match = _KEY_RE.match(line)
            if not match:
                continue
            full = header + _split_dotted(match.group(1))
        if full == target:
            return number
        if inside is None and full[: len(target)] == target:
            inside = number
        elif target[: len(full)] == full and (best is None or len(full) > best[0]):
            best = (len(full), number)
    if inside is not None:
        return inside
    return best[1] if best else None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class _Collector:
    """Builds located ConfigErrors for one file."""

    def __init__(self, path: Path, text: str):
        self.path = path
        self.text = text
        self.errors: list[ConfigError] = []
        self.warnings: list[str] = []

    def error(
        self,
        section: Sequence[str],
        key: str | None,
        message: str,
        subkey: str | None = None,
    ) -> None:
        where = [*section, *([key] if key is not None else []), *([subkey] if subkey else [])]
        line = find_line(self.text, where)
        label = ".".join(_quote(p) for p in section)
        prefix = f"[{label}] " if label else ""
        shown = f"{key}.{_quote(subkey)}" if subkey is not None else key
        what = f"{prefix}{shown}: " if key is not None else prefix
        self.errors.append(ConfigError(f"{what}{message}", path=self.path, line=line))

    def warning(self, section: Sequence[str], message: str) -> None:
        line = find_line(self.text, list(section))
        loc = f"{self.path}:{line}" if line else str(self.path)
        label = ".".join(_quote(p) for p in section)
        self.warnings.append(f"{loc}: [{label}] {message}")


def _quote(part: str) -> str:
    return part if re.fullmatch(r"[A-Za-z0-9_-]+", part) else f'"{part}"'


def _unknown_key(key: str, allowed: Iterable[str]) -> str:
    close = difflib.get_close_matches(key, list(allowed), n=1)
    hint = f"; did you mean '{close[0]}'?" if close else ""
    return f"unknown key{hint}"


def _validate_job_layer(
    c: _Collector,
    section: tuple[str, ...],
    table: Any,
    *,
    structural: Mapping[str, Callable[[Any], Any]] | None = None,
    defaults: bool = False,
) -> dict[str, Any]:
    """Validate one job layer; returns the table with its valid keys only.

    *defaults* is True for ``[defaults]``, the one layer that accepts
    :data:`DEFAULTS_ONLY_FIELDS`.
    """
    if not isinstance(table, dict):
        c.error(section[:-1], section[-1], f"expected a table, got {_describe(table)}")
        return {}
    validators: dict[str, Callable[[Any], Any]] = {
        key: validator
        for key, validator in FIELD_VALIDATORS.items()
        if defaults or key not in DEFAULTS_ONLY_FIELDS
    }
    validators.update(structural or {})
    allowed = list(validators)
    result: dict[str, Any] = {}
    for key, value in table.items():
        validator = validators.get(key)
        if validator is None:
            c.error(section, key, _unknown_key(key, allowed))
            continue
        try:
            result[key] = validator(value)
        except _Invalid as exc:
            c.error(section, key, exc.message, exc.subkey)
    for field, key in env_conflicts(result):
        c.error(
            section,
            field,
            f"{key} is also set by env_vars in this section; set it or {field[4:]} to it, not both",
            key,
        )
    return result


def _validate_named(
    c: _Collector, namespace: str, value: Any, layer: Callable[[str, Any], dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        c.error((), namespace, f"expected a table, got {_describe(value)}")
        return {}
    return {name: layer(name, entry) for name, entry in value.items()}


def validate_file(
    path: Path,
    text: str,
    data: dict[str, Any],
    scheduler_keys: Callable[[str], Mapping[str, type] | None],
    *,
    local: bool = False,
) -> tuple[dict[str, Any], list[ConfigError], list[str]]:
    """Validate one parsed config file.

    Args:
        path: The file, for error locations.
        text: Its raw text, for line lookup.
        data: ``tomllib``'s parse of *text*.
        scheduler_keys: Maps a scheduler name to its declared
            ``CONFIG_KEYS``, or None if the package has no such backend.
        local: The file is a local file, read after a project file: any
            top-level key outside :data:`LOCAL_FILE_KEYS` is an error and
            is not read.

    Returns:
        ``(converted, errors, warnings)``: the file's sections with their
        valid keys, every problem found, and every warning.
    """
    c = _Collector(path, text)

    if "config_version" not in data:
        c.errors.append(
            ConfigError(
                "missing config_version; add `config_version = 1` as the first line",
                path=path,
            )
        )
    elif data["config_version"] != CONFIG_VERSION or isinstance(data["config_version"], bool):
        c.error(
            (),
            "config_version",
            f"unsupported version {data['config_version']!r}; this hpc-runner reads "
            f"config_version = {CONFIG_VERSION}",
        )

    for key in data:
        if key not in TOP_LEVEL_KEYS:
            c.error((), key, _unknown_key(key, TOP_LEVEL_KEYS))
        elif local and key not in LOCAL_FILE_KEYS:
            c.error(
                (),
                key,
                "not allowed in a local file, which holds [tools.*] and [types.*] only; "
                "set it in the project file",
            )
    if local:
        data = {key: value for key, value in data.items() if key in LOCAL_FILE_KEYS}

    converted: dict[str, Any] = {}

    # Which scheduler this installation submits to. A name the registry does
    # not know is reported by get_scheduler, which also sees schedulers
    # registered at runtime.
    if "scheduler" in data:
        if isinstance(data["scheduler"], str) and data["scheduler"]:
            converted["scheduler"] = data["scheduler"]
        else:
            c.error(
                (), "scheduler", f"expected a scheduler name, got {_describe(data['scheduler'])}"
            )

    # The script sourced to define `module` on the nodes. A node fact, so it
    # is set once for the installation rather than once per scheduler.
    if "module_init_script" in data:
        if isinstance(data["module_init_script"], str):
            converted["module_init_script"] = data["module_init_script"]
        else:
            c.error(
                (),
                "module_init_script",
                f"expected a path, got {_describe(data['module_init_script'])}",
            )

    if "defaults" in data:
        converted["defaults"] = _validate_job_layer(
            c, ("defaults",), data["defaults"], defaults=True
        )

    def tool(name: str, entry: Any) -> dict[str, Any]:
        section = ("tools", name)

        def options(value: Any) -> dict[str, dict[str, Any]]:
            if not isinstance(value, dict):
                raise _Invalid(f"expected a table of option patterns, got {_describe(value)}")
            return {
                pattern: _validate_job_layer(c, (*section, "options", pattern), opt)
                for pattern, opt in value.items()
            }

        return _validate_job_layer(
            c, section, entry, structural={"options": options, "commands": _tool_commands}
        )

    def job_type(name: str, entry: Any) -> dict[str, Any]:
        return _validate_job_layer(c, ("types", name), entry, structural={"tools": _type_tools})

    if "tools" in data:
        converted["tools"] = _validate_named(c, "tools", data["tools"], tool)
    if "types" in data:
        converted["types"] = _validate_named(c, "types", data["types"], job_type)

    if "schedulers" in data:
        converted["schedulers"] = _validate_named(
            c,
            "schedulers",
            data["schedulers"],
            lambda name, entry: _validate_scheduler(c, name, entry, scheduler_keys(name)),
        )

    if "trace" in data:
        from hpc_runner.trace import TRACE_KEYS

        converted["trace"] = _validate_typed(c, ("trace",), data["trace"], TRACE_KEYS)

    return converted, c.errors, c.warnings


def _validate_scheduler(
    c: _Collector, name: str, table: Any, declared: Mapping[str, type] | None
) -> dict[str, Any]:
    section = ("schedulers", name)
    if not isinstance(table, dict):
        c.error(("schedulers",), name, f"expected a table, got {_describe(table)}")
        return {}
    if declared is None:
        c.warning(section, f"no scheduler '{name}' in this hpc-runner; section not checked")
        return dict(table)
    for key in RETIRED_SCHEDULER_KEYS.keys() & table.keys():
        c.error(section, key, RETIRED_SCHEDULER_KEYS[key])
    return _validate_typed(
        c, section, {k: v for k, v in table.items() if k not in RETIRED_SCHEDULER_KEYS}, declared
    )


def _validate_typed(
    c: _Collector, section: tuple[str, ...], table: Any, declared: Mapping[str, type]
) -> dict[str, Any]:
    """A flat table of scalar keys with declared types (``[schedulers.x]``, ``[trace]``)."""
    if not isinstance(table, dict):
        c.error(section[:-1], section[-1], f"expected a table, got {_describe(table)}")
        return {}
    result: dict[str, Any] = {}
    for key, value in table.items():
        expected = declared.get(key)
        if expected is None:
            c.error(section, key, _unknown_key(key, declared))
            continue
        if not isinstance(value, expected) or (expected is not bool and isinstance(value, bool)):
            c.error(section, key, f"expected {expected.__name__}, got {_describe(value)}")
            continue
        result[key] = value
    return result
