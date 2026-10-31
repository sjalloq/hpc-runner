"""``hpc-testbed``: drive the Docker Compose scheduler testbed.

A developer tool, not part of the product: it needs a checkout of the
repository (it looks for ``testbed/<family>/<flavour>/compose.yml`` above the
current directory) and the ``docker`` CLI with the Compose plugin. It replaces
the former ``testbed/Makefile`` with the same six operations plus ``capture``
(regenerate the unit fixtures from the live stack): every subcommand is one
or two ``docker compose`` invocations, printed before they run.

The tests themselves still run *inside* the ``login`` container (the product
must call the real ``qsub``); this tool only starts the stack, runs pytest in
it and copies the JUnit report out. See ``testbed/README.md``.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

DEFAULT_FLAVOUR = "soge"
DEFAULT_PY = "3.12"
#: Accounting flush interval of the master (D-T6): the default stack flushes
#: every second; ``--slow-accounting`` uses SGE's stock 15 s.
ACCOUNTING_FLUSH_FAST = "00:00:01"
ACCOUNTING_FLUSH_SLOW = "00:00:15"
#: In-container paths fixed by the image (testbed/sge/soge/Dockerfile).
VENV_ROOT = "/opt/venv"
SRC_DIR = "/src"
WORK_DIR = "/work"
TESTBED_DIR = "/opt/testbed"


class TestbedError(Exception):
    """A usage or environment problem reported on stderr, exit status 2."""


@dataclass(frozen=True)
class Stack:
    """One flavour's compose stack: where its file is and how to address it."""

    root: Path
    family: str
    flavour: str

    @property
    def compose_file(self) -> Path:
        return self.root / "testbed" / self.family / self.flavour / "compose.yml"

    @property
    def project(self) -> str:
        return f"hpc-testbed-{self.flavour}"

    @property
    def junit_dir(self) -> Path:
        return self.root / "testbed"

    def compose(self, *args: str) -> list[str]:
        """``docker compose -f <file> -p <project> <args>``."""
        return ["docker", "compose", "-f", str(self.compose_file), "-p", self.project, *args]


def find_root(start: Path) -> Path:
    """The repository checkout containing *start*: the directory holding ``testbed/``."""
    for candidate in (start, *start.parents):
        if (candidate / "testbed").is_dir() and (candidate / "pyproject.toml").is_file():
            return candidate
    raise TestbedError(
        f"no hpc-runner checkout above {start}: hpc-testbed needs the repository's testbed/ "
        "directory (run it from inside the checkout)"
    )


def find_stack(root: Path, flavour: str) -> Stack:
    """Locate the flavour's compose file under ``testbed/<family>/<flavour>/``."""
    matches = sorted((root / "testbed").glob(f"*/{flavour}/compose.yml"))
    if not matches:
        known = sorted(p.parent.name for p in (root / "testbed").glob("*/*/compose.yml"))
        raise TestbedError(f"unknown flavour {flavour!r}; known flavours: {', '.join(known)}")
    if len(matches) > 1:
        raise TestbedError(f"flavour {flavour!r} exists in more than one family: {matches}")
    return Stack(root=root, family=matches[0].parent.parent.name, flavour=flavour)


def venv_bin(py: str) -> str:
    return f"{VENV_ROOT}/{py}/bin"


# -----------------------------------------------------------------------------
# Running commands: the only place a subprocess is started; tests patch it.
# -----------------------------------------------------------------------------


def run(argv: Sequence[str], *, env: dict[str, str] | None = None, check: bool = True) -> int:
    """Run *argv* with inherited stdio after echoing it, return its exit status."""
    print("+ " + shlex.join(argv), file=sys.stderr, flush=True)
    full_env = {**os.environ, **env} if env else None
    status = subprocess.call(list(argv), env=full_env)
    if check and status != 0:
        raise SystemExit(status)
    return status


# -----------------------------------------------------------------------------
# Subcommands
# -----------------------------------------------------------------------------


