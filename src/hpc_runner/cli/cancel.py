"""Cancel command - cancel jobs by id or by filter."""

from __future__ import annotations

import re

import rich_click as click
from click.exceptions import Exit
from rich.console import Console
from rich.table import Table

from hpc_runner.cli._context import Context, pass_context
from hpc_runner.core.host import current_user
from hpc_runner.core.job_info import JobInfo
from hpc_runner.core.result import JobStatus

console = Console()


@click.command()
@click.argument("job_ids", nargs=-1, metavar="[JOB_ID]...")
@click.option(
    "-N",
    "--name",
    "name_pattern",
    default=None,
    help="Your active jobs whose name matches this regex (e.g. 'tc_.*')",
)
@click.option("--all", "-a", "cancel_all", is_flag=True, help="All your active jobs")
@click.option("--pending", "-p", is_flag=True, help="With -N or --all: only pending jobs")
@click.option("--running", "-r", is_flag=True, help="With -N or --all: only running jobs")
@click.option("--force", is_flag=True, help="Cancel without confirmation")
@pass_context
def cancel(
    ctx: Context,
    job_ids: tuple[str, ...],
    name_pattern: str | None,
    cancel_all: bool,
    pending: bool,
    running: bool,
    force: bool,
) -> None:
    """Cancel jobs, named by id or selected by a filter.

    \b
    Give job ids, or select your own active jobs with -N or --all
    (optionally narrowed with --pending or --running). The jobs are
    listed and confirmed once before anything is cancelled.

    \b
    Examples:
      hpc cancel 1234 1235            Cancel two jobs
      hpc cancel 1234 --force         Cancel without confirmation
      hpc cancel -N 'tc_.*'           Cancel jobs whose name matches tc_.*
      hpc cancel --all --pending      Cancel only your pending jobs
    """
    filtered = name_pattern is not None or cancel_all
    if job_ids and filtered:
        raise click.UsageError("Give job ids, or select jobs with -N/--all, not both.")
    if job_ids and (pending or running):
        raise click.UsageError("--pending and --running apply only with -N or --all.")
    if not job_ids and not filtered:
        raise click.UsageError(
            "No jobs selected. Give job ids, use -N/--name to select jobs by name,\n"
            "or --all to cancel all your active jobs."
        )

    pattern = None
    if name_pattern is not None:
        try:
            pattern = re.compile(name_pattern)
        except re.error as exc:
            raise click.UsageError(f"Invalid regex '{name_pattern}': {exc}") from exc

    from hpc_runner.schedulers import get_scheduler

    scheduler = get_scheduler(config=ctx.config)

    # One row per job to cancel: (id, the scheduler's record of it or None).
    selected: list[tuple[str, JobInfo | None]]
    if job_ids:
        active = {j.job_id: j for j in scheduler.list_active_jobs()}
        selected = [(job_id, active.get(job_id)) for job_id in dict.fromkeys(job_ids)]
    else:
        jobs = scheduler.list_active_jobs(user=current_user())
        if pattern is not None:
            jobs = [j for j in jobs if pattern.search(j.name)]
        if pending or running:
            allowed: set[JobStatus] = set()
            if pending:
                allowed.add(JobStatus.PENDING)
            if running:
                allowed |= {JobStatus.RUNNING, JobStatus.SUSPENDED}
            jobs = [j for j in jobs if j.status in allowed]
        selected = [(j.job_id, j) for j in jobs]

    if not selected:
        console.print("[dim]No active jobs match the given filter.[/dim]")
        return

    table = Table(title="Jobs to cancel")
    table.add_column("Job ID", style="cyan", no_wrap=True)
    table.add_column("Name")
    table.add_column("State")
    table.add_column("Queue")
    table.add_column("Node")
    for job_id, info in selected:
        if info is None:
            table.add_row(job_id, "—", "not active", "—", "—")
        else:
            table.add_row(job_id, info.name, info.status.name, info.queue or "—", info.node or "—")

    console.print(table)
    console.print(f"\n[bold]{len(selected)}[/bold] job(s) will be cancelled.")

    if not force and not click.confirm("Proceed?"):
        console.print("[yellow]Aborted[/yellow]")
        return

    succeeded = 0
    failed = 0
    for job_id, info in selected:
        if scheduler.cancel(job_id):
            succeeded += 1
        else:
            label = f"{job_id} ({info.name})" if info is not None else job_id
            console.print(f"[red]Failed to cancel job {label}[/red]")
            failed += 1

    if succeeded:
        console.print(f"[green]Cancelled {succeeded} job(s)[/green]")
    if failed:
        console.print(f"[red]Failed to cancel {failed} job(s)[/red]")
        raise Exit(1)
