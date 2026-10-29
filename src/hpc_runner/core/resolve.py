"""resolve(): the one place a JobSpec is merged with config into a Job."""

from __future__ import annotations

import shlex
from collections.abc import Iterator, Mapping
from types import MappingProxyType
from typing import Any

from hpc_runner.core.array import ArrayRange
from hpc_runner.core.config import HPCConfig, _find_git_root
from hpc_runner.core.exceptions import ConfigError, NotSupportedError
from hpc_runner.core.expand import expand
from hpc_runner.core.host import Host
from hpc_runner.core.job import Job
from hpc_runner.core.merge import Edit, KeyFunc, Set, key_for, merge_list
from hpc_runner.core.resources import Resource, ResourceSet
from hpc_runner.core.schema import (
    DICT_FIELDS,
    ENV_NAME,
    EXPANDED_FIELDS,
    FIELD_VALIDATORS,
    LIST_FIELDS,
    env_conflicts,
)
from hpc_runner.core.spec import JobSpec

# Package defaults for fields that are never left unset.
_PACKAGE_DEFAULTS: dict[str, Any] = {
    "inherit_env": True,
    "shell": "/bin/bash",
    "use_cwd": True,
    "interactive": False,
}

# Scalar fields: the most specific layer that sets them, else package default.
_SCALARS: tuple[str, ...] = (
    "cpu",
    "mem",
    "time",
    "queue",
    "priority",
    "project",
    "nodes",
    "tasks",
    "stdout",
    "stderr",
    "inherit_env",
    "workdir",
    "shell",
    "use_cwd",
    "interactive",
)

# A list being built, each item paired with the layer that contributed it.
_Tracked = list[tuple[Any, str]]


