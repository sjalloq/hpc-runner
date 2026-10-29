"""Status command - check job status."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

import rich_click as click
from rich.console import Console
from rich.table import Table

from hpc_runner.cli._context import Context, pass_context
from hpc_runner.core.exceptions import JobNotFoundError

if TYPE_CHECKING:
    from hpc_runner.core.job_info import JobInfo
    from hpc_runner.schedulers.base import BaseScheduler

console = Console()


def _status_style(status: str) -> str:
    """Apply Rich color markup to a status string."""
    colors = {
        "PENDING": "[yellow]PENDING[/yellow]",
        "RUNNING": "[blue]RUNNING[/blue]",
        "COMPLETED": "[green]COMPLETED[/green]",
        "FAILED": "[red]FAILED[/red]",
        "CANCELLED": "[magenta]CANCELLED[/magenta]",
        "TIMEOUT": "[red]TIMEOUT[/red]",
        "SKIPPED": "[magenta]SKIPPED[/magenta]",
        "UNKNOWN": "[dim]UNKNOWN[/dim]",
    }
    return colors.get(status, status)


def _get_current_user() -> str:
    """Return the current username."""
    return os.environ.get("USER", os.environ.get("USERNAME", "unknown"))


def _format_datetime(dt: object) -> str:
    """Format a datetime for display, or return '—' if None."""
    from datetime import datetime

    if isinstance(dt, datetime):
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    return "—"


# --------------------------------------------------------------------- #
# Command
# --------------------------------------------------------------------- #


@click.command()
@click.argument("job_id", required=False)
@click.option("--history", "-H", is_flag=True, help="Show recently completed jobs (via accounting)")
@click.option(
    "--since",
    "-s",
    "since_value",
    default=None,
    help="Time window for --history (e.g. 30m, 2h, 1d)",
)
@click.option("--all", "-a", "all_users", is_flag=True, help="Show all users' jobs")
@click.option("--json", "-j", "use_json", is_flag=True, help="Output as JSON")
@click.option("--verbose", "-v", "verbose", is_flag=True, help="Show extra columns/fields")
@click.option("--watch", is_flag=True, help="Watch mode (refresh periodically)")
@pass_context
def status(
    ctx: Context,
    job_id: str | None,
    history: bool,
    since_value: str | None,
    all_users: bool,
    use_json: bool,
    verbose: bool,
    watch: bool,
) -> None:
    """Check job status.

    \b
    Three modes of operation:
      hpc status            List your active jobs
      hpc status --history  List recently completed jobs
      hpc status <JOB_ID>   Show details for a single job
    """
    # ---- Validation ------------------------------------------------- #
    if job_id and history:
        raise click.UsageError("Cannot combine JOB_ID with --history.")
    if since_value:
        history = True
    if watch:
        console.print("[yellow]--watch is not yet implemented.[/yellow]")
        return

    from hpc_runner.schedulers import get_scheduler

    scheduler = get_scheduler(ctx.scheduler, config=ctx.config)

    if job_id:
        _show_single_job(scheduler, job_id, verbose=verbose, use_json=use_json)
    elif history:
        _show_history(
            scheduler,
            since_value=since_value,
            all_users=all_users,
            verbose=verbose,
            use_json=use_json,
        )
    else:
        _show_active_jobs(
            scheduler,
            all_users=all_users,
            verbose=verbose,
            use_json=use_json,
        )


# --------------------------------------------------------------------- #
# Mode 1: Active jobs
# --------------------------------------------------------------------- #


def _show_active_jobs(
    scheduler: BaseScheduler,
    *,
    all_users: bool,
    verbose: bool,
    use_json: bool,
) -> None:
    """List active (running/pending) jobs."""
    user = None if all_users else _get_current_user()
    jobs = scheduler.list_active_jobs(user=user)

    if use_json:
        console.print_json(json.dumps([_job_info_to_dict(j) for j in jobs]))
        return

    if not jobs:
        console.print("[dim]No active jobs.[/dim]")
        return

    if verbose:
        _enrich_working_dirs(scheduler, jobs)

    table = Table(title="Active Jobs")
    table.add_column("Job ID", style="cyan", no_wrap=True)
    table.add_column("Name")
    table.add_column("State")
    table.add_column("Started")
    table.add_column("Queue")
    table.add_column("Node")
    table.add_column("CPU")
    table.add_column("Runtime")
    if verbose:
        table.add_column("Work Dir")

    for j in jobs:
        row: list[str] = [
            j.job_id,
            j.name,
            _status_style(j.status.name),
            _format_datetime(j.start_time),
            j.queue or "—",
            j.node or "—",
            str(j.cpu) if j.cpu is not None else "—",
            j.runtime_display,
        ]
        if verbose:
            row.append(str(j.working_dir) if j.working_dir else "—")
        table.add_row(*row)

    console.print(table)


# --------------------------------------------------------------------- #
# Mode 2: History
# --------------------------------------------------------------------- #


def _show_history(
    scheduler: BaseScheduler,
    *,
    since_value: str | None,
    all_users: bool,
    verbose: bool,
    use_json: bool,
) -> None:
    """List recently completed jobs from accounting."""
    from hpc_runner.core.exceptions import AccountingNotAvailable
    from hpc_runner.core.timeutil import parse_since

    if not scheduler.has_accounting():
        console.print("[red]Job accounting is not available for this scheduler.[/red]")
        raise SystemExit(1)

    since_dt = parse_since(since_value or "30m")
    user = None if all_users else _get_current_user()

    try:
        jobs = scheduler.list_completed_jobs(user=user, since=since_dt)
    except AccountingNotAvailable as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)

    if use_json:
        console.print_json(json.dumps([_job_info_to_dict(j) for j in jobs]))
        return

    if not jobs:
        console.print("[dim]No completed jobs found in the given time window.[/dim]")
        return

    table = Table(title="Completed Jobs")
    table.add_column("Job ID", style="cyan", no_wrap=True)
    table.add_column("Name")
    table.add_column("State")
    table.add_column("Exit", justify="right")
    table.add_column("Ended")
    table.add_column("Queue")
    table.add_column("Node")
    table.add_column("CPU")
    table.add_column("Runtime")

    for j in jobs:
        exit_str = str(j.exit_code) if j.exit_code is not None else "—"
        if j.exit_code == 0:
            exit_str = f"[green]{exit_str}[/green]"
        elif j.exit_code is not None:
            exit_str = f"[red]{exit_str}[/red]"

        row: list[str] = [
            j.job_id,
            j.name,
            _status_style(j.status.name),
            exit_str,
            _format_datetime(j.end_time),
            j.queue or "—",
            j.node or "—",
            str(j.cpu) if j.cpu is not None else "—",
            j.runtime_display,
        ]
        table.add_row(*row)

    console.print(table)


# --------------------------------------------------------------------- #
# Mode 3: Single job
# --------------------------------------------------------------------- #


def _show_single_job(
    scheduler: BaseScheduler,
    job_id: str,
    *,
    verbose: bool,
    use_json: bool,
) -> None:
    """Show detailed information for one job."""
    extra: dict[str, object] = {}
    job_info = None

    # Try the rich get_job_details() first
    try:
        job_info, extra = scheduler.get_job_details(job_id)
    except (NotImplementedError, JobNotFoundError):
        pass

    # Fall back to get_status() + get_exit_code()
    if job_info is None:
        from hpc_runner.core.job_info import JobInfo

        status_val = scheduler.get_status(job_id)
        exit_code = scheduler.get_exit_code(job_id)
        job_info = JobInfo(
            job_id=job_id,
            name=job_id,
            user=_get_current_user(),
            status=status_val,
            exit_code=exit_code,
        )

    if use_json:
        data = _job_info_to_dict(job_info)
        if extra:
            data["details"] = {k: _serialize(v) for k, v in extra.items()}
        console.print_json(json.dumps(data))
        return

    table = Table(title=f"Job {job_id}", show_header=False)
    table.add_column("Property", style="cyan")
    table.add_column("Value")

    table.add_row("Job ID", job_info.job_id)
    table.add_row("Name", job_info.name)
    table.add_row("User", job_info.user)
    table.add_row("Status", _status_style(job_info.status.name))

    if job_info.exit_code is not None:
        table.add_row("Exit Code", str(job_info.exit_code))
    if job_info.queue:
        table.add_row("Queue", job_info.queue)
    if job_info.node:
        table.add_row("Node", job_info.node)
    if job_info.cpu is not None:
        table.add_row("CPU", str(job_info.cpu))
    if job_info.memory:
        table.add_row("Memory", job_info.memory)
    if job_info.submit_time:
        table.add_row("Submitted", _format_datetime(job_info.submit_time))
    if job_info.start_time:
        table.add_row("Started", _format_datetime(job_info.start_time))
    if job_info.end_time:
        table.add_row("Ended", _format_datetime(job_info.end_time))
    if job_info.runtime:
        table.add_row("Runtime", job_info.runtime_display)
    if job_info.working_dir:
        table.add_row("Work Dir", str(job_info.working_dir))
    if job_info.stdout_path:
        table.add_row("Stdout", str(job_info.stdout_path))
    if job_info.stderr_path:
        table.add_row("Stderr", str(job_info.stderr_path))

    if verbose and extra:
        table.add_row("", "")  # visual separator
        for key, val in extra.items():
            table.add_row(key, str(val))

    console.print(table)


# --------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------- #


def _enrich_working_dirs(scheduler: BaseScheduler, jobs: list[JobInfo]) -> None:
    """Fill in working_dir by querying per-job details for jobs that lack it."""
    for j in jobs:
        if j.working_dir:
            continue
        try:
            detail, _ = scheduler.get_job_details(j.job_id)
            if detail.working_dir:
                j.working_dir = detail.working_dir
        except (NotImplementedError, JobNotFoundError):
            pass


def _job_info_to_dict(j: JobInfo) -> dict[str, object]:
    """Convert a JobInfo to a JSON-serializable dict."""
    return {
        "job_id": j.job_id,
        "name": j.name,
        "user": j.user,
        "status": j.status.name,
        "queue": j.queue,
        "node": j.node,
        "cpu": j.cpu,
        "memory": j.memory,
        "exit_code": j.exit_code,
        "submit_time": _format_datetime(j.submit_time) if j.submit_time else None,
        "start_time": _format_datetime(j.start_time) if j.start_time else None,
        "end_time": _format_datetime(j.end_time) if j.end_time else None,
        "runtime": j.runtime_display if j.runtime else None,
        "working_dir": str(j.working_dir) if j.working_dir else None,
    }


def _serialize(value: object) -> object:
    """Best-effort JSON serialization for extra detail values."""
    from pathlib import Path

    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, dict, str, int, float, bool, type(None))):
        return value
    return str(value)
