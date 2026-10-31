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
"""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.integration.cluster import WORK_ROOT, Cluster, SGECluster, poll_ready

HERE = Path(__file__).resolve().parent
TESTBED_DIR = HERE.parent.parent / "testbed"
CLEAN_SCRIPT = Path("/opt/testbed/clean.sh")


def known_flavours(testbed_dir: Path = TESTBED_DIR) -> dict[str, str]:
    """``{flavour: family}`` for every ``testbed/<family>/<flavour>/compose.yml``."""
    return {p.parent.name: p.parent.parent.name for p in testbed_dir.glob("*/*/compose.yml")}


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("cluster", "live scheduler testbed")
    group.addoption("--cluster", default="sge", choices=["sge"], help="scheduler family under test")
    group.addoption("--flavour", default="soge", help="the implementation whose stack is up")
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
    flavour = config.getoption("--flavour")
    flavours = known_flavours()
    if flavour not in flavours:
        raise pytest.UsageError(
            f"--flavour {flavour!r} is not a testbed flavour; known: {', '.join(sorted(flavours))}"
        )


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Mark every item here ``integration``; select the variant; validate flavour marks.

    A test the active flavour cannot run is *deselected* (it is not a result
    of this run), as is the slow-accounting variant not asked for.
    """
    active = config.getoption("--flavour")
    slow = config.getoption("--slow-accounting")
    flavours = known_flavours()
    kept: list[pytest.Item] = []
    deselected: list[pytest.Item] = []
    for item in items:
        if not item.path.is_relative_to(HERE):
            kept.append(item)
            continue
        item.add_marker(pytest.mark.integration)
        supported = {name for mark in item.iter_markers("flavour") for name in mark.args}
        unknown = supported - flavours.keys()
        if unknown:
            raise pytest.UsageError(
                f"{item.nodeid}: @pytest.mark.flavour names unknown flavour(s) "
                f"{', '.join(sorted(unknown))}; known: {', '.join(sorted(flavours))}"
            )
        wrong_flavour = bool(supported) and active not in supported
        wrong_variant = bool(item.get_closest_marker("slow_accounting")) != slow
        (deselected if wrong_flavour or wrong_variant else kept).append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = kept


@pytest.fixture(scope="session")
def cluster(request: pytest.FixtureRequest) -> Cluster:
    """The reachable scheduler stack, or a session-wide skip."""
    name = request.config.getoption("--cluster")
    flavour = request.config.getoption("--flavour")
    ready, output = poll_ready()
    if not ready:
        reason = f"no {name} cluster reachable ({flavour}): {output.strip() or 'no output'}"
        if request.config.getoption("--require-cluster"):
            pytest.fail(reason, pytrace=False)
        pytest.skip(reason)
    assert name == "sge"
    cluster = SGECluster(flavour=flavour, workdir=WORK_ROOT)
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
    """Per-test cleanup: qdel everything, wait for an empty qstat, remove the workdir."""
    yield
    cluster.run([str(CLEAN_SCRIPT), str(workdir)], timeout=120)
