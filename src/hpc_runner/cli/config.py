"""Config command - manage configuration."""

from pathlib import Path
from typing import Any

import rich_click as click
from rich.console import Console
from rich.syntax import Syntax

from hpc_runner.cli._context import Context, pass_context

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
    from hpc_runner.core.config import HPC_CONFIG_ENV_VAR, load_config

    if ctx.config_path:
        config = load_config(ctx.config_path)
    else:
        config = load_config()

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
        content = "\n".join(lines)

        syntax = Syntax(content, "toml", theme="monokai", line_numbers=True)
        console.print(syntax)


def _format_toml_value(value: Any) -> str:
    """Format a value for TOML output."""
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
    default_config = """# hpc-runner configuration

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
merge_output = true

# Tool-specific configurations
# [tools.python]
# modules = ["python/3.11"]

# Job type configurations
# [types.gpu]
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
