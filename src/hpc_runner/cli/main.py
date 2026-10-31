"""Main CLI entry point using rich-click."""

import logging
import shlex
import sys
from pathlib import Path

import rich_click as click
from rich.console import Console

from hpc_runner.cli._context import Context, pass_context
from hpc_runner.cli.cancel import cancel
from hpc_runner.cli.config import config_cmd
from hpc_runner.cli.kill import kill
from hpc_runner.cli.monitor import monitor
from hpc_runner.cli.run import run
from hpc_runner.cli.status import status
from hpc_runner.cli.trace import trace_cmd
from hpc_runner.core.exceptions import HPCToolsError, SchedulerCommandError, SubmissionError

# Configure rich-click
click.rich_click.SHOW_ARGUMENTS = True

# Global console for Rich output
console = Console()

# The group callback stores its Context here so main() can read --verbose
# when reporting an error after click has unwound.
_context: Context | None = None

# Handler installed by --verbose; kept so a repeat invocation in the same
# process (tests) replaces it rather than stacking another.
_log_handler: logging.Handler | None = None


def _configure_logging(verbose: bool) -> None:
    """Send hpc_runner DEBUG logs (every scheduler argv) to stderr under --verbose.

    The package never configures logging itself; only the CLI does.
    """
    global _log_handler
    logger = logging.getLogger("hpc_runner")
    if _log_handler is not None:
        logger.removeHandler(_log_handler)
        _log_handler = None
    if not verbose:
        return
    _log_handler = logging.StreamHandler(sys.stderr)
    _log_handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
    logger.addHandler(_log_handler)
    logger.setLevel(logging.DEBUG)


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
    help="Log every scheduler command and show full scheduler errors",
)
@click.option(
    "--trace",
    "trace_dir",
    type=click.Path(path_type=Path),
    metavar="DIR",
    help="Record every scheduler call (argv, exit, stdout, stderr) as JSON under DIR",
)
@click.version_option(package_name="hpc-runner")
@pass_context
def cli(
    ctx: Context, config: Path | None, scheduler: str | None, verbose: bool, trace_dir: Path | None
) -> None:
    """HPC job submission tool.

    Submit and manage jobs across different HPC schedulers (SGE, Slurm, PBS)
    with a unified interface.

    Any unrecognized short options are passed directly to the underlying
    scheduler, allowing use of native flags like -N, -n, -q, etc.
    """
    global _context
    _context = ctx
    ctx.config_path = config
    ctx.scheduler = scheduler
    ctx.verbose = verbose
    _configure_logging(verbose)
    if trace_dir is not None:
        # Before the config is read: the flag wins over [trace] (D-TR2).
        from hpc_runner.trace import activate

        activate(trace_dir)


cli.add_command(run)
cli.add_command(status)
cli.add_command(cancel)
cli.add_command(kill)
cli.add_command(config_cmd, name="config")
cli.add_command(monitor)
cli.add_command(trace_cmd, name="trace")


def _report_error(exc: HPCToolsError, verbose: bool) -> None:
    """Print *exc* as one line; under --verbose add the argv and full stderr."""
    click.echo(f"error: {exc}", err=True)
    if not verbose or not isinstance(exc, SchedulerCommandError | SubmissionError):
        return
    if exc.argv:
        click.echo(f"command: {shlex.join(exc.argv)}", err=True)
    if exc.stderr and exc.stderr.strip():
        click.echo("stderr:", err=True)
        click.echo(exc.stderr.rstrip("\n"), err=True)


def main() -> None:
    """Entry point for console script.

    Every deliberate error is an ``HPCToolsError``: print one line and exit
    1.  A traceback escaping here means a bug.
    """
    global _context
    _context = None
    try:
        cli()
    except HPCToolsError as exc:
        _report_error(exc, verbose=_context.verbose if _context is not None else False)
        sys.exit(1)


if __name__ == "__main__":
    main()