def cmd_up(stack: Stack, *, slow_accounting: bool, ready_timeout: int, build: bool) -> None:
    """Build the image, start the four services, wait for ``ready.sh``."""
    env = {"ACCOUNTING_FLUSH": ACCOUNTING_FLUSH_SLOW if slow_accounting else ACCOUNTING_FLUSH_FAST}
    if build:
        run(stack.compose("build"), env=env)
    run(stack.compose("up", "-d", "--wait"), env=env)
    run(
        stack.compose(
            "exec",
            "-T",
            "login",
            f"{TESTBED_DIR}/common/wait-for.sh",
            str(ready_timeout),
            "3",
            "--",
            f"{TESTBED_DIR}/ready.sh",
        ),
        env=env,
    )


def cmd_down(stack: Stack) -> None:
    """Remove containers, network and volumes."""
    run(stack.compose("down", "-v", "--remove-orphans"))


def cmd_shell(stack: Stack, *, py: str) -> None:
    """An interactive shell in ``login`` as ``tester`` with the venv on PATH."""
    path = f"{venv_bin(py)}:/usr/local/bin:/usr/bin:/bin"
    run(
        stack.compose("exec", "-u", "tester", "-w", SRC_DIR, "-e", f"PATH={path}", "login", "bash"),
        check=False,
    )


def cmd_logs(stack: Stack, *, services: Sequence[str]) -> None:
    run(stack.compose("logs", "--no-color", *services), check=False)


def cmd_clean(stack: Stack) -> None:
    """Delete every job and wait for an empty queue (``clean.sh``)."""
    run(stack.compose("exec", "-T", "login", f"{TESTBED_DIR}/clean.sh"))


def pytest_argv(
    stack: Stack, *, py: str, flavour: str, slow_accounting: bool, extra: Sequence[str]
) -> list[str]:
    """The ``docker compose exec`` command that runs the suite inside ``login``.

    ``-m integration`` overrides pyproject's ``-m 'not integration'`` addopts
    and ``--require-cluster`` makes an unreachable scheduler a failure: inside
    the testbed a missing precondition is a broken testbed, never a skip.
    ``--slow-accounting`` selects the tests marked ``slow_accounting`` instead
    of the default set.
    """
    return stack.compose(
        "exec",
        "-T",
        "-u",
        "tester",
        "-w",
        SRC_DIR,
        "login",
        f"{venv_bin(py)}/pytest",
        "tests/integration",
        "-m",
        "integration",
        "--require-cluster",
        "--cluster",
        stack.family,
        "--flavour",
        flavour,
        f"--junitxml={WORK_DIR}/junit-{py}.xml",
        *(["--slow-accounting"] if slow_accounting else []),
        *extra,
    )


def cmd_pytest(
    stack: Stack, *, py: str, junit: Path, slow_accounting: bool, extra: Sequence[str]
) -> int:
    """Run ``tests/integration`` inside ``login``; copy the JUnit XML to *junit*."""
    argv = pytest_argv(
        stack, py=py, flavour=stack.flavour, slow_accounting=slow_accounting, extra=extra
    )
    status = run(argv, check=False)
    junit.parent.mkdir(parents=True, exist_ok=True)
    run(stack.compose("cp", f"login:{WORK_DIR}/junit-{py}.xml", str(junit)), check=False)
    return status


FIXTURES_DIR = "tests/unit/fixtures"
CAPTURE_DIR = f"{WORK_DIR}/capture"


def cmd_capture(stack: Stack, *, py: str) -> None:
    """Regenerate the unit-test fixtures from the live stack.

    Runs ``tests/integration/capture.py`` inside ``login`` (it submits the
    scenario jobs, kills a node and stops the master) and copies its output
    to ``tests/unit/fixtures/<family>/``.
    """
    run(
        stack.compose(
            "exec",
            "-T",
            "-u",
            "tester",
            "-w",
            SRC_DIR,
            "login",
            f"{venv_bin(py)}/python",
            "-m",
            "tests.integration.capture",
            CAPTURE_DIR,
            stack.flavour,
        )
    )
    dest = stack.root / FIXTURES_DIR / stack.family
    dest.mkdir(parents=True, exist_ok=True)
    run(stack.compose("cp", f"login:{CAPTURE_DIR}/.", str(dest)))


