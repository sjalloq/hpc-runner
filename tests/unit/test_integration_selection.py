"""The integration suite's family and flavour selection (tests/integration/conftest.py).

Pure functions only: nothing here needs a testbed. The family comes from the
flavour's directory and picks the cluster class; ``family``/``flavour``
marks and the slow-accounting variant decide what is deselected (D-T48).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.integration.cluster import ACCOUNTING_FILES, CLUSTERS, SGECluster
from tests.integration.conftest import deselect_reason, family_of, known_flavours

FLAVOURS = {"soge": "sge", "ocs": "sge", "debian": "slurm"}


def _reason(**overrides: object) -> str | None:
    args: dict = {
        "flavours": set(),
        "families": set(),
        "slow_marked": False,
        "active_flavour": "soge",
        "active_family": "sge",
        "slow": False,
    }
    args.update(overrides)
    return deselect_reason(**args)


def test_known_flavours_maps_each_flavour_to_its_directory(tmp_path: Path) -> None:
    for family, flavour in (("sge", "soge"), ("slurm", "debian")):
        (tmp_path / family / flavour).mkdir(parents=True)
        (tmp_path / family / flavour / "compose.yml").write_text("services: {}\n")
    (tmp_path / "common").mkdir()  # no compose.yml: not a flavour
    assert known_flavours(tmp_path) == {"soge": "sge", "debian": "slurm"}


def test_the_repository_testbed_has_a_class_for_every_family() -> None:
    assert set(known_flavours().values()) <= set(CLUSTERS)
    assert CLUSTERS["sge"] is SGECluster


def test_family_of_a_known_flavour() -> None:
    assert family_of("ocs", FLAVOURS) == "sge"


def test_family_of_an_unknown_flavour_lists_the_known_ones() -> None:
    with pytest.raises(pytest.UsageError, match="known: debian, ocs, soge"):
        family_of("nope", FLAVOURS)


def test_family_without_a_cluster_class_is_a_usage_error() -> None:
    with pytest.raises(pytest.UsageError, match="family 'slurm', which has no cluster class"):
        family_of("debian", FLAVOURS)


def test_unmarked_test_runs_on_every_family() -> None:
    assert _reason() is None
    assert _reason(active_flavour="debian", active_family="slurm") is None


def test_family_mark_deselects_other_families() -> None:
    assert _reason(families={"sge"}) is None
    assert _reason(families={"sge"}, active_flavour="debian", active_family="slurm") == (
        "family slurm not in ['sge']"
    )


def test_flavour_mark_deselects_other_flavours() -> None:
    assert _reason(flavours={"soge"}) is None
    assert _reason(flavours={"soge"}, active_flavour="ocs") == "flavour ocs not in ['soge']"


@pytest.mark.parametrize(
    ("slow_marked", "slow", "selected"),
    [(False, False, True), (True, True, True), (True, False, False), (False, True, False)],
)
def test_slow_accounting_selects_exactly_its_variant(
    slow_marked: bool, slow: bool, selected: bool
) -> None:
    assert (_reason(slow_marked=slow_marked, slow=slow) is None) is selected


def test_sge_cluster_dumps_qstat_for_a_failed_wait() -> None:
    cluster = SGECluster(flavour="soge", workdir=Path("/work"))
    assert cluster.name == "sge" and cluster.probe_command == "qstat"
    assert cluster.dump_commands("42") == [["qstat", "-f"], ["qstat", "-j", "42"]]


def test_every_sge_flavour_names_its_accounting_file() -> None:
    sge = {flavour for flavour, family in known_flavours().items() if family == "sge"}
    assert sge == set(ACCOUNTING_FILES)
