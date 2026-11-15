"""The integration suite's scheduler and flavour selection (tests/integration/conftest.py).

Pure functions only: nothing here needs a testbed. ``--scheduler`` picks the
cluster class and ``--flavour`` a release of it; ``scheduler``/``flavour``
marks and the slow-accounting variant decide what is deselected.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hpc_runner._testbed import DEFAULT_FLAVOURS
from tests.integration.cluster import ACCOUNTING_FILES, CLUSTERS, SGECluster
from tests.integration.conftest import deselect_reason, known_flavours, select_stack

KNOWN = {"sge": ["ocs", "soge"], "slurm": ["22.05", "24.11"]}


def _reason(**overrides: object) -> str | None:
    args: dict = {
        "schedulers": set(),
        "flavours": set(),
        "slow_marked": False,
        "active_scheduler": "sge",
        "active_flavour": "soge",
        "slow": False,
    }
    args.update(overrides)
    return deselect_reason(**args)


def test_known_flavours_lists_each_schedulers_directories(tmp_path: Path) -> None:
    for scheduler, flavour in (("sge", "soge"), ("sge", "ocs"), ("slurm", "24.11")):
        (tmp_path / scheduler / flavour).mkdir(parents=True)
        (tmp_path / scheduler / flavour / "compose.yml").write_text("services: {}\n")
    (tmp_path / "common").mkdir()  # no compose.yml: not a flavour
    assert known_flavours(tmp_path) == {"sge": ["ocs", "soge"], "slurm": ["24.11"]}


def test_the_repository_testbed_has_a_class_and_a_default_for_every_scheduler() -> None:
    known = known_flavours()
    assert set(known) <= set(CLUSTERS)
    assert CLUSTERS["sge"] is SGECluster
    for scheduler, flavours in known.items():
        assert DEFAULT_FLAVOURS[scheduler] in flavours


def test_scheduler_and_flavour_are_taken_as_given() -> None:
    assert select_stack("sge", "ocs", KNOWN) == ("sge", "ocs")


def test_no_flavour_means_the_schedulers_default() -> None:
    assert select_stack("sge", None, KNOWN) == ("sge", "soge")
    assert select_stack("slurm", None, KNOWN) == ("slurm", "24.11")


def test_an_unknown_scheduler_lists_the_known_ones() -> None:
    with pytest.raises(pytest.UsageError, match="known: sge, slurm"):
        select_stack("nope", None, KNOWN)


def test_an_unknown_flavour_lists_that_schedulers_flavours() -> None:
    with pytest.raises(pytest.UsageError, match="not a sge flavour; known: ocs, soge"):
        select_stack("sge", "24.11", KNOWN)


def test_scheduler_without_a_cluster_class_is_a_usage_error() -> None:
    with pytest.raises(pytest.UsageError, match="scheduler 'lsf' has no cluster class"):
        select_stack("lsf", "ce", {**KNOWN, "lsf": ["ce"]})


def test_unmarked_test_runs_on_every_scheduler() -> None:
    assert _reason() is None
    assert _reason(active_scheduler="slurm", active_flavour="24.11") is None


def test_scheduler_mark_deselects_other_schedulers() -> None:
    assert _reason(schedulers={"sge"}) is None
    assert _reason(schedulers={"sge"}, active_scheduler="slurm", active_flavour="24.11") == (
        "scheduler slurm not in ['sge']"
    )


def test_flavour_mark_names_scheduler_and_flavour() -> None:
    assert _reason(flavours={"sge/soge"}) is None
    assert _reason(flavours={"sge/soge"}, active_flavour="ocs") == (
        "flavour sge/ocs not in ['sge/soge']"
    )


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
    assert set(known_flavours()["sge"]) == set(ACCOUNTING_FILES)
