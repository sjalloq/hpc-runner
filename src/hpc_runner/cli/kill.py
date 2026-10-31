"""Kill command - cancel jobs matching filters."""

from __future__ import annotations

import re

import rich_click as click
from rich.console import Console
from rich.table import Table

from hpc_runner.cli._context import Context, pass_context
from hpc_runner.core.host import current_user

console = Console()


@click.command()
@click.option(
    "-N",
    "--name",
    "name_pattern",
    default=None,
    help="Job name pattern (regex, e.g. 'tc_.*')",
)
@click.option("--all", "-a", "kill_all", is_flag=True, help="Kill all your active jobs")
@click.option("--pending", "-p", is_flag=True, help="Only kill pending jobs")
@click.option("--running", "-r", is_flag=True, help="Only kill running jobs")
@click.option("--force", is_flag=True, help="Kill without confirmation")
@pass_context
def kill(
    ctx: Context,
    name_pattern: str | None,
    kill_all: bool,
    pending: bool,
    running: bool,
    force: bool,
) -> None:
    """Kill running jobs matching a filter.

    \b
    Requires -N or --all to avoid accidentally killing jobs.
    Optionally narrow by state with --pending or --running.

    \b
    Examples:
      hpc kill -N 'tc_.*'           Kill jobs whose name matches tc_.*
      hpc kill -N sim --force        Kill matching jobs without confirmation
      hpc kill --all                 Kill all your active jobs
      hpc kill --all --pending       Kill only your pending jobs
      hpc kill -N 'tc_.*' --running  Kill only running jobs matching tc_.*
    """
    if name_pattern is None and not kill_all:
        raise click.UsageError(
            "No filter specified. Use -N/--name to select jobs by name,\n"
            "or --all to kill all your active jobs."
        )

    pattern = None
    if name_pattern is not None:
        try:
            pattern = re.compile(name_pattern)
        except re.error as exc:
            raise click.UsageError(f"Invalid regex '{name_pattern}': {exc}") from exc

    from hpc_runner.schedulers import get_scheduler

    scheduler = get_scheduler(ctx.scheduler, config=ctx.config)

    user = current_user()
    jobs = scheduler.list_active_jobs(user=user)

    if pattern is not None:
        matched = [j for j in jobs if pattern.search(j.name)]
    else:
        matched = jobs

    # Filter by state if requested
    if pending or running:
        from hpc_runner.core.result import JobStatus

        allowed = set()
        if pending:
            allowed.add(JobStatus.PENDING)
        if running:
            allowed.add(JobStatus.RUNNING)
        matched = [j for j in matched if j.status in allowed]

    if not matched:
        console.print("[dim]No active jobs match the given filter.[/dim]")
        return

    # Show what will be killed
    table = Table(title="Jobs to kill")
    table.add_column("Job ID", style="cyan", no_wrap=True)
    table.add_column("Name")
    table.add_column("State")
    table.add_column("Queue")
    table.add_column("Node")

    for j in matched:
        table.add_row(
            j.job_id,
            j.name,
            j.status.name,
            j.queue or "—",
            j.node or "—",
        )

    console.print(table)
    console.print(f"\n[bold]{len(matched)}[/bold] job(s) will be killed.")

    if not force:
        if not click.confirm("Proceed?"):
            console.print("[yellow]Aborted[/yellow]")
            return

    # Cancel each matched job
    succeeded = 0
    failed = 0
    for j in matched:
        if scheduler.cancel(j.job_id):
            succeeded += 1
        else:
            console.print(f"[red]Failed to cancel job {j.job_id} ({j.name})[/red]")
            failed += 1

    if succeeded:
        console.print(f"[green]Killed {succeeded} job(s)[/green]")
    if failed:
        console.print(f"[red]Failed to kill {failed} job(s)[/red]")
        raise SystemExit(1)
