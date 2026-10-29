"""Run command - submit jobs to the scheduler."""

from typing import TYPE_CHECKING

import rich_click as click
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.syntax import Syntax

from hpc_runner.cli._context import Context, pass_context
from hpc_runner.core.result import ArrayJobResult, JobStatus

if TYPE_CHECKING:
    from hpc_runner.core.dependency import Dependency
    from hpc_runner.core.job import Job
    from hpc_runner.schedulers.base import BaseScheduler

console = Console()


def _parse_args(args: tuple[str, ...]) -> tuple[list[str], list[str]]:
    """Split args on '--' into (scheduler_passthrough, command).

    If '--' is absent, all args are treated as the command.
    """
    args_list = list(args)
    if "--" in args_list:
        idx = args_list.index("--")
        return args_list[:idx], args_list[idx + 1 :]
    return [], args_list


@click.command(
    context_settings={
        "ignore_unknown_options": True,
        "allow_interspersed_args": False,
    }
)
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
# Short-option namespace rule: any short flag click does not recognise is
# forwarded verbatim to the scheduler (see _parse_args and the `--`
# separator). A short flag defined here therefore *shadows* the scheduler's
# flag of the same letter. Only claim one when hpc-runner already owns that
# meaning: -t shadows qsub/sbatch -t (array / time), both covered by
# --array and --time; -b shadows qsub -b (binary), irrelevant because jobs
# always run via a generated script.
@click.option("--name", "job_name", help="Job name")
@click.option("--cpu", type=int, help="Number of CPUs")
@click.option("--mem", help="Memory for the whole job (e.g., 16G, 1.5GiB)")
@click.option("--time", "time_limit", help="Time limit (e.g., 4:00:00, 4h, 1h30m)")
@click.option("--queue", help="Queue/partition name")
@click.option("--nodes", type=int, help="Number of nodes (MPI jobs)")
@click.option("--ntasks", type=int, help="Number of tasks (MPI jobs)")
@click.option("--directory", type=click.Path(exists=True), help="Working directory")
@click.option("-t", "--type", "job_type", help="Job type from config")
@click.option("--stderr", help="Separate stderr file pattern (default: merged into stdout)")
@click.option(
    "--stdout", "stdout", help="Stdout file pattern (default: ${hpc.job_name}.${hpc.job_id}.out)"
)
@click.option("--array", help="Array job specification (e.g., 1-100)")
@click.option(
    "--env",
    "env",
    multiple=True,
    metavar="KEY=VALUE",
    help="Set an environment variable in the job (repeatable)",
)
@click.option(
    "--resource",
    "resource",
    multiple=True,
    metavar="NAME=VALUE",
    help="Request a scheduler resource or licence, e.g. gpu=2 (repeatable)",
)
@click.option(
    "--depend",
    multiple=True,
    help="Job dependency, repeatable: ID (afterok) or TYPE:ID[:ID...][,TYPE:ID...] "
    "(e.g. afterok:123:124,afterany:125)",
)
@click.option(
    "-b",
    "--batch",
    is_flag=True,
    help="Submit as a batch job and return immediately (default: run interactively)",
)
@click.option("--dry-run", "dry_run", is_flag=True, help="Show what would be submitted")
@click.option("--wait", is_flag=True, help="With --batch: block until the job completes")
@click.option("--keep-script", "keep_script", is_flag=True, help="Keep job script for debugging")
@pass_context
def run(
    ctx: Context,
    args: tuple[str, ...],
    job_name: str | None,
    cpu: int | None,
    mem: str | None,
    time_limit: str | None,
    queue: str | None,
    nodes: int | None,
    ntasks: int | None,
    directory: str | None,
    job_type: str | None,
    stderr: str | None,
    stdout: str | None,
    array: str | None,
    env: tuple[str, ...],
    resource: tuple[str, ...],
    depend: tuple[str, ...],
    batch: bool,
    dry_run: bool,
    wait: bool,
    keep_script: bool,
) -> None:
    """Run a command on the cluster.

    By default the job runs interactively (qrsh/srun): the command's output
    streams to this terminal and hpc waits for it to finish. Use --batch
    to submit and return immediately.

    COMMAND is the command to execute, including any flags it needs:

    \b
        hpc run xterm
        hpc run -t xcelium make sim
        hpc run --batch --cpu 4 make -j4 sim

    Use ``--`` to pass raw scheduler arguments before the command:

    \b
        hpc run -q batch.q -l gpu=2 -- python train.py
        hpc run --cpu 4 -q batch.q -- mpirun -N 4 ./sim

    Without ``--``, everything after hpc-runner options is the command.
    """
    from hpc_runner.core.array import ArrayRange
    from hpc_runner.core.host import Host
    from hpc_runner.core.resolve import resolve
    from hpc_runner.core.spec import JobSpec
    from hpc_runner.schedulers import get_scheduler

    # Split on '--': everything before is scheduler passthrough,
    # everything after is the command.  No '--' means all command.
    passthrough, command_parts = _parse_args(args)

    # Click forwards every option it does not know (ignore_unknown_options), so
    # a mistyped hpc-runner option would otherwise reach the scheduler, or be
    # submitted as the start of the command. Long options are always ours;
    # single-dash flags belong to the scheduler and must precede `--`.
    unknown_long = [a for a in passthrough if a.startswith("--")]
    if unknown_long:
        raise click.UsageError(
            f"No such option: {unknown_long[0]} (long options are hpc-runner's own; "
            "scheduler flags are single-dash)"
        )
    if not command_parts:
        raise click.UsageError("Command is required")
    if command_parts[0].startswith("-"):
        raise click.UsageError(
            f"No such option: {command_parts[0]}. Scheduler flags must come before '--', "
            "e.g. hpc run -q batch.q -- make sim"
        )
    if array is not None:
        try:
            ArrayRange.parse(array)
        except ValueError as exc:
            raise click.BadParameter(str(exc), param_hint="--array") from exc
    if wait and not batch and array is None:
        raise click.UsageError(
            "--wait needs --batch: an interactive run already waits for the command"
        )
    dependencies = _parse_depend(depend)
    env_vars = dict(_parse_pairs(env, "--env", "KEY=VALUE"))
    resources = [
        {"name": n, "value": v} for n, v in _parse_pairs(resource, "--resource", "NAME=VALUE")
    ]

    scheduler = get_scheduler(ctx.scheduler, config=ctx.config)

    # Array jobs can only be batch; everything else is interactive unless
    # --batch was given.
    spec = JobSpec(
        command_parts,  # joined with shlex.join, preserving quoting
        type=job_type,
        name=job_name,
        cpu=cpu,
        mem=mem,
        time=time_limit,
        queue=queue,
        nodes=nodes,
        tasks=ntasks,
        workdir=directory,
        stderr=stderr,
        stdout=stdout,
        array=array,
        env_vars=env_vars or None,
        resources=resources or None,
        dependencies=dependencies,
        scheduler_args={scheduler.name: passthrough} if passthrough else None,
        interactive=not batch and not array,
    )
    job = resolve(spec, ctx.config, Host.capture())

    if dry_run:
        _show_dry_run(job, scheduler, verbose=ctx.verbose)
        return

    # Submit the job
    result = scheduler.submit(job, keep_script=keep_script, verbose=ctx.verbose)

    if job.interactive:
        if result.exit_code == 0:
            console.print("[green]Job completed successfully[/green]")
        else:
            console.print(f"[red]Job failed with exit code: {result.exit_code}[/red]")
        return

    if isinstance(result, ArrayJobResult):
        console.print(f"Submitted array job [bold cyan]{result.job_id}[/bold cyan]")
        console.print(f"  Tasks: {result.array.count} ({result.array.range_str})")
    else:
        console.print(f"Submitted job [bold cyan]{result.job_id}[/bold cyan]")

    if ctx.verbose:
        console.print(f"  Scheduler: {scheduler.name}")
        console.print(f"  Job name: {job.name}")
        console.print(f"  Command: {job.command}")
        if passthrough:
            console.print(f"  Passthrough args: {' '.join(passthrough)}")

    if wait:
        console.print("[dim]Waiting for job completion...[/dim]")
        if isinstance(result, ArrayJobResult):
            task_statuses = result.wait()
            failed = sum(1 for st in task_statuses.values() if st is not JobStatus.COMPLETED)
            console.print(f"Array job completed: {len(task_statuses) - failed} ok, {failed} not ok")
        else:
            final_status = result.wait()
            console.print(f"Job completed with status: [bold]{final_status.name}[/bold]")


