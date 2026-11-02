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
deselected. Flavour names, on ``--flavour`` and in ``@pytest.mark.flavour``,
must name a directory under ``testbed/<family>/`` or collection fails.

The family is never an option: it is the directory the flavour lives in,
and it picks the cluster class (``cluster.CLUSTERS``). A test that drives
one family's own CLI or syntax says so with ``@pytest.mark.family("sge")``
(usually as a module ``pytestmark``) and is deselected on every other
family; an unmarked test must hold on all of them (D-T48).
"""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from hpc_runner._testbed import SLOW_ACCOUNTING_FAMILIES
from tests.integration.cluster import CLUSTERS, WORK_ROOT, Cluster

HERE = Path(__file__).resolve().parent
TESTBED_DIR = HERE.parent.parent / "testbed"


def known_flavours(testbed_dir: Path = TESTBED_DIR) -> dict[str, str]:
    """``{flavour: family}`` for every ``testbed/<family>/<flavour>/compose.yml``."""
    return {p.parent.name: p.parent.parent.name for p in testbed_dir.glob("*/*/compose.yml")}


def family_of(flavour: str, flavours: dict[str, str]) -> str:
    """The family whose directory holds *flavour*; ``UsageError`` for an unknown one.

    The family must also have a cluster class in ``CLUSTERS``, and
    ``--slow-accounting`` must be a variant that family has.
    """
    if flavour not in flavours:
        raise pytest.UsageError(
            f"--flavour {flavour!r} is not a testbed flavour; known: {', '.join(sorted(flavours))}"
        )
    family = flavours[flavour]
    if family not in CLUSTERS:
        raise pytest.UsageError(
            f"flavour {flavour!r} is in family {family!r}, which has no cluster class; "
            f"add one to CLUSTERS in tests/integration/cluster.py (known: "
            f"{', '.join(sorted(CLUSTERS))})"
        )
    return family


def deselect_reason(
    *,
    flavours: set[str],
    families: set[str],
    slow_marked: bool,
    active_flavour: str,
    active_family: str,
    slow: bool,
) -> str | None:
    """Why a test with these marks is not part of this run, or None to run it."""
    if families and active_family not in families:
        return f"family {active_family} not in {sorted(families)}"
    if flavours and active_flavour not in flavours:
        return f"flavour {active_flavour} not in {sorted(flavours)}"
    if slow_marked != slow:
        return "slow-accounting variant" if slow_marked else "not the slow-accounting variant"
    return None


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("cluster", "live scheduler testbed")
    group.addoption(
        "--flavour",
        default="soge",
        help="the implementation whose stack is up; its testbed/<family>/ directory "
        "selects the family",
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
    family = family_of(config.getoption("--flavour"), known_flavours())
    if config.getoption("--slow-accounting") and family not in SLOW_ACCOUNTING_FAMILIES:
        raise pytest.UsageError(
            f"--slow-accounting is a variant of the {', '.join(sorted(SLOW_ACCOUNTING_FAMILIES))} "
            f"stack, not of {family}"
        )


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Mark every item here ``integration``; select the variant; validate family/flavour marks.

    A test the active family or flavour cannot run is *deselected* (it is not
    a result of this run), as is the slow-accounting variant not asked for.
    """
    active = config.getoption("--flavour")
    slow = config.getoption("--slow-accounting")
    flavours = known_flavours()
    families = set(flavours.values())
    active_family = flavours[active]
    kept: list[pytest.Item] = []
    deselected: list[pytest.Item] = []
    for item in items:
        if not item.path.is_relative_to(HERE):
            kept.append(item)
            continue
        item.add_marker(pytest.mark.integration)
        marked: dict[str, set[str]] = {}
        for marker, known in (("family", families), ("flavour", set(flavours))):
            names = {name for mark in item.iter_markers(marker) for name in mark.args}
            if names - known:
                raise pytest.UsageError(
                    f"{item.nodeid}: @pytest.mark.{marker} names unknown {marker}(s) "
                    f"{', '.join(sorted(names - known))}; known: {', '.join(sorted(known))}"
                )
            marked[marker] = names
        reason = deselect_reason(
            flavours=marked["flavour"],
            families=marked["family"],
            slow_marked=bool(item.get_closest_marker("slow_accounting")),
            active_flavour=active,
            active_family=active_family,
            slow=slow,
        )
        (kept if reason is None else deselected).append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = kept


@pytest.fixture(scope="session")
def cluster(request: pytest.FixtureRequest) -> Cluster:
    """The reachable scheduler stack, or a session-wide skip."""
    flavour = request.config.getoption("--flavour")
    family = known_flavours()[flavour]
    cluster = CLUSTERS[family](flavour=flavour, workdir=WORK_ROOT)
    ready, output = cluster.poll_ready()
    if not ready:
        reason = f"no {family} cluster reachable ({flavour}): {output.strip() or 'no output'}"
        if request.config.getoption("--require-cluster"):
            pytest.fail(reason, pytrace=False)
        pytest.skip(reason)
    # The stack exports HPC_RUNNER_CONFIG for the flavour it was built from;
    # a mismatched --flavour would test one stack with another's config.
    exported = os.environ.get("HPC_RUNNER_CONFIG")
    if exported and Path(exported) != cluster.config_path:
        pytest.fail(
            f"--flavour {flavour} expects {cluster.config_path} but the stack exports "
            f"HPC_RUNNER_CONFIG={exported}",
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
