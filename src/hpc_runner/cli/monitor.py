"""CLI command for launching the interactive job monitor."""

import rich_click as click

from hpc_runner.cli._context import Context, pass_context


@click.command()
@click.option(
    "--refresh",
    "-r",
    default=10,
    type=int,
    help="Auto-refresh interval in seconds",
)
@pass_context
def monitor(ctx: Context, refresh: int) -> None:
    """Launch interactive job monitor TUI.

    Opens a terminal UI for monitoring HPC jobs across schedulers.
    Shows active and completed jobs with filtering and search.

    \b
    Keyboard shortcuts:
      q      Quit
      r      Manual refresh
      u      Toggle user filter (me/all)
      Tab    Switch tabs
    """
    from hpc_runner.tui import HpcMonitorApp

    app = HpcMonitorApp(ctx.config, refresh_interval=refresh)
    app.run()