def resolve(spec: JobSpec, config: HPCConfig, host: Host) -> Job:
    """Resolve *spec* against *config* and the submitting *host*.

    The layers are :meth:`HPCConfig.layers_for` (``defaults``, then the
    tool or type chain) followed by the spec itself. Scalars: the most
    specific layer that sets one wins, else the package default. Lists
    merge per D1 (:func:`~hpc_runner.core.merge.merge_list`); dicts merge
    by key. Every string that reaches the job script or the scheduler is
    expanded per D5 (:func:`~hpc_runner.core.expand.expand`); the command
    is not. The returned :class:`Job` records the source of every value in
    ``provenance``.

    Raises:
        ConfigError: The spec names an unknown type, the type lists an
            unknown tool, a value references an undefined variable, a
            module entry expands to nothing, an ``env_passthrough`` name is
            not set on the host, the venv has no ``bin/activate``, or the
            spec sets and prepends/appends the same variable.
        NotSupportedError: An array job was requested interactively.
        ValueError: ``array`` is not a valid range.
    """
    command = spec.command if isinstance(spec.command, str) else shlex.join(spec.command)
    layers = [*config.layers_for(spec), ("spec", _spec_layer(spec))]

    scalars: dict[str, Any] = {}
    origin: dict[str, str] = {}
    dicts: dict[str, dict[str, tuple[Any, str]]] = {f: {} for f in DICT_FIELDS}
    lists: dict[str, _Tracked] = {f: [] for f in LIST_FIELDS}
    sched_args: dict[str, _Tracked] = {}

    for layer_name, table in layers:
        for key, value in table.items():
            if key not in FIELD_VALIDATORS or value is None:
                continue  # e.g. "command" in a hand-built config: never from config
            if key in DICT_FIELDS:
                dicts[key].update({k: (v, layer_name) for k, v in value.items()})
                origin[key] = layer_name
            elif key in LIST_FIELDS:
                lists[key] = _merge_tracked(lists[key], value, key_for(key), layer_name)
            elif key == "scheduler_args":
                # scheduler_args lists are argv, so the D1 verbs apply but
                # not the dedupe: `-l a=1 -l b=2` must keep both `-l`.
                for sched, args in value.items():
                    sched_args[sched] = _merge_tracked(
                        sched_args.get(sched, []), args, None, layer_name, dedupe=False
                    )
            else:
                scalars[key] = value
                origin[key] = layer_name

    variables = _ResolverVariables(host)

    def exp(value: str, where: str) -> str:
        return expand(value, host_env=host.env, extra=variables, where=where)

    provenance: dict[str, str | tuple[tuple[Any, str], ...]] = {
        "command": "spec",
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

    # Name: spec, config, else generated from the submitting user.
    name = pick("name")
    if name is None:
        name = _generate_name(host.user, command)
        provenance["name"] = "host"

    venv = _resolve_venv(pick("venv"), origin.get("venv"), host, provenance)

    def env_dict(field: str) -> Mapping[str, str]:
        provenance[field] = origin.get(field, "package")
        return MappingProxyType(
            {k: exp(str(v), f"{layer}: {field}.{k}") for k, (v, layer) in dicts[field].items()}
        )

    def tracked(field: str, pairs: _Tracked) -> tuple[Any, ...]:
        provenance[field] = tuple(pairs)
        return tuple(item for item, _ in pairs)

    # Module lists: an entry that expands to nothing would render a bare
    # `module load`, a silent no-op that leaves the job on whatever tools it
    # inherited: exactly the "works for me" failure, so it is an error.
    def module_list(field: str) -> tuple[str, ...]:
        expanded: _Tracked = []
        for raw, layer in lists[field]:
            value = exp(raw, f"{layer}: {field} entry {raw!r}")
            if not value.strip():
                raise ConfigError(
                    f"{layer}: {field} entry {raw!r} expands to an empty string; the "
                    "referenced environment variable is set but empty in the submitting shell"
                )
            expanded.append((value, layer))
        return tracked(field, expanded)

    env_unset = tracked("env_unset", lists["env_unset"])

    # env_passthrough: only meaningful without -V; with inherit_env the
    # variables arrive anyway, so the names are recorded but not looked up.
    tracked("env_passthrough", lists["env_passthrough"])
    env_passthrough: dict[str, str] = {}
    if not values["inherit_env"]:
        for var, layer in lists["env_passthrough"]:
            if var not in host.env:
                raise ConfigError(
                    f"{layer}: env_passthrough: {var} is not set in the submitting environment"
                )
            env_passthrough[var] = host.env[var]

    resource_pairs = [
        (Resource(item["name"], item["value"]), layer) for item, layer in lists["resources"]
    ]
    resources = ResourceSet(list(tracked("resources", resource_pairs)))

    expanded_args = {
        sched: [(exp(arg, f"{layer}: scheduler_args.{sched}"), layer) for arg, layer in pairs]
        for sched, pairs in sched_args.items()
    }
    scheduler_args = MappingProxyType(
        {sched: tuple(arg for arg, _ in pairs) for sched, pairs in expanded_args.items()}
    )
    provenance["scheduler_args"] = tuple(
        ((sched, arg), layer) for sched, pairs in expanded_args.items() for arg, layer in pairs
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
        type=spec.type,
        name=str(name),
        venv=venv,
        env_unset=env_unset,
        env_passthrough=MappingProxyType(env_passthrough),
        resources=resources,
        scheduler_args=scheduler_args,
        dependencies=dependencies,
        array=array,
        provenance=MappingProxyType(provenance),
        env_vars=env_dict("env_vars"),
        env_prepend=env_dict("env_prepend"),
        env_append=env_dict("env_append"),
        modules=module_list("modules"),
        modules_path=module_list("modules_path"),
        modules_path_append=module_list("modules_path_append"),
        **values,
    )


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
        ConfigError: An environment variable name is not an identifier, or
            the spec both sets and prepends/appends one variable.
    """
    layer: dict[str, Any] = {}
    for field in FIELD_VALIDATORS:
        value = getattr(spec, field)
        if value is None:
            continue
        if field == "resources":
            value = _resource_layer(value)
        layer[field] = value
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
    """The variable names in an env dict or a D1 list value."""
    if value is None:
        return []
    if isinstance(value, dict):
        return list(value)
    if isinstance(value, Set):
        return list(value.items)
    if isinstance(value, Edit):
        return [*value.remove, *value.add]
    return list(value)


def _resource_layer(value: Any) -> Any:
    """Normalise a spec's resources (ResourceSet, Resources or dicts) to dicts."""

    def item(entry: Any) -> dict[str, Any]:
        if isinstance(entry, Resource):
            return {"name": entry.name, "value": entry.value}
        return {"name": entry["name"], "value": entry["value"]}

    if isinstance(value, Set):
        return Set([item(e) for e in value.items])
    if isinstance(value, Edit):
        return Edit(remove=[item(e) for e in value.remove], add=[item(e) for e in value.add])
    return [item(e) for e in value]


def _merge_tracked(
    acc: _Tracked, value: Any, key: KeyFunc | None, layer: str, *, dedupe: bool = True
) -> _Tracked:
    """:func:`merge_list` over items paired with their contributing layer.

    An item already present unchanged keeps its earlier layer; a new item,
    a keyed item whose value changed, and every item of a ``Set`` take
    *layer*.
    """
    merged = merge_list([item for item, _ in acc], value, key=key, dedupe=dedupe)
    if isinstance(value, Set):
        return [(item, layer) for item in merged]
    if not dedupe:
        # Without dedupe the surviving earlier items keep their order and
        # everything after them is this layer's.
        removed = value.remove if isinstance(value, Edit) else ()
        survivors = [pair for pair in acc if pair[0] not in removed]
        return survivors + [(item, layer) for item in merged[len(survivors) :]]
    result: _Tracked = []
    for item in merged:
        for old_item, old_layer in acc:
            if old_item == item:
                result.append((item, old_layer))
                break
        else:
            result.append((item, layer))
    return result


def _generate_name(user: str, command: str) -> str:
    """``<user>_<first command word>``, skipping ``VAR=value`` prefixes."""
    for part in command.split():
        if "=" not in part:
            return f"{user}_{part.split('/')[-1]}"
    return f"{user}_job"
