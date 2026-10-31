"""``hpc trace``: replay and export a scheduler trace (docs/dev/trace/DESIGN.md)."""

from __future__ import annotations

import json
from pathlib import Path

import rich_click as click
from rich.console import Console
from rich.table import Table

console = Console()


@click.group()
def trace_cmd() -> None:
    """Inspect a scheduler trace recorded with ``hpc --trace DIR`` or ``[trace]``."""


@trace_cmd.command("compare")
@click.argument("directory", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option(
    "--fixtures",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="A captured fixture root (tests/unit/fixtures/sge); list trace shapes it does not cover",
)
@click.option("--json", "use_json", is_flag=True, help="Output as JSON")
def compare(directory: Path, fixtures: Path | None, use_json: bool) -> None:
    """Run every recorded scheduler output back through the parsers.

    Lists each argv shape in the trace with how many calls it had and how
    many the parsers could not make sense of, then every such finding. Exits
    1 when there is a finding or, with --fixtures, an uncovered shape.
    """
    from hpc_runner.trace.compare import compare as run_compare

    report = run_compare(directory, fixtures)
    failed = bool(report.findings or report.uncovered)

    if use_json:
        console.print_json(json.dumps(report.as_dict()))
        raise SystemExit(1 if failed else 0)

    if not report.records:
        console.print(f"[dim]No trace records under {directory}.[/dim]")
        return

    table = Table(title=f"Trace {directory} ({len(report.records)} calls)")
    table.add_column("Shape", style="cyan")
    table.add_column("Calls", justify="right")
    table.add_column("Findings", justify="right")
    if report.covered is not None:
        table.add_column("Fixture")
    per_shape = {s: sum(1 for f in report.findings if f.record.shape == s) for s in report.shapes}
    for s, n in sorted(report.shapes.items()):
        bad = per_shape[s]
        row = [s, str(n), f"[red]{bad}[/red]" if bad else "[green]0[/green]"]
        if report.covered is not None:
            row.append("[green]yes[/green]" if s in report.covered else "[yellow]no[/yellow]")
        table.add_row(*row)
    console.print(table)

    if report.findings:
        console.print(f"\n[red]{len(report.findings)} finding(s):[/red]")
        for f in report.findings:
            console.print(f"  {f.record.path}: {f.message}")
    if report.uncovered:
        console.print(
            f"\n[yellow]{len(report.uncovered)} shape(s) with no captured fixture:[/yellow] "
            + ", ".join(report.uncovered)
        )
    if failed:
        raise SystemExit(1)
    console.print("\n[green]Every record parsed.[/green]")
