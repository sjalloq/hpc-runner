"""Main CLI entry point using rich-click."""

import sys
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import rich_click as click
from rich.console import Console

from hpc_runner.core.exceptions import ConfigError

# Configure rich-click
click.rich_click.SHOW_ARGUMENTS = True

# Global console for Rich output
console = Console()


# Context object to pass state between commands
class Context:
    def __init__(self) -> None:
        self.config_path: Path | None = None
        self.scheduler: str | None = None
        self.verbose: bool = False


F = TypeVar("F", bound=Callable[..., object])
pass_context: Callable[[F], F] = click.make_pass_decorator(Context, ensure=True)  # type: ignore[assignment]


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--config",
    type=click.Path(exists=True, path_type=Path),
    help="Path to configuration file",
)
@click.option(
    "--scheduler",
    type=str,
    help="Force scheduler (sge, slurm, pbs, local)",
)
@click.option(
    "--verbose",
    is_flag=True,
    help="Enable verbose output",
)
@click.version_option(package_name="hpc-runner")
@pass_context
def cli(ctx: Context, config: Path | None, scheduler: str | None, verbose: bool) -> None:
    """HPC job submission tool.

    Submit and manage jobs across different HPC schedulers (SGE, Slurm, PBS)
    with a unified interface.

    Any unrecognized short options are passed directly to the underlying
    scheduler, allowing use of native flags like -N, -n, -q, etc.
    """
    ctx.config_path = config
    ctx.scheduler = scheduler
    ctx.verbose = verbose

    # Ensure the config cache reflects CLI selection / CWD discovery.
    # Without this, --config would only affect `hpc config ...` commands.
    from hpc_runner.core.config import reload_config

    reload_config(config)


# Import and register subcommands (must be after cli is defined to avoid circular imports)
from hpc_runner.cli.cancel import cancel  # noqa: E402
from hpc_runner.cli.config import config_cmd  # noqa: E402
from hpc_runner.cli.kill import kill  # noqa: E402
from hpc_runner.cli.monitor import monitor  # noqa: E402
from hpc_runner.cli.run import run  # noqa: E402
from hpc_runner.cli.status import status  # noqa: E402

cli.add_command(run)
cli.add_command(status)
cli.add_command(cancel)  # type: ignore[has-type]
cli.add_command(kill)  # type: ignore[has-type]
cli.add_command(config_cmd, name="config")  # type: ignore[has-type]
cli.add_command(monitor)


def main() -> None:
    """Entry point for console script."""
    try:
        cli()
    except ConfigError as e:
        Console(stderr=True).print(f"[red]Config error:[/red] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
