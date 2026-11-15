"""Integration-test fixtures (docs/dev/testbed/TESTBED_PLAN.md section 4.2).

The suite runs inside the testbed's ``login`` container; the stack's
lifecycle belongs to ``hpc-testbed up/down``, not to pytest. The
session fixture only checks readiness and skips (or, with
``--require-cluster``, fails) when no scheduler is reachable.

There are no conditional skips in the tests themselves: the testbed *is*
the environment, so a missing precondition (no Docker socket, the wrong
accounting flush) is a broken testbed and the test fails saying so.
Variants are selected, never detected: ``--slow-accounting`` selects the
tests marked ``slow_accounting`` (and only those); without it they are
deselected.

The stack under test is named, never detected: ``--scheduler`` is a directory
under ``testbed/`` and picks the cluster class (``cluster.CLUSTERS``);
``--flavour`` is a directory under ``testbed/<scheduler>/`` and defaults to
that scheduler's entry in ``DEFAULT_FLAVOURS``. A flavour never selects a
scheduler. A test that drives one scheduler's own CLI or syntax says so with
``@pytest.mark.scheduler("sge")`` (usually as a module ``pytestmark``) and is
deselected on every other scheduler; an unmarked test must hold on all of
them. ``@pytest.mark.flavour("sge/ocs")`` narrows a test to the listed
``<scheduler>/<flavour>`` pairs. A name with no directory fails collection.
"""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from hpc_runner._testbed import DEFAULT_FLAVOURS, SLOW_ACCOUNTING_SCHEDULERS
from hpc_runner._testbed import known_flavours as _known_flavours
from tests.integration.cluster import CLUSTERS, WORK_ROOT, Cluster

HERE = Path(__file__).resolve().parent
TESTBED_DIR = HERE.parent.parent / "testbed"


def known_flavours(testbed_dir: Path = TESTBED_DIR) -> dict[str, list[str]]:
    """``{scheduler: [flavours]}`` for every ``testbed/<scheduler>/<flavour>/compose.yml``."""
    return _known_flavours(testbed_dir)


def select_stack(
    scheduler: str, flavour: str | None, known: dict[str, list[str]]
) -> tuple[str, str]:
    """The ``(scheduler, flavour)`` under test; ``UsageError`` for a name with no directory.

    The scheduler must also have a cluster class in ``CLUSTERS``. No *flavour*
    means the scheduler's entry in ``DEFAULT_FLAVOURS``.
    """
    if scheduler not in known:
        raise pytest.UsageError(
            f"--scheduler {scheduler!r} is not a testbed scheduler; "
            f"known: {', '.join(sorted(known))}"
        )
    if scheduler not in CLUSTERS:
        raise pytest.UsageError(
            f"scheduler {scheduler!r} has no cluster class; add one to CLUSTERS in "
            f"tests/integration/cluster.py (known: {', '.join(sorted(CLUSTERS))})"
        )
    flavours = ", ".join(known[scheduler])
    if flavour is None:
        flavour = DEFAULT_FLAVOURS.get(scheduler)
        if flavour is None:
            raise pytest.UsageError(
                f"scheduler {scheduler!r} has no default flavour; give --flavour ({flavours})"
            )
    if flavour not in known[scheduler]:
        raise pytest.UsageError(
            f"--flavour {flavour!r} is not a {scheduler} flavour; known: {flavours}"
        )
    return scheduler, flavour


def deselect_reason(
    *,
    schedulers: set[str],
    flavours: set[str],
    slow_marked: bool,
    active_scheduler: str,
    active_flavour: str,
    slow: bool,
) -> str | None:
    """Why a test with these marks is not part of this run, or None to run it.

    *flavours* holds ``<scheduler>/<flavour>`` pairs, as the mark is written.
    """
    if schedulers and active_scheduler not in schedulers:
        return f"scheduler {active_scheduler} not in {sorted(schedulers)}"
    active = f"{active_scheduler}/{active_flavour}"
    if flavours and active not in flavours:
        return f"flavour {active} not in {sorted(flavours)}"
    if slow_marked != slow:
        return "slow-accounting variant" if slow_marked else "not the slow-accounting variant"
    return None