def _parse_pairs(values: tuple[str, ...], option: str, form: str) -> list[tuple[str, str]]:
    """Split each ``NAME=VALUE`` in *values* at the first ``=``.

    Raises:
        click.BadParameter: A value has no ``=`` or an empty name.
    """
    pairs: list[tuple[str, str]] = []
    for text in values:
        name, sep, value = text.partition("=")
        if not sep or not name:
            raise click.BadParameter(f"expected {form}, got {text!r}", param_hint=option)
        pairs.append((name, value))
    return pairs


def _parse_depend(values: tuple[str, ...]) -> list["Dependency"]:
    """Parse every ``--depend`` value into one Dependency list.

    Raises:
        click.UsageError: A value is not in the grammar of
            :func:`~hpc_runner.core.dependency.parse_dependency_string`.
    """
    from hpc_runner.core.dependency import parse_dependency_string

    dependencies: list[Dependency] = []
    for text in values:
        try:
            dependencies.extend(parse_dependency_string(text))
        except ValueError as exc:
            raise click.UsageError(f"Invalid --depend: {exc}") from None
    return dependencies


def _show_dry_run(job: "Job", scheduler: "BaseScheduler", *, verbose: bool = False) -> None:
    """Display what would be submitted."""
    script = scheduler.preview(job, verbose=verbose)  # raises before printing if unsupported
    mode = "interactive" if job.interactive else "batch"
    lines = [
        f"[bold]Scheduler:[/bold] {scheduler.name}",
        f"[bold]Mode:[/bold] {mode}",
        f"[bold]Job name:[/bold] {job.name}",
        f"[bold]Command:[/bold] {job.command}",
        f"[bold]venv:[/bold] {escape(job.venv) if job.venv else 'none'}",
    ]
    if job.mem is not None:
        lines.append(f"[bold]mem:[/bold] {job.mem}")
    if job.time is not None:
        lines.append(f"[bold]time:[/bold] {job.time}")
    if job.array is not None:
        lines.append(f"[bold]Array job:[/bold] {job.array.range_str} ({job.array.count} tasks)")
    passthrough = job.scheduler_args.get(scheduler.name, ())
    if passthrough:
        lines.append(f"[bold]Scheduler passthrough:[/bold] {' '.join(passthrough)}")
    console.print(
        Panel.fit(
            "\n".join(lines),
            title="Dry Run",
            border_style="blue",
        )
    )

    console.print("\n[bold]Generated script:[/bold]")
    syntax = Syntax(script, "bash", theme="monokai", line_numbers=True)
    console.print(syntax)
