"""Jinja2 template engine for job scripts."""

import shlex
from pathlib import Path
from typing import Any

import jinja2


class ShellWord(str):
    """Text that is already one shell word, quoted as needed.

    The ``sh`` filter passes it through unchanged. Schedulers build these
    for values containing ``${hpc.*}``, whose rendering must stay a live
    shell expansion inside otherwise quoted text.
    """


def sh(value: Any) -> str:
    """Jinja filter: *value* as a single shell word (``shlex.quote``).

    Every value a template interpolates goes through this, except the job
    command, which is deliberately raw shell.
    """
    if isinstance(value, ShellWord):
        return str(value)
    return shlex.quote(str(value))


# Template directories - schedulers and package templates
_SCHEDULERS_DIR = Path(__file__).parent.parent / "schedulers"
_PACKAGE_DIR = Path(__file__).parent

_env: jinja2.Environment | None = None


def _get_env() -> jinja2.Environment:
    """Get or create the Jinja2 environment."""
    global _env
    if _env is None:
        _env = jinja2.Environment(
            loader=jinja2.FileSystemLoader([str(_SCHEDULERS_DIR), str(_PACKAGE_DIR)]),
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )
        _env.filters["sh"] = sh
    return _env


def render_template(name: str, **context: Any) -> str:
    """Render a template.

    Args:
        name: Template name (e.g., "sge/templates/job.sh.j2")
        **context: Template context variables

    Returns:
        Rendered template content
    """
    env = _get_env()
    template = env.get_template(name)
    return template.render(**context)


def render_string(template_str: str, **context: Any) -> str:
    """Render a template string.

    Args:
        template_str: Template content as a string
        **context: Template context variables

    Returns:
        Rendered content
    """
    env = _get_env()
    template = env.from_string(template_str)
    return template.render(**context)
