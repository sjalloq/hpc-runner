"""resolve(): the one place a JobSpec is merged with config into a Job."""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Iterable, Iterator, Mapping
from types import MappingProxyType
from typing import Any

from hpc_runner.core.array import ArrayRange
from hpc_runner.core.config import HPCConfig, _find_git_root
from hpc_runner.core.exceptions import ConfigError, NotSupportedError
from hpc_runner.core.expand import check_hpc_refs, expand
from hpc_runner.core.host import Host
from hpc_runner.core.job import Job
from hpc_runner.core.schema import (
    DEFAULTS_ONLY_FIELDS,
    ENV_NAME,
    EXPANDED_FIELDS,
    FIELD_VALIDATORS,
    count_problem,
    env_conflicts,
)
from hpc_runner.core.spec import JobSpec
from hpc_runner.core.units import Duration, Memory

# Package defaults for fields that are never left unset.
_PACKAGE_DEFAULTS: dict[str, Any] = {
    "inherit_env": True,
    "interactive": False,
    # D9: hpc-runner always names the output file; never the scheduler default.
    "stdout": "${hpc.job_name}.${hpc.job_id}.out",
}

# ${hpc.*} names that only exist inside the running job, so an output path
# (which the scheduler opens before the job starts) cannot use them.
_NOT_IN_PATHS = ("cpus", "tmpdir")
# Why an empty output pattern is wrong (also the schema's message).
_EMPTY_OUTPUT = {
    "stdout": "empty; omit the key to use the default",
    "stderr": "empty; omit the key to merge into stdout",
}
_HPC_REF = re.compile(r"\$\{hpc\.([A-Za-z_][A-Za-z0-9_]*)\}")

# How a key combines across layers. The job consumes modules, licences and
# devices, so these add up: a type that pulls in two tools needs both tools'.
# Every other key says where and how the job runs, and overrides: a layer
# that sets it replaces what the earlier layers set, whole.
ADDING_LISTS = frozenset({"modules"})
ADDING_MAPS = frozenset({"licenses", "devices"})

# Fields that hold one value: the last layer that sets one, else the package
# default.
_SCALARS: tuple[str, ...] = (
    "cpu",
    "mem",
    "time",
    "queue",
    "project",
    "stdout",
    "stderr",
    "inherit_env",
    "workdir",
    "interactive",
)

# An adding list being built, each item paired with the layer that added it.
_Tracked = list[tuple[Any, str]]


def combine_layers(earlier: Mapping[str, Any], later: Mapping[str, Any]) -> dict[str, Any]:
    """One layer with the effect of *earlier* then *later*.

    Used where two config files hold the same section. ``modules`` appends
    and drops duplicates, first occurrence kept; ``licenses`` and
    ``devices`` add names, the later count winning; every other key is
    replaced by *later*'s value.
    """
    result = dict(earlier)
    for key, value in later.items():
        if key in ADDING_LISTS and key in result:
            result[key] = _unique([*result[key], *value])
        elif key in ADDING_MAPS and key in result:
            result[key] = {**result[key], **value}
        else:
            result[key] = value
    return result


def _unique(items: Iterable[Any]) -> list[Any]:
    """*items* without duplicates, the first occurrence of each kept."""
    result: list[Any] = []
    for item in items:
        if item not in result:
            result.append(item)
    return result


