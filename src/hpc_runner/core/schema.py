"""Config schema: which keys each config section accepts, and their types (D11).

The keys allowed in a job layer (``[defaults]``, ``[tools.x]``,
``[tools.x.options."..."]``, ``[types.x]``) are the :class:`JobSpec` fields
minus the per-invocation ones; :data:`FIELD_VALIDATORS` gives each its
expected TOML type. Three structural keys sit beside them: ``options`` and
``commands`` under ``[tools.x]``, ``tools`` under ``[types.x]``. Validation
converts as it checks: verb tables become :class:`~hpc_runner.core.merge.Set`
/ :class:`~hpc_runner.core.merge.Edit`.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from hpc_runner.core.exceptions import ConfigError
from hpc_runner.core.merge import Edit, Set
from hpc_runner.core.units import Duration, Memory

#: The config format this package reads.
CONFIG_VERSION = 1

#: JobSpec fields that only make sense per invocation, never in config.
PER_INVOCATION_FIELDS = frozenset({"command", "type", "dependencies", "array"})

#: Top-level keys of a config file.
TOP_LEVEL_KEYS = (
    "config_version",
    "extends",
    "defaults",
    "tools",
    "types",
    "schedulers",
    "trace",
)


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


def _list_of(item: Callable[[Any, int], Any], what: str) -> Callable[[Any], Any]:
    """A D1 list validator: a plain list, ``{ set = [...] }`` or ``{ remove, add }``."""

    expected = f"expected a list of {what} or a {{ set | remove | add }} table"

    def items(value: Any, verb: str | None) -> list[Any]:
        if not isinstance(value, list):
            where = f"{verb}: " if verb else ""
            raise _Invalid(f"{where}{expected}, got {_describe(value)}")
        return [item(entry, i) for i, entry in enumerate(value)]

    def validate(value: Any) -> Any:
        if isinstance(value, list):
            return items(value, None)
        if not isinstance(value, dict):
            raise _Invalid(f"{expected}, got {_describe(value)}")
        unknown = [k for k in value if k not in ("set", "remove", "add")]
        if unknown:
            raise _Invalid(
                f"unknown list verb {unknown[0]!r}; use {{ set = [...] }} "
                "or { remove = [...], add = [...] }"
            )
        if "set" in value:
            if len(value) > 1:
                raise _Invalid("'set' cannot be combined with 'remove' or 'add'")
            return Set(items(value["set"], "set"))
        return Edit(
            remove=items(value.get("remove", []), "remove"),
            add=items(value.get("add", []), "add"),
        )

    return validate


def _str_item(value: Any, index: int) -> str:
    if not isinstance(value, str):
        raise _Invalid(f"entry {index + 1}: expected a string, got {_describe(value)}")
    return value


def _resource_item(value: Any, index: int) -> dict[str, Any]:
    where = f"entry {index + 1}"
    if not isinstance(value, dict):
        raise _Invalid(f"{where}: expected a {{ name, value }} table, got {_describe(value)}")
    keys = set(value)
    if keys != {"name", "value"}:
        extra = sorted(keys - {"name", "value"})
        missing = sorted({"name", "value"} - keys)
        problem = f"unknown key {extra[0]!r}" if extra else f"missing {missing[0]!r}"
        raise _Invalid(f"{where}: {problem}; a resource is {{ name = ..., value = ... }}")
    if not isinstance(value["name"], str):
        raise _Invalid(f"{where}: name: expected a string, got {_describe(value['name'])}")
    val = value["value"]
    if isinstance(val, bool) or not isinstance(val, int | str):
        raise _Invalid(f"{where}: value: expected an integer or string, got {_describe(val)}")
    return {"name": value["name"], "value": val}


def _env_name_item(value: Any, index: int) -> str:
    name = _str_item(value, index)
    if not ENV_NAME.fullmatch(name):
        raise _Invalid(f"entry {index + 1}: {name!r} is not a valid environment variable name")
    return name


_str_list = _list_of(_str_item, "strings")
_env_name_list = _list_of(_env_name_item, "environment variable names")
_resources = _list_of(_resource_item, "{ name, value } tables")


def _scheduler_args(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _Invalid(f"expected a table of scheduler name = [args], got {_describe(value)}")
    result: dict[str, Any] = {}
    for sched, args in value.items():
        try:
            result[sched] = _str_list(args)
        except _Invalid as exc:
            raise _Invalid(exc.message, subkey=sched) from None
    return result


#: Every config-settable JobSpec field and its validator. A test asserts this
#: covers exactly ``fields(JobSpec) - PER_INVOCATION_FIELDS``.
FIELD_VALIDATORS: dict[str, Callable[[Any], Any]] = {
    "name": _str,
    "cpu": _int,
    "mem": _parsed(Memory.parse),
    "time": _parsed(Duration.parse),
    "queue": _str,
    "priority": _int,
    "project": _str,
    "nodes": _int,
    "tasks": _int,
    "stdout": _output_path("empty; omit the key to use the default"),
    "stderr": _output_path("empty; omit the key to merge into stdout"),
    "inherit_env": _bool,
    "workdir": _str,
    "shell": _str,
    "use_cwd": _bool,
    "venv": _venv,
    "env_vars": _env_dict,
    "env_prepend": _env_dict,
    "env_append": _env_dict,
    "env_unset": _env_name_list,
    "env_passthrough": _env_name_list,
    "modules": _str_list,
    "modules_path": _str_list,
    "modules_path_append": _str_list,
    "resources": _resources,
    "scheduler_args": _scheduler_args,
    "interactive": _bool,
}

#: List-valued job settings, merged per D1.
LIST_FIELDS = frozenset(
    {
        "env_unset",
        "env_passthrough",
        "modules",
        "modules_path",
        "modules_path_append",
        "resources",
    }
)

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

#: Dict-valued job settings: keys merge across layers, later layer wins per key.
DICT_FIELDS = frozenset({"env_vars", "env_prepend", "env_append"})


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
    # The command names a tool entry matches instead of its own name. Merged
    # across files like every other list (D1).
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
    # Two config files both setting [types.T] tools follow the D1 list rule
    # (append + dedupe; `{ set = [...] }` to replace), like every other list.
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
) -> dict[str, Any]:
    """Validate one job layer; returns the converted table (valid keys only)."""
    if not isinstance(table, dict):
        c.error(section[:-1], section[-1], f"expected a table, got {_describe(table)}")
        return {}
    extra = dict(structural or {})
    allowed = list(FIELD_VALIDATORS) + list(extra)
    result: dict[str, Any] = {}
    for key, value in table.items():
        validator = FIELD_VALIDATORS.get(key) or extra.get(key)
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
) -> tuple[dict[str, Any], list[ConfigError], list[str]]:
    """Validate one parsed config file.

    Args:
        path: The file, for error locations.
        text: Its raw text, for line lookup.
        data: ``tomllib``'s parse of *text*.
        scheduler_keys: Maps a scheduler name to its declared
            ``CONFIG_KEYS``, or None if the package has no such backend.

    Returns:
        ``(converted, errors, warnings)``: the file's sections with every
        valid key converted (verb tables become ``Set``/``Edit``), every
        problem found, and every warning.
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

    if "extends" in data and not isinstance(data["extends"], str):
        c.error((), "extends", f"expected a path, got {_describe(data['extends'])}")

    converted: dict[str, Any] = {}

    if "defaults" in data:
        converted["defaults"] = _validate_job_layer(c, ("defaults",), data["defaults"])

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
    return _validate_typed(c, section, table, declared)


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
