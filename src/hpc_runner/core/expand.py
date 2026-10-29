"""The one variable expansion rule for config values (D5).

*Every ``$VAR`` in config is resolved where you submit from; runtime values
come from ``${hpc.*}``; anything else is shell in your script or Makefile.*

- ``$$`` is a literal ``$``.
- ``${hpc.<name>}`` names a value only the scheduler knows at run time
  (:data:`HPC_VARS`). It is kept verbatim here and rendered by the
  scheduler (SGE turns ``${hpc.cpus}`` into ``$NSLOTS``).
- Every other ``$VAR`` / ``${VAR}`` is resolved on the submit host: first
  from the resolver variables (``CONFIG_DIR``, ``GIT_ROOT``, ``CWD``,
  ``USER``), then from the submitting environment. Undefined is an error.

``${CONFIG_DIR}`` is the directory of the config file that set the value.
It is bound when that file is loaded (:func:`bind_config_dir`), because
which file set a value is lost once files are merged.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from hpc_runner.core.exceptions import ConfigError

#: The portable runtime names: ``${hpc.<name>}``.
HPC_VARS: tuple[str, ...] = ("cpus", "job_id", "task_id", "tmpdir", "job_name", "host")

#: Scheduler runtime variables, and the ``${hpc.*}`` name that replaces each.
_RUNTIME_HINTS: dict[str, str] = {
    "NSLOTS": "cpus",
    "JOB_ID": "job_id",
    "SGE_TASK_ID": "task_id",
    "TASK_ID": "task_id",
    "TMPDIR": "tmpdir",
    "JOB_NAME": "job_name",
    "HOSTNAME": "host",
    "SLURM_CPUS_PER_TASK": "cpus",
    "SLURM_JOB_ID": "job_id",
    "SLURM_ARRAY_TASK_ID": "task_id",
    "SLURM_JOB_NAME": "job_name",
    "SLURMD_NODENAME": "host",
    "PBS_JOBID": "job_id",
    "PBS_ARRAYID": "task_id",
    "PBS_JOBNAME": "job_name",
    "NCPUS": "cpus",
}

# One token: `$$`, `${...}`, or `$NAME`. A `$` followed by anything else is
# left as written.
# A `$` that starts no variable (`$1`, `$(cmd)`, a trailing `$`) is kept
# literally; it reaches the script quoted, so it stays a literal `$`.
_TOKEN = re.compile(r"\$(?:(\$)|\{([^}]*)\}|([A-Za-z_][A-Za-z0-9_]*))")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

CONFIG_DIR = "CONFIG_DIR"


def expand(value: str, *, host_env: Mapping[str, str], extra: Mapping[str, str], where: str) -> str:
    """Expand *value* per D5.

    Args:
        value: The string as written in config or the spec.
        host_env: The submitting environment.
        extra: Resolver variables, consulted before *host_env*.
        where: Where *value* came from, for error messages.

    Returns:
        *value* with ``$$`` turned into ``$``, every variable resolved and
        ``${hpc.*}`` references kept as written.

    Raises:
        ConfigError: A variable is undefined, ``${hpc.x}`` names an unknown
            runtime value, or a ``${...}`` form is not a plain name.
    """

    def replace(match: re.Match[str]) -> str:
        dollar, braced, bare = match.groups()
        if dollar:
            return "$"
        name = bare if bare is not None else braced
        if braced is not None and braced.startswith("hpc."):
            hpc_name = braced[4:]
            if hpc_name not in HPC_VARS:
                valid = ", ".join(f"${{hpc.{n}}}" for n in HPC_VARS)
                raise ConfigError(f"{where}: unknown ${{{braced}}}; valid names are {valid}")
            return match.group(0)
        if not _NAME.fullmatch(name):
            raise ConfigError(
                f"{where}: unsupported ${{{name}}}; only ${{NAME}} is expanded "
                "(write $$ for a literal $)"
            )
        if name in extra:
            return extra[name]
        if name in host_env:
            return host_env[name]
        if name == CONFIG_DIR:
            raise ConfigError(
                f"{where}: ${{CONFIG_DIR}} is only defined for values set in a config file"
            )
        hint = _RUNTIME_HINTS.get(name)
        suffix = f"; did you mean ${{hpc.{hint}}}?" if hint else ""
        raise ConfigError(f"{where}: undefined variable ${name}{suffix}")

    # One left-to-right pass: substituted text is never rescanned, so
    # `$$HOME` stays literal and an expanded value containing `$` is inert.
    return _TOKEN.sub(replace, value)


def _escape(text: str) -> str:
    return text.replace("$", "$$")


def bind_config_dir(value: str, config_dir: str) -> str:
    """Replace ``$CONFIG_DIR`` / ``${CONFIG_DIR}`` in *value* with *config_dir*.

    Applied when a config file is loaded, so a value keeps the directory
    of the file that set it through merging. ``$$`` escapes are kept, and
    a ``$`` in *config_dir* is escaped, so :func:`expand` later treats the
    directory as literal text.
    """

    def replace(match: re.Match[str]) -> str:
        dollar, braced, bare = match.groups()
        if not dollar and (braced or bare) == CONFIG_DIR:
            return _escape(config_dir)
        return match.group(0)

    return _TOKEN.sub(replace, value)
