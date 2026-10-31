"""Config command - manage configuration."""

import sys
from pathlib import Path
from typing import Any

import rich_click as click
from rich.console import Console
from rich.markup import escape
from rich.syntax import Syntax
from rich.table import Table

from hpc_runner.cli._context import Context, pass_context
from hpc_runner.core.exceptions import ConfigError
from hpc_runner.core.merge import Edit, Set

console = Console()


@click.group()
def config_cmd() -> None:
    """Manage configuration."""
    pass


@config_cmd.command("show")
@click.option("--raw", is_flag=True, help="Show raw file contents instead of merged")
@pass_context
def show(ctx: Context, raw: bool) -> None:
    """Show current configuration."""
    from hpc_runner.core.config import HPC_CONFIG_ENV_VAR

    config = ctx.config

    if not config._source_paths:
        console.print("[yellow]No configuration file found[/yellow]")
        console.print("\nSearched locations:")
        env_or_git = f"${HPC_CONFIG_ENV_VAR} env var (or <git-root>/hpc-runner.toml)"
        console.print(f"  - {env_or_git}")
        console.print("  - ./hpc-runner.toml")
        return

    if raw:
        # Show each config file's raw contents
        for config_path in config._source_paths:
            console.print(f"\n[bold]Config file:[/bold] {config_path}")
            console.print()
            content = config_path.read_text()
            syntax = Syntax(content, "toml", theme="monokai", line_numbers=True)
            console.print(syntax)
    else:
        # Show merged config
        if len(config._source_paths) > 1:
            console.print("[bold]Merged from:[/bold]")
            for i, p in enumerate(config._source_paths, 1):
                console.print(f"  {i}. {p}")
            console.print()

        console.print("[bold]Merged configuration:[/bold]\n")

        lines = []
        if config.defaults:
            lines.append("[defaults]")
            for k, v in config.defaults.items():
                lines.append(f"{k} = {_format_toml_value(v)}")
            lines.append("")
        if config.schedulers:
            for sched, sched_config in config.schedulers.items():
                lines.append(f"[schedulers.{sched}]")
                for k, v in sched_config.items():
                    lines.append(f"{k} = {_format_toml_value(v)}")
                lines.append("")
        if config.tools:
            for tool, tool_config in config.tools.items():
                lines.append(f"[tools.{tool}]")
                for k, v in tool_config.items():
                    lines.append(f"{k} = {_format_toml_value(v)}")
                lines.append("")
        if config.types:
            for typ, type_config in config.types.items():
                lines.append(f"[types.{typ}]")
                for k, v in type_config.items():
                    lines.append(f"{k} = {_format_toml_value(v)}")
                lines.append("")
        if config.trace:
            lines.append("[trace]")
            for k, v in config.trace.items():
                lines.append(f"{k} = {_format_toml_value(v)}")
            lines.append("")
        content = "\n".join(lines)

        syntax = Syntax(content, "toml", theme="monokai", line_numbers=True)
        console.print(syntax)


def _format_toml_value(value: Any) -> str:
    """Format a value for TOML output."""
    if isinstance(value, Set):
        return f"{{ set = {_format_toml_value(list(value.items))} }}"
    if isinstance(value, Edit):
        parts = []
        if value.remove:
            parts.append(f"remove = {_format_toml_value(list(value.remove))}")
        if value.add:
            parts.append(f"add = {_format_toml_value(list(value.add))}")
        return f"{{ {', '.join(parts)} }}"
    if isinstance(value, bool):
        return "true" if value else "false"
    elif isinstance(value, str):
        return f'"{value}"'
    elif isinstance(value, list):
        items = ", ".join(_format_toml_value(v) for v in value)
        return f"[{items}]"
    elif isinstance(value, dict):
        items = ", ".join(f"{k} = {_format_toml_value(v)}" for k, v in value.items())
        return f"{{ {items} }}"
    else:
        return str(value)


