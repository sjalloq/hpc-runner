"""Standalone submit command — config-driven daily driver with short options.

Unlike ``hpc run`` (which supports scheduler passthrough and long options),
``submit`` is a closed interface that rejects unknown flags.  All common
options have single-letter shortcuts for quick interactive use.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import rich_click as click
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

from hpc_runner.core.exceptions import ConfigError

if TYPE_CHECKING:
    from hpc_runner.core.job import Job
    from hpc_runner.schedulers.base import BaseScheduler

console = Console()


@click.command(
    context_settings={
        "help_option_names": ["-h", "--help"],
        "ignore_unknown_options": False,
        "allow_interspersed_args": False,
    },
)
@click.argument("args", nargs=-1, required=True, type=click.UNPROCESSED)
@click.option("-t", "--type", "job_type", default=None, help="Job type from config")
@click.option("-n", "--cpu", type=int, default=None, help="Number of CPUs")
@click.option("-m", "--mem", default=None, help="Memory (e.g. 16G)")
@click.option("-T", "--time", "time_limit", default=None, help="Time limit (e.g. 4:00:00)")
@click.option("-I", "--interactive", is_flag=True, help="Run interactively")
@click.option("-q", "--queue", default=None, help="Queue/partition")
@click.option("-N", "--name", "job_name", default=None, help="Job name")
@click.option("-w", "--wait", is_flag=True, help="Wait for completion")
@click.option("-a", "--array", default=None, help="Array job spec (e.g. 1-100%5)")
@click.option(
    "-M", "--extra-module", "extra_modules", multiple=True, help="Extra module to append to config"
)
@click.option(
    "-P",
    "--extra-module-path",
    "extra_module_path",
    multiple=True,
    help="Extra module path to append to config",
)
@click.option("-e", "--env", multiple=True, help="Env var KEY=VAL (repeatable)")
@click.option("-d", "--depend", default=None, help="Job dependency")
@click.option("-v", "--verbose", is_flag=True, help="Verbose output")
@click.option("--dry-run", is_flag=True, help="Show what would be submitted")
def submit(
    args: tuple[str, ...],
    job_type: str | None,
    cpu: int | None,
    mem: str | None,
    time_limit: str | None,
    interactive: bool,
    queue: str | None,
    job_name: str | None,
    wait: bool,
    array: str | None,
    extra_modules: tuple[str, ...],
    extra_module_path: tuple[str, ...],
    env: tuple[str, ...],
    depend: str | None,
    verbose: bool,
    dry_run: bool,
) -> None:
    """Submit a job to the scheduler (config-driven, short options).

    \b
    COMMAND is the command to execute, including any flags it needs:
        submit echo hello
        submit -t gpu python train.py
        submit -n 4 -m 16G make sim

    For full control (scheduler passthrough, etc.) use ``hpc run``.
    """
    import shlex

    from hpc_runner.core.job import Job
    from hpc_runner.schedulers import get_scheduler

    cmd_str = shlex.join(args)

    # Auto-detect scheduler
    scheduler = get_scheduler()

    # Create job — Job() auto-consults TOML config hierarchy
    job = Job(
        command=cmd_str,
        job_type=job_type,
        name=job_name,
        cpu=cpu,
        mem=mem,
        time=time_limit,
        queue=queue,
        dependency=depend,
        extra_modules=list(extra_modules) if extra_modules else None,
        extra_modules_path=list(extra_module_path) if extra_module_path else None,
    )

    # Parse -e KEY=VAL entries
    for entry in env:
        if "=" not in entry:
            raise click.BadParameter(
                f"Expected KEY=VAL format, got: {entry!r}",
                param_hint="'-e'",
            )
        key, _, val = entry.partition("=")
        job.env_vars[key] = val

    # Handle array jobs
    if array:
        _handle_array_job(job, array, scheduler, dry_run, verbose)
        return

    if dry_run:
        _show_dry_run(job, scheduler, interactive=interactive)
        return

    # Submit the job
    result = scheduler.submit(job, interactive=interactive)

    if interactive:
        if result.returncode == 0:
            console.print("[green]Job completed successfully[/green]")
        else:
            console.print(f"[red]Job failed with exit code: {result.returncode}[/red]")
    else:
        console.print(f"Submitted job [bold cyan]{result.job_id}[/bold cyan]")

        if verbose:
            console.print(f"  Scheduler: {scheduler.name}")
            console.print(f"  Job name: {job.name}")
            console.print(f"  Command: {job.command}")

        if wait:
            console.print("[dim]Waiting for job completion...[/dim]")
            final_status = result.wait()
            console.print(f"Job completed with status: [bold]{final_status.name}[/bold]")


def _show_dry_run(
    job: Job,
    scheduler: BaseScheduler,
    interactive: bool = False,
) -> None:
    """Display what would be submitted."""
    mode = "interactive" if interactive else "batch"
    console.print(
        Panel.fit(
            f"[bold]Scheduler:[/bold] {scheduler.name}\n"
            f"[bold]Mode:[/bold] {mode}\n"
            f"[bold]Job name:[/bold] {job.name}\n"
            f"[bold]Command:[/bold] {job.command}",
            title="Dry Run",
            border_style="blue",
        )
    )

    console.print("\n[bold]Generated script:[/bold]")
    if interactive:
        script = scheduler.generate_interactive_script(job, "/tmp/example_script.sh")
    else:
        script = scheduler.generate_script(job)
    syntax = Syntax(script, "bash", theme="monokai", line_numbers=True)
    console.print(syntax)


def _handle_array_job(
    job: Job,
    array_spec: str,
    scheduler: BaseScheduler,
    dry_run: bool,
    verbose: bool,
) -> None:
    """Handle array job submission."""
    from hpc_runner.core.job_array import JobArray

    parts = array_spec.replace("%", ":").split(":")
    range_parts = parts[0].split("-")

    start = int(range_parts[0])
    end = int(range_parts[1]) if len(range_parts) > 1 else start
    step = int(parts[1]) if len(parts) > 1 else 1
    max_concurrent = int(parts[2]) if len(parts) > 2 else None

    array_job = JobArray(
        job=job,
        start=start,
        end=end,
        step=step,
        max_concurrent=max_concurrent,
    )

    if dry_run:
        console.print(f"[bold]Array job:[/bold] {array_job.range_str} ({array_job.count} tasks)")
        _show_dry_run(job, scheduler)
        return

    result = array_job.submit(scheduler)
    console.print(f"Submitted array job [bold cyan]{result.base_job_id}[/bold cyan]")
    console.print(f"  Tasks: {array_job.count} ({array_job.range_str})")


def main() -> None:
    """Console script entry point for ``submit``."""
    try:
        submit()
    except ConfigError as e:
        Console(stderr=True).print(f"[red]Config error:[/red] {e}")
        sys.exit(1)
