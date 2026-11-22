"""The integration suite's scheduler and flavour selection (tests/integration/conftest.py).

Pure functions only: nothing here needs a testbed. ``--scheduler`` picks the
cluster class and ``--flavour`` a release of it; the test's folder, its
``flavour`` mark and the slow-accounting variant decide what is deselected.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from hpc_runner._testbed import DEFAULT_FLAVOURS
from hpc_runner.core.config import load_config
from tests.integration.cluster import (
    ACCOUNTING_FILES,
    CLUSTERS,
    PBSCluster,
    SGECluster,
    SlurmCluster,
    sacct_finished_rows,
)
from tests.integration.conftest import (
    deselect_reason,
    known_flavours,
    scheduler_folder,
    select_stack,
)

KNOWN = {"sge": ["ocs", "soge"], "slurm": ["22.05", "24.11"]}


def _reason(**overrides: object) -> str | None:
    args: dict = {
        "folder": None,
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
    assert CLUSTERS == {"sge": SGECluster, "slurm": SlurmCluster, "pbs": PBSCluster}
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


def test_a_test_file_in_the_top_folder_has_no_scheduler_folder() -> None:
    root = Path("/src/tests/integration")
    assert scheduler_folder(root / "test_submit.py", root) is None


def test_a_test_file_under_a_schedulers_folder_belongs_to_it() -> None:
    root = Path("/src/tests/integration")
    assert scheduler_folder(root / "sge" / "test_submit.py", root) == "sge"
    assert scheduler_folder(root / "slurm" / "deeper" / "test_x.py", root) == "slurm"


def test_every_scheduler_folder_in_the_repository_names_a_testbed_scheduler() -> None:
    root = Path(__file__).resolve().parents[1] / "integration"
    folders = {scheduler_folder(p, root) for p in root.rglob("test_*.py")}
    assert folders == {None, "sge"}
    assert "sge" in known_flavours()


def test_top_folder_test_runs_on_every_scheduler() -> None:
    assert _reason() is None
    assert _reason(active_scheduler="slurm", active_flavour="24.11") is None


def test_scheduler_folder_test_runs_on_that_scheduler_only() -> None:
    assert _reason(folder="sge") is None
    assert _reason(folder="sge", active_scheduler="slurm", active_flavour="24.11") == (
        "a sge test, not run on slurm"
    )
    assert _reason(folder="slurm") == "a slurm test, not run on sge"


def test_flavour_mark_still_narrows_a_scheduler_folder_test() -> None:
    assert _reason(folder="sge", flavours={"sge/ocs"}) == "flavour sge/soge not in ['sge/ocs']"
    assert _reason(folder="sge", flavours={"sge/ocs"}, active_flavour="ocs") is None


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


REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("scheduler", "flavour"),
    [("sge", "soge"), ("sge", "ocs"), ("slurm", "24.11"), ("slurm", "22.05")],
)
def test_each_stack_exports_its_own_config_naming_its_scheduler(
    scheduler: str, flavour: str
) -> None:
    folder = REPO / "testbed" / scheduler / flavour
    config = load_config(folder / "hpc-runner-testbed.toml")
    assert config.scheduler == scheduler
    exported = f"HPC_RUNNER_CONFIG: /src/testbed/{scheduler}/{flavour}/hpc-runner-testbed.toml"
    assert exported in (folder / "compose.yml").read_text()
    # The generic alias test asks for the cluster's second queue by this entry.
    cluster = CLUSTERS[scheduler](flavour=flavour, workdir=Path("/work"))
    assert config.tools["sort"]["queue"] == cluster.other_queue


def test_slurm_cluster_answers_in_slurms_words() -> None:
    cluster = SlurmCluster(flavour="24.11", workdir=Path("/work"))
    assert cluster.name == "slurm" and cluster.probe_command == "sinfo"
    assert cluster.dump_commands("42") == [
        ["sinfo", "-N", "-l"],
        ["scontrol", "show", "job", "42"],
    ]
    assert cluster.array_task_var == "SLURM_ARRAY_TASK_ID"
    assert cluster.array_task_id("42", 3) == "42_3"
    assert cluster.hold_args == ("--hold",)
    assert (cluster.default_queue, cluster.other_queue) == ("all", "short")
    assert cluster.queue_slots == {"all": 4}
    assert cluster.submit_command == "sbatch"
    assert cluster.listing_command == ("squeue",)
    assert cluster.accounting_command == "sacct"
    assert cluster.submit_output("42") == "Submitted batch job 42"
    assert cluster.directive_prefix == "#SBATCH"


def test_slurm_queue_names_and_slots_match_the_testbed_slurm_conf() -> None:
    conf = (REPO / "testbed/slurm/common/conf/slurm.conf").read_text().splitlines()
    assert [line for line in conf if line.startswith(("NodeName=", "PartitionName="))] == [
        "NodeName=exec1 CPUs=2 RealMemory=2000 TmpDisk=1000 Feature=fast Gres=gpu:1 State=UNKNOWN",
        "NodeName=exec2 CPUs=2 RealMemory=2000 TmpDisk=1000 State=UNKNOWN",
        "PartitionName=all Nodes=exec[1-2] Default=YES MaxTime=INFINITE State=UP",
        "PartitionName=short Nodes=exec[1-2] MaxTime=00:10:00 State=UP",
    ]


def test_slurm_cluster_cannot_empty_the_accounting_store() -> None:
    with pytest.raises(NotImplementedError):
        SlurmCluster(flavour="24.11", workdir=Path("/work")).accounting_truncate()


def test_a_cluster_class_without_an_answer_names_the_member() -> None:
    with pytest.raises(NotImplementedError, match=r"PBSCluster\.extra_output_lines"):
        _ = PBSCluster(flavour="openpbs", workdir=Path("/work")).extra_output_lines


def test_sacct_finished_rows_counts_one_per_ended_task_or_run() -> None:
    # A three-task array, all ended (24.11 testbed, job 10).
    assert sacct_finished_rows("10_1|COMPLETED\n10_2|COMPLETED\n10_3|FAILED\n") == 3
    # A requeued job: its first run is a record, the pending second is not.
    assert sacct_finished_rows("9|REQUEUED\n9|PENDING\n") == 1
    assert sacct_finished_rows("9|REQUEUED\n9|CANCELLED by 1003\n") == 2
    assert sacct_finished_rows("12_[1-4%2]|PENDING\n12_1|RUNNING\n13|SUSPENDED\n") == 0
    assert sacct_finished_rows("") == 0


def test_only_slurm_22_05_adds_lines_to_a_jobs_output() -> None:
    work = Path("/work")
    assert SGECluster(flavour="soge", workdir=work).extra_output_lines == ()
    assert SGECluster(flavour="ocs", workdir=work).extra_output_lines == ()
    assert SlurmCluster(flavour="24.11", workdir=work).extra_output_lines == ()
    patterns = SlurmCluster(flavour="22.05", workdir=work).extra_output_lines
    # The tail of an `echo hello` job's output file on the 22.05 testbed.
    tail = [
        "slurmstepd-exec1: error: common_file_write_uint32s: write pid 664 to "
        "/sys/fs/cgroup/cgroup.procs failed: Device or resource busy",
        "slurmstepd-exec1: error: Unable to move pid 664 to init root cgroup /sys/fs/cgroup",
        "slurmstepd-exec1: error: error unlocking cgroup "
        "'/sys/fs/cgroup/system.slice/exec1_slurmstepd.scope' : Bad file descriptor",
    ]
    assert len(patterns) == 3
    assert all(re.fullmatch(p, line) for p, line in zip(patterns, tail))
    assert not any(re.fullmatch(p, "hello") for p in patterns)
    # The out-of-memory report is the job's own news, not one of the three.
    oom = (
        "slurmstepd-exec1: error: Detected 1 oom-kill event(s) in StepId=3.batch. "
        "Some of your processes may have been killed by the cgroup out-of-memory handler."
    )
    assert not any(re.fullmatch(p, oom) for p in patterns)