def resolve(spec: JobSpec, config: HPCConfig, host: Host) -> Job:
    """Resolve *spec* against *config* and the submitting *host*.

    The layers are :meth:`HPCConfig.layers_for` (``defaults``, then the
    tool or type chain) followed by the spec itself. ``modules``,
    ``licenses`` and ``devices`` add up across the layers: modules append,
    duplicates dropped and the first occurrence kept; a licence or device
    named again takes the later count. Every other key overrides: the last
    layer that sets it supplies the whole value (an empty list or table
    clears it), else the package default applies. ``modules_path`` and
    ``modules_path_append`` are read from ``defaults`` and the spec only.
    Every string that reaches the job script or the scheduler is
    expanded per D5 (:func:`~hpc_runner.core.expand.expand`). The command
    is not: it is kept as written, a list also as its words, and only the
    names of its ``${hpc.*}`` references are checked; the scheduler renders
    those. The returned :class:`Job` records the source of every value in
    ``provenance``.

    ``mem`` and ``time`` are parsed into :class:`Memory` and
    :class:`Duration` after expansion (D10). ``workdir`` is made absolute
    against the host's cwd, which it defaults to; relative output paths
    are relative to it (D9).

    Raises:
        ConfigError: The spec names an unknown type, the type lists an
            unknown tool, a value references an undefined variable, the
            command has a ``${hpc.x}`` with an unknown name, a
            module entry expands to nothing, an ``env_passthrough`` name is
            not set on the host, a tool or type layer sets ``modules_path``
            or ``modules_path_append``, the venv has no ``bin/activate``, the
            spec sets and prepends/appends the same variable, a spec's
            ``licenses`` or ``devices`` entry is not a name with a whole
            number of 1 or more, ``mem`` or
            ``time`` does not parse (D10), or an output path is empty,
            uses ``${hpc.cpus}`` or ``${hpc.tmpdir}``, or has ``${hpc.*}``
            in its directory part.
        NotSupportedError: An array job was requested interactively.
        ValueError: ``array`` is not a valid range.
    """
    command_words: tuple[str, ...] | None
    if isinstance(spec.command, str):
        command, command_words = spec.command, None
    else:
        command_words = tuple(spec.command)
        command = shlex.join(command_words)
    for text in (command,) if command_words is None else command_words:
        check_hpc_refs(text, where="spec: command")
    layers = [*config.layers_for(spec), ("spec", _spec_layer(spec))]

    # Overriding keys: the value and the one layer that set it.
    scalars: dict[str, Any] = {}
    origin: dict[str, str] = {}
    # Adding keys: every item with the layer that added it.
    added: dict[str, _Tracked] = {f: [] for f in ADDING_LISTS}
    counts: dict[str, dict[str, tuple[int, str]]] = {f: {} for f in ADDING_MAPS}

    for layer_name, table in layers:
        for key, value in table.items():
            if key not in FIELD_VALIDATORS or value is None:
                continue  # e.g. "command" in a hand-built config: never from config
            if key in DEFAULTS_ONLY_FIELDS and layer_name not in ("defaults", "spec"):
                raise ConfigError(f"[{layer_name}] {key}: unknown key")
            if key in ADDING_LISTS:
                seen = [item for item, _ in added[key]]
                added[key] += [(item, layer_name) for item in _unique(value) if item not in seen]
            elif key in ADDING_MAPS:
                counts[key].update({name: (count, layer_name) for name, count in value.items()})
            else:
                scalars[key] = value
                origin[key] = layer_name

    variables = _ResolverVariables(host)

    def exp(value: str, where: str) -> str:
        return expand(value, host_env=host.env, extra=variables, where=where)

    provenance: dict[str, str | tuple[tuple[Any, str], ...]] = {
        "command": "spec",
        "command_words": "spec",
        "type": "spec",
    }

    def pick(field: str) -> Any:
        if field in scalars:
            provenance[field] = origin[field]
            value = scalars[field]
            if field in EXPANDED_FIELDS and isinstance(value, str):
                value = exp(value, f"{origin[field]}: {field}")
            return value
        provenance[field] = "package"
        return _PACKAGE_DEFAULTS.get(field)

    values: dict[str, Any] = {field: pick(field) for field in _SCALARS}

    # D10: mem and time are parsed after expansion.
    values["mem"] = _parse_unit(values["mem"], Memory, "mem", provenance)
    values["time"] = _parse_unit(values["time"], Duration, "time", provenance)

    # D9: output paths may use ${hpc.*} names the scheduler knows before the
    # job starts; relative paths are relative to the working directory.
    for field in ("stdout", "stderr"):
        pattern = values[field]
        if pattern is None:
            continue
        where = f"{provenance[field]}: {field}"
        if pattern == "":
            raise ConfigError(f"{where}: {_EMPTY_OUTPUT[field]}")
        for ref in _HPC_REF.findall(pattern):
            if ref in _NOT_IN_PATHS:
                raise ConfigError(f"{where}: ${{hpc.{ref}}} is not available in output paths")
        # The directory is created on the submit host before submission, so
        # runtime values are allowed only in the final path component.
        if "${hpc." in os.path.dirname(pattern):
            raise ConfigError(
                f"{where}: the directory part of {field} cannot depend on ${{hpc.*}}; "
                "SGE discards output to a missing directory"
            )

    if values["workdir"] is None:
        provenance["workdir"] = "host"
    values["workdir"] = os.path.normpath(host.cwd / (values["workdir"] or "."))

    # Name: spec, config, else generated from the submitting user.
    name = pick("name")
    if name is None:
        name = _generate_name(host.user, command)
        provenance["name"] = "host"

    venv = _resolve_venv(pick("venv"), origin.get("venv"), host, provenance)

    def env_dict(field: str) -> Mapping[str, str]:
        layer = provenance[field] = origin.get(field, "package")
        table = scalars.get(field) or {}
        return MappingProxyType({k: exp(str(v), f"{layer}: {field}.{k}") for k, v in table.items()})

    def override_list(field: str) -> tuple[Any, ...]:
        """An overriding list: the one layer's value, as it wrote it."""
        provenance[field] = origin.get(field, "package")
        return tuple(scalars.get(field) or ())

    # Module lists: an entry that expands to nothing would render a bare
    # `module load`, a silent no-op that leaves the job on whatever tools it
    # inherited: exactly the "works for me" failure, so it is an error.
    def module_entries(field: str, pairs: _Tracked) -> _Tracked:
        expanded: _Tracked = []
        for raw, layer in pairs:
            value = exp(raw, f"{layer}: {field} entry {raw!r}")
            if not value.strip():
                raise ConfigError(
                    f"{layer}: {field} entry {raw!r} expands to an empty string; the "
                    "referenced environment variable is set but empty in the submitting shell"
                )
            expanded.append((value, layer))
        return expanded

    def module_path(field: str) -> tuple[str, ...]:
        layer = provenance[field] = origin.get(field, "package")
        entries = module_entries(field, [(raw, layer) for raw in scalars.get(field) or ()])
        return tuple(value for value, _ in entries)

    modules = module_entries("modules", added["modules"])
    provenance["modules"] = tuple(modules)

    env_unset = override_list("env_unset")

    # env_passthrough: only meaningful without -V; with inherit_env the
    # variables arrive anyway, so the names are recorded but not looked up.
    passthrough_layer = origin.get("env_passthrough", "package")
    passthrough_names = tuple(scalars.get("env_passthrough") or ())
    provenance["env_passthrough"] = tuple((var, passthrough_layer) for var in passthrough_names)
    env_passthrough: dict[str, str] = {}
    if not values["inherit_env"]:
        for var in passthrough_names:
            if var not in host.env:
                raise ConfigError(
                    f"{passthrough_layer}: env_passthrough: {var} is not set "
                    "in the submitting environment"
                )
            env_passthrough[var] = host.env[var]

    def count_map(field: str) -> Mapping[str, int]:
        provenance[field] = tuple(
            ((name, count), layer) for name, (count, layer) in counts[field].items()
        )
        return MappingProxyType({name: count for name, (count, _) in counts[field].items()})

    features = override_list("features")

    args_layer = provenance["scheduler_args"] = origin.get("scheduler_args", "package")
    scheduler_args = tuple(
        exp(arg, f"{args_layer}: scheduler_args") for arg in scalars.get("scheduler_args") or ()
    )

    # Dependencies and array come from the spec only: they are per
    # invocation, so a config layer has nothing meaningful to say about them.
    dependencies = tuple(spec.dependencies)
    provenance["dependencies"] = "spec" if spec.dependencies else "package"
    array = ArrayRange.parse(str(spec.array)) if spec.array is not None else None
    provenance["array"] = "spec" if spec.array is not None else "package"

    if array is not None and values["interactive"]:
        raise NotSupportedError("array jobs cannot be interactive")

    return Job(
        command=command,
        command_words=command_words,
        type=spec.type,
        name=str(name),
        venv=venv,
        env_unset=env_unset,
        env_passthrough=MappingProxyType(env_passthrough),
        licenses=count_map("licenses"),
        devices=count_map("devices"),
        features=features,
        scheduler_args=scheduler_args,
        dependencies=dependencies,
        array=array,
        provenance=MappingProxyType(provenance),
        env_vars=env_dict("env_vars"),
        env_prepend=env_dict("env_prepend"),
        env_append=env_dict("env_append"),
        modules=tuple(value for value, _ in modules),
        modules_path=module_path("modules_path"),
        modules_path_append=module_path("modules_path_append"),
        **values,
    )


