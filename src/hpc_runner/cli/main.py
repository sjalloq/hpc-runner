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
from hpc_runner.cli.monitor import monitor
from hpc_runner.cli.run import run
from hpc_runner.cli.status import status
from hpc_runner.core.exceptions import HPCToolsError, SchedulerCommandError, SubmissionError
from hpc_runner.trace import Invocation

# Configure rich-click
click.rich_click.SHOW_ARGUMENTS = True

# Global console for Rich output
console = Console()

# The group callback stores its Context here so main() can read --verbose
# when reporting an error after click has unwound.
_context: Context | None = None

# The record of this command line, made by main(); the group callback stops
# its output capture before a full-screen command.
_invocation: Invocation | None = None

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
    "--verbose",
    is_flag=True,
    help="Log every scheduler command and show full scheduler errors",
)
@click.option(
    "--trace",
    "trace_dir",
    type=click.Path(path_type=Path),
    metavar="DIR",
    help="Record this command, its config, script, scheduler calls and output as JSON under DIR",
)
@click.version_option(package_name="hpc-runner")
@pass_context
@click.pass_context
def cli(
    click_ctx: click.Context,
    ctx: Context,
    config: Path | None,
    verbose: bool,
    trace_dir: Path | None,
) -> None:
    """HPC job submission tool.

    Submit and manage jobs across different HPC schedulers (SGE, Slurm, PBS)
    with a unified interface. The scheduler is the one named by `scheduler`
    in hpc-runner.toml, or by HPC_SCHEDULER in the environment.
    """
    global _context
    _context = ctx
    ctx.config_path = config
    ctx.verbose = verbose
    _configure_logging(verbose)
    if click_ctx.invoked_subcommand == "monitor":
        # The monitor is never traced: it polls the scheduler for as long as
        # it is open, and a trace is for what one command submitted or read.
        if trace_dir is not None:
            raise click.UsageError("--trace does not apply to 'hpc monitor': it is not traced")
        from hpc_runner.trace import disable

        disable()
        if _invocation is not None:
            # The TUI owns the terminal.
            _invocation.release()
    elif trace_dir is not None:
        # Before the config is read: the flag wins over [trace].
        from hpc_runner.trace import activate

        activate(trace_dir)


cli.add_command(run)
cli.add_command(status)
cli.add_command(cancel)
cli.add_command(config_cmd, name="config")
cli.add_command(monitor)


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

    When a trace is active by the end (``--trace`` or ``[trace]``), the
    command line, the config files read, what was printed, the error and
    the exit code are written to it as one record.
    """
    global _context, _invocation
    _context = None
    _invocation = invocation = Invocation(sys.argv)
    invocation.capture()
    code = 0
    error: BaseException | None = None
    try:
        cli()
    except HPCToolsError as exc:
        _report_error(exc, verbose=_context.verbose if _context is not None else False)
        code, error = 1, exc
    except SystemExit as exc:
        # click exits this way on success and on its own usage errors.
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    except BaseException as exc:
        error = exc
        code = 1
        raise
    finally:
        _invocation = None
        invocation.finish(exit_code=code, error=error, config_files=_config_files())
    sys.exit(code)


def _config_files() -> list[Path]:
    """The config files this command read, in merge order; empty when none was loaded."""
    config = _context._config if _context is not None else None
    return list(config._source_paths) if config is not None else []


if __name__ == "__main__":
    main()
