"""resolve(): the one place a JobSpec is merged with config into a Job."""

from __future__ import annotations

import re
import shlex
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any

from hpc_runner.core.array import ArrayRange
from hpc_runner.core.config import HPCConfig, _expand_env_vars
from hpc_runner.core.exceptions import ConfigError, NotSupportedError
from hpc_runner.core.host import Host
from hpc_runner.core.job import Job
from hpc_runner.core.merge import Edit, KeyFunc, Set, key_for, merge_list
from hpc_runner.core.resources import Resource, ResourceSet
from hpc_runner.core.schema import DICT_FIELDS, FIELD_VALIDATORS, LIST_FIELDS
from hpc_runner.core.spec import JobSpec

# Matches a ``$VAR`` or ``${VAR}`` reference left behind after expansion.
_UNRESOLVED_VAR = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")

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
    by key. The returned :class:`Job` records the source of every value in
    ``provenance``.

    Raises:
        ConfigError: The spec names an unknown type, the type lists an
            unknown tool, or a module entry references an undefined or
            empty environment variable.
        NotSupportedError: An array job was requested interactively.
        ValueError: ``array`` is not a valid range.
    """
    command = spec.command if isinstance(spec.command, str) else shlex.join(spec.command)
    layers = [*config.layers_for(spec), ("spec", _spec_layer(spec))]

    scalars: dict[str, Any] = {}
    origin: dict[str, str] = {}
    dicts: dict[str, dict[str, Any]] = {f: {} for f in DICT_FIELDS}
    lists: dict[str, _Tracked] = {f: [] for f in LIST_FIELDS}
    sched_args: dict[str, _Tracked] = {}

    for layer_name, table in layers:
        for key, value in table.items():
            if key not in FIELD_VALIDATORS or value is None:
                continue  # e.g. "command" in a hand-built config: never from config
            if key in DICT_FIELDS:
                dicts[key].update(value)
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

    provenance: dict[str, str | tuple[tuple[Any, str], ...]] = {
        "command": "spec",
        "type": "spec",
    }

    def pick(field: str) -> Any:
        if field in scalars:
            provenance[field] = origin[field]
            return scalars[field]
        provenance[field] = "package"
        return _PACKAGE_DEFAULTS.get(field)

    values: dict[str, Any] = {field: pick(field) for field in _SCALARS}

    # Name: spec, config, else generated from the submitting user.
    name = pick("name")
    if name is None:
        name = _generate_name(host.user, command)
        provenance["name"] = "host"

    # venv: unset = capture the host's, str = that venv, False/"" = none.
    venv_value = pick("venv")
    venv: str | None
    if venv_value is None:
        venv = host.virtual_env
        provenance["venv"] = "host" if venv else "package"
    else:
        venv = venv_value if isinstance(venv_value, str) and venv_value else None

    # Environment dicts: expand $VAR on the submit host, before the job
    # script's module purge can change anything. Undefined vars pass through.
    def env_dict(field: str) -> Mapping[str, str]:
        provenance[field] = origin.get(field, "package")
        return MappingProxyType(
            {k: _expand_env_vars(str(v), host.env) for k, v in dicts[field].items()}
        )

    def tracked(field: str, pairs: _Tracked) -> tuple[Any, ...]:
        provenance[field] = tuple(pairs)
        return tuple(item for item, _ in pairs)

    # Module lists: $VAR expanded on the submit host; unresolved is an error.
    def module_list(field: str) -> tuple[str, ...]:
        pairs = lists[field]
        expanded = _expand_module_list([item for item, _ in pairs], field, host.env)
        return tracked(field, [(e, layer) for e, (_, layer) in zip(expanded, pairs, strict=True)])

    env_unset = tracked("env_unset", lists["env_unset"])

    resource_pairs = [
        (Resource(item["name"], item["value"]), layer) for item, layer in lists["resources"]
    ]
    resources = ResourceSet(list(tracked("resources", resource_pairs)))

    scheduler_args = MappingProxyType(
        {sched: tuple(arg for arg, _ in pairs) for sched, pairs in sched_args.items()}
    )
    provenance["scheduler_args"] = tuple(
        ((sched, arg), layer) for sched, pairs in sched_args.items() for arg, layer in pairs
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


def _spec_layer(spec: JobSpec) -> dict[str, Any]:
    """The spec as one more layer: every config-settable field it sets."""
    layer: dict[str, Any] = {}
    for field in FIELD_VALIDATORS:
        value = getattr(spec, field)
        if value is None:
            continue
        if field == "resources":
            value = _resource_layer(value)
        layer[field] = value
    return layer


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


def _expand_module_list(
    entries: Sequence[str] | None, key: str, env: Mapping[str, str]
) -> list[str]:
    """Expand ``$VAR`` references in a ``modules``/``modules_path`` list.

    Module names are expanded on the submit host, before the job script's
    ``module purge`` wipes the environment. Unlike ``env_vars``, an entry that
    cannot be resolved is an error: a bare ``module load`` (empty name) or a
    literal ``module load $XCELIUM_MODULE`` in the script is a silent no-op
    that leaves the job running with whatever tools it inherited, which is
    exactly the "works for me" failure this package exists to prevent.

    Raises:
        ConfigError: If an entry references an undefined environment variable
            or expands to an empty string.
    """
    result: list[str] = []
    for raw in entries or []:
        expanded = _expand_env_vars(raw, env)
        unresolved = _UNRESOLVED_VAR.findall(expanded)
        if unresolved:
            names = ", ".join(f"${v}" for v in dict.fromkeys(unresolved))
            raise ConfigError(
                f"{key} entry {raw!r} references undefined environment variable(s) "
                f"{names}; set them in the shell that submits the job"
            )
        if not expanded.strip():
            raise ConfigError(
                f"{key} entry {raw!r} expands to an empty string; the referenced "
                f"environment variable is set but empty in the submitting shell"
            )
        result.append(expanded)
    return result