def _active(config: pytest.Config) -> tuple[str, str] | None:
    """The stack named on the command line, or None when ``--scheduler`` was not given."""
    scheduler = config.getoption("--scheduler")
    flavour = config.getoption("--flavour")
    if scheduler is None:
        if flavour is not None:
            raise pytest.UsageError("--flavour needs --scheduler: a flavour does not name one")
        return None
    return select_stack(scheduler, flavour, known_flavours())


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("cluster", "live scheduler testbed")
    group.addoption(
        "--scheduler",
        default=None,
        help="the scheduler whose stack is up: a directory under testbed/ (sge, slurm, pbs); "
        "required to run tests/integration",
    )
    group.addoption(
        "--flavour",
        default=None,
        help="which release of that scheduler: a directory under testbed/<scheduler>/ "
        "(default: the scheduler's entry in DEFAULT_FLAVOURS)",
    )
    group.addoption(
        "--require-cluster",
        action="store_true",
        default=False,
        help="fail instead of skip when no scheduler is reachable",
    )
    group.addoption(
        "--slow-accounting",
        action="store_true",
        default=False,
        help="run only the tests marked slow_accounting (the stack from "
        "`hpc-testbed up --slow-accounting`); without it they are deselected",
    )


def pytest_configure(config: pytest.Config) -> None:
    active = _active(config)
    if (
        active is not None
        and config.getoption("--slow-accounting")
        and active[0] not in SLOW_ACCOUNTING_SCHEDULERS
    ):
        raise pytest.UsageError(
            "--slow-accounting is a variant of the "
            f"{', '.join(sorted(SLOW_ACCOUNTING_SCHEDULERS))} stack, not of {active[0]}"
        )


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Mark every item here ``integration``; select the variant; validate the marks.

    A test the active scheduler or flavour cannot run is *deselected* (it is
    not a result of this run), as is the slow-accounting variant not asked
    for. Without ``--scheduler`` (the unit run, where ``-m 'not integration'``
    drops these items anyway) the marks are still validated and nothing is
    deselected here.
    """
    active = _active(config)
    slow = config.getoption("--slow-accounting")
    known = known_flavours()
    known_marks = {
        "scheduler": set(known),
        "flavour": {f"{s}/{f}" for s, fs in known.items() for f in fs},
    }
    kept: list[pytest.Item] = []
    deselected: list[pytest.Item] = []
    for item in items:
        if not item.path.is_relative_to(HERE):
            kept.append(item)
            continue
        item.add_marker(pytest.mark.integration)
        marked: dict[str, set[str]] = {}
        for marker, names_known in known_marks.items():
            names = {name for mark in item.iter_markers(marker) for name in mark.args}
            if names - names_known:
                raise pytest.UsageError(
                    f"{item.nodeid}: @pytest.mark.{marker} names unknown {marker}(s) "
                    f"{', '.join(sorted(names - names_known))}; "
                    f"known: {', '.join(sorted(names_known))}"
                )
            marked[marker] = names
        reason = None
        if active is not None:
            reason = deselect_reason(
                schedulers=marked["scheduler"],
                flavours=marked["flavour"],
                slow_marked=bool(item.get_closest_marker("slow_accounting")),
                active_scheduler=active[0],
                active_flavour=active[1],
                slow=slow,
            )
        (kept if reason is None else deselected).append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = kept


@pytest.fixture(scope="session")
def cluster(request: pytest.FixtureRequest) -> Cluster:
    """The reachable scheduler stack, or a session-wide skip."""
    active = _active(request.config)
    if active is None:
        pytest.exit(
            "tests/integration needs --scheduler <name> (sge, slurm, pbs); "
            "`hpc-testbed --scheduler <name> pytest` passes it",
            returncode=4,
        )
    scheduler, flavour = active
    cluster = CLUSTERS[scheduler](flavour=flavour, workdir=WORK_ROOT)
    ready, output = cluster.poll_ready()
    if not ready:
        reason = f"no {scheduler} cluster reachable ({flavour}): {output.strip() or 'no output'}"
        if request.config.getoption("--require-cluster"):
            pytest.fail(reason, pytrace=False)
        pytest.skip(reason)
    # The stack exports HPC_RUNNER_CONFIG for the flavour it was built from;
    # a mismatched --flavour would test one stack with another's config.
    exported = os.environ.get("HPC_RUNNER_CONFIG")
    if exported and Path(exported) != cluster.config_path:
        pytest.fail(
            f"--scheduler {scheduler} --flavour {flavour} expects {cluster.config_path} "
            f"but the stack exports HPC_RUNNER_CONFIG={exported}",
            pytrace=False,
        )
    return cluster


@pytest.fixture
def workdir(request: pytest.FixtureRequest, cluster: Cluster) -> Path:
    """A fresh directory under /work, writable by alice and bob; also ``cluster.workdir``."""
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", request.node.name)[:60]
    path = WORK_ROOT / f"{stem}-{uuid.uuid4().hex[:8]}"
    path.mkdir()
    path.chmod(0o777)
    cluster.workdir = path
    return path


@pytest.fixture(autouse=True)
def _clean_after(cluster: Cluster, workdir: Path) -> Iterator[None]:
    """Per-test cleanup: delete every job, wait for an empty queue, remove the workdir."""
    yield
    cluster.clean(workdir)