@config_cmd.command("init")
@pass_context
def init(ctx: Context) -> None:
    """Create a new configuration file in the current directory."""
    config_path = Path.cwd() / "hpc-runner.toml"

    if config_path.exists():
        if not click.confirm(f"{config_path} already exists. Overwrite?"):
            console.print("[yellow]Cancelled[/yellow]")
            return

    # Write default config
    default_config = """config_version = 1

# hpc-runner configuration

[defaults]
# Default job settings
cpu = 1
mem = "4G"
time = "1:00:00"
# queue = "batch"

# Modules to always load
modules = []

[schedulers.sge]
# SGE-specific settings
parallel_environment = "smp"
memory_resource = "mem_free"
time_resource = "h_rt"

# Tool-specific configurations, matched on the command's first word
# [tools.python]
# modules = ["python/3.11"]

# One entry can stand for several commands
# [tools.xcelium]
# commands = ["xrun", "xmvlog", "xmelab"]
# modules = ["xcelium/23.09"]

# Job type configurations: a type pulls in tools, then adds its own keys
# [types.gpu]
# tools = ["python"]
# queue = "gpu"
# resources = [{name = "gpu", value = 1}]
"""

    config_path.write_text(default_config)
    console.print(f"[green]Created {config_path}[/green]")


@config_cmd.command("path")
@click.option("--all", "show_all", is_flag=True, help="Show all configs in merge chain")
@pass_context
def path(ctx: Context, show_all: bool) -> None:
    """Show path to active configuration file(s)."""
    from hpc_runner.core.config import HPC_CONFIG_ENV_VAR, _resolve_extends, find_config_files

    if ctx.config_path:
        # Explicit config - show its extends chain
        config_files = _resolve_extends(ctx.config_path)
    else:
        config_files = find_config_files()

    if not config_files:
        console.print("[yellow]No configuration file found[/yellow]")
        console.print("\nSearched locations:")
        env_or_git = f"${HPC_CONFIG_ENV_VAR} env var (or <git-root>/hpc-runner.toml)"
        console.print(f"  - {env_or_git}")
        console.print("  - ./hpc-runner.toml")
        return

    if show_all or len(config_files) > 1:
        console.print("[bold]Config merge chain[/bold] (first = lowest priority):\n")
        for i, p in enumerate(config_files, 1):
            console.print(f"  {i}. {p}")
    else:
        console.print(str(config_files[0]))


@config_cmd.command("check")
@click.argument("path", required=False, type=click.Path(path_type=Path))
@pass_context
def check(ctx: Context, path: Path | None) -> None:
    """Validate configuration and report every problem.

    Checks PATH (with its extends chain), else --config, else the
    discovered files. Exits 1 if anything is wrong.
    """
    from hpc_runner.core.config import check_config

    target = path if path is not None else ctx.config_path
    try:
        config, errors = check_config(target)
    except ConfigError as exc:
        config, errors = None, exc.errors

    if config is not None:
        for warning in config.warnings:
            click.echo(f"warning: {warning}", err=True)
    for error in errors:
        click.echo(f"error: {error}", err=True)
    if errors:
        sys.exit(1)
    assert config is not None
    count = len(config._source_paths)
    click.echo(f"ok: {count} file{'s' if count != 1 else ''}")


@config_cmd.command(
    "resolve",
    context_settings={"ignore_unknown_options": True, "allow_interspersed_args": False},
)
@click.option("-t", "--type", "job_type", help="Job type from config")
@click.argument("command", nargs=-1, required=True, type=click.UNPROCESSED)
@pass_context
def resolve_cmd(ctx: Context, job_type: str | None, command: tuple[str, ...]) -> None:
    """Show the job COMMAND would get, and which layer set each value.

    Builds the job from the command (and -t TYPE) alone, the way
    ``hpc run`` would before any of its other options apply.
    """
    import dataclasses

    from hpc_runner.core.host import Host
    from hpc_runner.core.job import Job
    from hpc_runner.core.resolve import resolve
    from hpc_runner.core.spec import JobSpec

    job = resolve(JobSpec(list(command), type=job_type), ctx.config, Host.capture())

    table = Table("field", "value", "source")
    for f in dataclasses.fields(Job):
        if f.name == "provenance":
            continue
        source = job.provenance[f.name]
        if isinstance(source, tuple):
            if not source:
                table.add_row(f.name, "", "")
            for item, layer in source:
                if f.name == "scheduler_args":
                    sched, arg = item
                    table.add_row(f"scheduler_args.{sched}", escape(arg), layer)
                else:
                    table.add_row(f.name, escape(_format_item(item)), layer)
            continue
        table.add_row(f.name, escape(_format_item(getattr(job, f.name))), source)
    console.print(table)


def _format_item(value: Any) -> str:
    from hpc_runner.core.resources import Resource

    if value is None:
        return ""
    if isinstance(value, Resource):
        return f"{value.name}={value.value}"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, tuple):
        return " ".join(str(v) for v in value)
    if hasattr(value, "items"):
        return ", ".join(f"{k}={v}" for k, v in value.items())
    return str(value)
