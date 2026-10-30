"""Integration-test fixtures (docs/dev/testbed/TESTBED_PLAN.md section 4.2).

The suite runs inside the testbed's ``login`` container; the stack's
lifecycle belongs to ``make -C testbed sge-up/down``, not to pytest. The
session fixture only checks readiness and skips (or, with
``--require-cluster``, fails) when no scheduler is reachable.
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
CLEAN_SCRIPT = Path("/opt/testbed/clean.sh")


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


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Mark every item here ``integration``; skip those not for the active flavour."""
    active = config.getoption("--flavour")
    for item in items:
        if not item.path.is_relative_to(HERE):
            continue
        item.add_marker(pytest.mark.integration)
        supported = {name for mark in item.iter_markers("flavour") for name in mark.args}
        if supported and active not in supported:
            item.add_marker(pytest.mark.skip(reason=f"not supported on flavour {active}"))


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
