"""resolve(): the one place a JobSpec is merged with config into a Job."""

from __future__ import annotations

import re
import shlex
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any

from hpc_runner.core.array import ArrayRange
from hpc_runner.core.config import HPCConfig, _expand_env_vars, _merge
from hpc_runner.core.exceptions import ConfigError, NotSupportedError
from hpc_runner.core.host import Host
from hpc_runner.core.job import Job
from hpc_runner.core.resources import ResourceSet
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

# Scalar fields: spec, else config, else package default (else None).
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


def resolve(spec: JobSpec, config: HPCConfig, host: Host) -> Job:
    """Resolve *spec* against *config* and the submitting *host*.

    For every field the spec's value wins if set, then the config layer
    (``[types.<spec.type>]`` if a type is named, else ``[tools.<argv0>]``
    and its matching option, each over ``[defaults]``), then the package
    default. The returned :class:`Job` records which layer supplied each
    value in ``provenance``.

    Raises:
        ConfigError: A module entry references an undefined or empty
            environment variable.
        NotSupportedError: An array job was requested interactively.
        ValueError: ``array`` is not a valid range.
    """
    command = spec.command if isinstance(spec.command, str) else shlex.join(spec.command)

    if spec.type:
        # task 3 (D2/D11) makes this an error: an unknown type name silently
        # falls back to [defaults] only.
        layers = config.type_layers(spec.type)
    else:
        layers = config.tool_layers(command)

    # Merge exactly as config does, then attribute each key to the most
    # specific layer that set it.
    merged: dict[str, Any] = {}
    origin: dict[str, str] = {}
    for layer_name, table in layers:
        merged = _merge(merged, table)
        for key in table:
            origin[key] = layer_name
    # Command always comes from the caller, never from config.
    merged.pop("command", None)

    provenance: dict[str, str] = {"command": "spec"}

    def pick(field: str) -> Any:
        value = getattr(spec, field)
        if value is not None:
            provenance[field] = "spec"
            return value
        if merged.get(field) is not None:
            provenance[field] = origin[field]
            return merged[field]
        provenance[field] = "package"
        return _PACKAGE_DEFAULTS.get(field)

    values: dict[str, Any] = {field: pick(field) for field in _SCALARS}
    provenance["type"] = "spec"

    # Name: spec, config, else generated from the submitting user.
    name = pick("name")
    if name is None:
        name = _generate_name(host.user, command)
        provenance["name"] = "host"

    # venv: None = capture the host's, str = that venv, False/"" = none.
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
        raw = pick(field) or {}
        return MappingProxyType({k: _expand_env_vars(str(v), host.env) for k, v in raw.items()})

    env_unset = tuple(pick("env_unset") or ())

    # Module lists: a spec list replaces the config list outright.
    # task 3 (D1) replaces this with the append rule.
    def module_list(field: str) -> tuple[str, ...]:
        return tuple(_expand_module_list(pick(field), field, host.env))

    # Resources: spec wins outright; config list-of-dicts -> ResourceSet.
    resources = _to_resource_set(pick("resources"))

    # Scheduler passthrough: spec only (config support is task 3).
    scheduler_args = MappingProxyType(
        {sched: tuple(args) for sched, args in (spec.scheduler_args or {}).items()}
    )
    provenance["scheduler_args"] = "spec" if spec.scheduler_args else "package"

    # Dependencies come from the spec only: job ids exist only at run time,
    # so a config layer has nothing meaningful to say about them.
    dependencies = tuple(spec.dependencies)
    provenance["dependencies"] = "spec" if spec.dependencies else "package"

    array_value = pick("array")
    array = ArrayRange.parse(str(array_value)) if array_value is not None else None

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


def _generate_name(user: str, command: str) -> str:
    """``<user>_<first command word>``, skipping ``VAR=value`` prefixes."""
    for part in command.split():
        if "=" not in part:
            return f"{user}_{part.split('/')[-1]}"
    return f"{user}_job"


def _to_resource_set(value: Any) -> ResourceSet:
    if value is None:
        return ResourceSet()
    if isinstance(value, ResourceSet):
        return ResourceSet(list(value.resources))
    resources = ResourceSet()
    for entry in value:
        resources.add(entry["name"], entry["value"])
    return resources


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