# -----------------------------------------------------------------------------
# Command line
# -----------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hpc-testbed",
        description=(
            "Drive the Docker Compose scheduler testbed (testbed/README.md). Every "
            "subcommand prints the docker compose commands it runs."
        ),
    )
    parser.add_argument(
        "--flavour",
        default=DEFAULT_FLAVOUR,
        help=f"scheduler implementation under testbed/<family>/ (default {DEFAULT_FLAVOUR})",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    up = sub.add_parser("up", help="build the image, start the stack, wait for ready.sh")
    _add_slow_accounting(up)
    up.add_argument("--no-build", action="store_true", help="start without rebuilding the image")
    up.add_argument(
        "--ready-timeout", type=int, default=90, help="seconds to wait for ready.sh (default 90)"
    )

    sub.add_parser("down", help="remove containers, network and volumes")

    shell = sub.add_parser("shell", help="bash in login as tester with the venv on PATH")
    _add_py(shell)

    logs = sub.add_parser("logs", help="container logs")
    logs.add_argument("services", nargs="*", help="services to show (default: all)")

    sub.add_parser("clean", help="delete every job and wait for an empty queue")

    capture = sub.add_parser(
        "capture",
        help="regenerate tests/unit/fixtures/<family>/ from the live stack",
    )
    _add_py(capture)

    test = sub.add_parser(
        "pytest",
        help="run tests/integration inside login; arguments after -- go to pytest",
        epilog=(
            "The JUnit report is written to testbed/junit-<py>.xml unless --junit is given. "
            "Example: hpc-testbed pytest --py 3.11 -- -k submit -x"
        ),
    )
    _add_py(test)
    test.add_argument("--junit", type=Path, help="host path for the JUnit XML report")
    test.add_argument(
        "--up", action="store_true", help="bring the stack up first and take it down after"
    )
    test.add_argument("--keep", action="store_true", help="with --up: leave the stack running")
    _add_slow_accounting(
        test,
        "run only the slow_accounting tests (with --up: start that stack too)",
    )
    test.add_argument(
        "--ready-timeout", type=int, default=90, help="with --up: seconds to wait for ready.sh"
    )
    test.add_argument("pytest_args", nargs=argparse.REMAINDER, help="passed to pytest after --")
    return parser


def _add_py(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--py", default=DEFAULT_PY, help=f"Python venv in login to use (default {DEFAULT_PY})"
    )


def _add_slow_accounting(
    parser: argparse.ArgumentParser,
    help_text: str = "start the master with the 15 s accounting flush (D-T6)",
) -> None:
    parser.add_argument("--slow-accounting", action="store_true", help=help_text)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        root = find_root(Path.cwd())
        stack = find_stack(root, args.flavour)
        return dispatch(stack, args)
    except TestbedError as exc:
        print(f"hpc-testbed: {exc}", file=sys.stderr)
        return 2


def dispatch(stack: Stack, args: argparse.Namespace) -> int:
    if args.command == "up":
        cmd_up(
            stack,
            slow_accounting=args.slow_accounting,
            ready_timeout=args.ready_timeout,
            build=not args.no_build,
        )
    elif args.command == "down":
        cmd_down(stack)
    elif args.command == "shell":
        cmd_shell(stack, py=args.py)
    elif args.command == "logs":
        cmd_logs(stack, services=args.services)
    elif args.command == "clean":
        cmd_clean(stack)
    elif args.command == "capture":
        cmd_capture(stack, py=args.py)
    elif args.command == "pytest":
        extra = list(args.pytest_args)
        if extra and extra[0] == "--":
            extra = extra[1:]
        junit = args.junit or stack.junit_dir / f"junit-{args.py}.xml"
        if args.up:
            cmd_up(
                stack,
                slow_accounting=args.slow_accounting,
                ready_timeout=args.ready_timeout,
                build=True,
            )
        try:
            return cmd_pytest(
                stack,
                py=args.py,
                junit=junit,
                slow_accounting=args.slow_accounting,
                extra=extra,
            )
        finally:
            if args.up and not args.keep:
                cmd_down(stack)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