def _parse_unit(
    value: Any,
    kind: type[Memory] | type[Duration],
    field: str,
    provenance: Mapping[str, Any],
) -> Any:
    """*value* as a :class:`Memory` / :class:`Duration`: parsed if a string.

    Raises:
        ConfigError: The string does not parse; names the field and the
            layer that supplied it.
    """
    if not isinstance(value, str):
        return value
    try:
        return kind.parse(value)
    except ValueError as exc:
        raise ConfigError(f"{provenance[field]}: {field}: {exc}") from None


def _resolve_venv(
    value: str | bool | None,
    layer: str | None,
    host: Host,
    provenance: dict[str, str | tuple[tuple[Any, str], ...]],
) -> str | None:
    """D4: unset captures ``$VIRTUAL_ENV``, a path is used as is, False is none.

    *value* is already expanded. A venv that will be activated must have
    ``bin/activate`` on the submit host (the filesystem is shared).

    Raises:
        ConfigError: The venv has no ``bin/activate``.
    """
    if value is None:
        captured = host.virtual_env
        if captured is None:
            provenance["venv"] = "package"
            return None
        provenance["venv"] = "captured from VIRTUAL_ENV"
        if not (host.cwd / captured / "bin" / "activate").is_file():
            raise ConfigError(
                f"venv {captured} captured from VIRTUAL_ENV has no bin/activate "
                "(stale VIRTUAL_ENV?)"
            )
        return captured
    if value is False:
        provenance["venv"] = f"{layer} (disabled)"
        return None
    # A venv path containing ${hpc.*} only exists at run time, so the
    # submit-host check below rejects it: a venv must be on the shared
    # filesystem (D4 assumes one).
    venv = str(value)
    if not (host.cwd / venv / "bin" / "activate").is_file():
        raise ConfigError(f"venv {venv} has no bin/activate (stale VIRTUAL_ENV?)")
    return venv


