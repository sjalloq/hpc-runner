"""Cancel command - cancel running jobs."""

import rich_click as click
from rich.console import Console

from hpc_runner.cli._context import Context, pass_context

console = Console()


@click.command()
@click.argument("job_id")
@click.option("--force", is_flag=True, help="Cancel without confirmation")
@pass_context
def cancel(
    ctx: Context,
    job_id: str,
    force: bool,
) -> None:
    """Cancel a job.

    JOB_ID is the job ID to cancel.
    """
    from hpc_runner.schedulers import get_scheduler

    scheduler = get_scheduler(ctx.scheduler, config=ctx.config)

    if not force:
        if not click.confirm(f"Cancel job {job_id}?"):
            console.print("[yellow]Cancelled[/yellow]")
            return

    success = scheduler.cancel(job_id)

    if success:
        console.print(f"[green]Job {job_id} cancelled[/green]")
    else:
        console.print(f"[red]Failed to cancel job {job_id}[/red]")