class _ResolverVariables(Mapping[str, str]):
    """The resolver variables of D5: ``CWD``, ``USER`` and ``GIT_ROOT``.

    ``CONFIG_DIR`` is bound per file at load time instead
    (:func:`~hpc_runner.core.expand.bind_config_dir`). ``GIT_ROOT`` is
    looked up only when referenced; outside a repository it is an error.
    """

    _NAMES = ("CWD", "USER", "GIT_ROOT")

    def __init__(self, host: Host) -> None:
        self._host = host

    def __getitem__(self, name: str) -> str:
        if name == "CWD":
            return str(self._host.cwd)
        if name == "USER":
            return self._host.user
        if name == "GIT_ROOT":
            root = _find_git_root(self._host.cwd)
            if root is None:
                raise ConfigError(
                    f"${{GIT_ROOT}} is undefined: {self._host.cwd} is not inside a git repository"
                )
            return str(root)
        raise KeyError(name)

    def __iter__(self) -> Iterator[str]:
        return iter(self._NAMES)

    def __len__(self) -> int:
        return len(self._NAMES)


def _spec_layer(spec: JobSpec) -> dict[str, Any]:
    """The spec as one more layer: every config-settable field it sets.

    Config layers are checked by the schema on load; the spec gets the
    same environment checks here, since its names reach the script.

    Raises:
        ConfigError: An environment variable name is not an identifier,
            the spec both sets and prepends/appends one variable, or a
            ``licenses`` or ``devices`` entry is not a name with a count
            (a whole number, 1 or more).
    """
    layer: dict[str, Any] = {}
    for field in FIELD_VALIDATORS:
        value = getattr(spec, field)
        if value is None:
            continue
        layer[field] = value
    for field in ("licenses", "devices"):
        for name, count in (layer.get(field) or {}).items():
            if not isinstance(name, str) or name == "":
                raise ConfigError(f"spec: {field}: {name!r} is not a name")
            if problem := count_problem(count):
                raise ConfigError(f"spec: {field}.{name}: {problem}")
    for field in ("env_vars", "env_prepend", "env_append", "env_unset", "env_passthrough"):
        for name in _names(layer.get(field)):
            if not ENV_NAME.fullmatch(str(name)):
                raise ConfigError(
                    f"spec: {field}: {name!r} is not a valid environment variable name"
                )
    for field, key in env_conflicts(layer):
        raise ConfigError(
            f"spec: {key} is both set (env_vars) and in {field}; set it or "
            f"{field[4:]} to it, not both"
        )
    return layer


def _names(value: Any) -> list[Any]:
    """The variable names in an env table or a list of names."""
    return [] if value is None else list(value)


def _generate_name(user: str, command: str) -> str:
    """``<user>_<first command word>``, skipping ``VAR=value`` prefixes."""
    for part in command.split():
        if "=" not in part:
            return f"{user}_{part.split('/')[-1]}"
    return f"{user}_job"
