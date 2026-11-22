"""The Slurm capture module and the files it wrote.

The files are in tests/unit/fixtures/slurm/<flavour>/, written by
``hpc-testbed --scheduler slurm --flavour <flavour> capture``
(tests/integration/capture_slurm.py). Nothing here parses them for
hpc-runner: these tests pin the commands the captures are taken with and
that both flavours hold the same, whole set.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.integration.capture_slurm import (
    SACCT_FIELDS,
    SACCT_FORMAT,
    SQUEUE_FIELDS,
    SQUEUE_FORMAT,
    history_argv,
    is_stub,
    job_rows,
    sacct_argv,
    sbatch_argv,
    scontrol_argv,
    squeue_argv,
    states,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "slurm"
FLAVOURS = ("22.05", "24.11")

#: docs/dev/slurm/PLAN.md, "Captures to take".
PLAN_SCENARIOS = (
    "ok",
    "exit1",
    "signal",
    "timeout",
    "out_of_memory",
    "cancelled_pending",
    "cancelled_running",
    "held",
    "dependent_skipped",
    "dependent_pending",
    "array",
    "array_partial",
    "array_throttled",
    "suspended",
    "requeued",
    "node_fail",
    "running",
    "pending_resources",
    "dependent_ok",
)
REJECTED_SCENARIOS = ("bad_partition", "bad_account")
#: The early ``sacct`` rows, which only 22.05 shows.
ONLY_22_05 = {"sacct_ok_stub.txt", "sacct_running_stub.txt"}
#: ``squeue -h`` with nothing queued prints nothing.
EMPTY = {"squeue_empty.txt"}


@pytest.fixture(params=FLAVOURS)
def flavour(request: pytest.FixtureRequest) -> str:
    return str(request.param)


def _manifest(flavour: str) -> dict[str, Any]:
    manifest: dict[str, Any] = json.loads((FIXTURES / flavour / "manifest.json").read_text())
    return manifest


def _names(flavour: str) -> set[str]:
    return {path.name for path in (FIXTURES / flavour).iterdir()}


def _rows(flavour: str, name: str) -> list[list[str]]:
    return [line.split("|") for line in (FIXTURES / flavour / name).read_text().splitlines()]


def test_the_field_lists_are_these() -> None:
    assert SACCT_FORMAT == (
        "JobID,JobIDRaw,User,Account,Partition,State,ExitCode,Reason,Submit,Start,End,"
        "Elapsed,Timelimit,NodeList,AllocCPUS,ReqCPUS,ReqMem,MaxRSS,ReqTRES,AllocTRES,"
        "WorkDir,JobName"
    )
    assert SQUEUE_FORMAT == "%i|%F|%K|%u|%a|%P|%T|%r|%V|%S|%e|%M|%l|%C|%m|%N|%E|%b|%Z|%j"
    assert len(SACCT_FIELDS) == 22
    assert len(SQUEUE_FIELDS) == 20
    assert SQUEUE_FIELDS[-1] == ("%j", "JobName")


def test_sacct_is_asked_for_every_row_of_a_job() -> None:
    assert sacct_argv("12") == ["sacct", "-P", "-n", "-j", "12", f"--format={SACCT_FORMAT}"]
    assert sacct_argv("12,13", duplicates=True) == [
        "sacct",
        "-P",
        "-n",
        "-D",
        "-j",
        "12,13",
        f"--format={SACCT_FORMAT}",
    ]


def test_squeue_is_asked_for_pipe_delimited_text_without_a_header() -> None:
    assert squeue_argv() == ["squeue", "-h", "-o", SQUEUE_FORMAT]
    assert squeue_argv("12") == ["squeue", "-h", "-j", "12", "-o", SQUEUE_FORMAT]
    assert squeue_argv("12", tasks=True) == ["squeue", "-h", "-r", "-j", "12", "-o", SQUEUE_FORMAT]


def test_the_history_is_asked_by_user_and_start_time() -> None:
    assert history_argv("tester", "2026-11-22T20:09:12") == [
        "sacct",
        "-P",
        "-n",
        "-u",
        "tester",
        "-S",
        "2026-11-22T20:09:12",
        f"--format={SACCT_FORMAT}",
    ]


#: The jobs submitted with ``sbatch --wait``: scenario and what sbatch exited with.
WAITED = {
    "ok": 0,
    "exit1": 1,
    "exit137": 137,
    "out_of_memory": 1,
    "array_partial": 1,
    "cancelled_pending": 0,
    "cancelled_running": 1,
    "dependent_skipped": 0,
}


def test_sbatch_wait_prints_the_job_id_and_exits_as_the_job_did(flavour: str) -> None:
    manifest = _manifest(flavour)
    for scenario, returncode in WAITED.items():
        waited = json.loads((FIXTURES / flavour / f"sbatch_wait_{scenario}.json").read_text())
        assert "--wait" in waited["argv"], scenario
        assert waited["returncode"] == returncode, scenario
        assert waited["stdout"] == f"Submitted batch job {manifest['jobs'][f'wait_{scenario}']}\n"
        assert waited["stderr"] == "", scenario
        assert f"sacct_wait_{scenario}.txt" in manifest["files"], scenario


def test_scontrol_and_sbatch_argv() -> None:
    assert scontrol_argv("12") == ["scontrol", "show", "job", "12"]
    assert sbatch_argv("ok", "/work/capture-jobs/ok.sh", "--hold") == [
        "sbatch",
        "-J",
        "ok",
        "--chdir=/work/capture-jobs",
        "-o",
        "%x.%j.out",
        "--hold",
        "/work/capture-jobs/ok.sh",
    ]
    assert sbatch_argv("a", "/work/capture-jobs/a.sh", "--array=1-4", array=True)[4:6] == [
        "-o",
        "%x.%A.%a.out",
    ]


def test_row_helpers_read_the_job_rows_only() -> None:
    def row(job_id: str, partition: str, state: str) -> list[str]:
        fields = [""] * len(SACCT_FIELDS)
        fields[0], fields[4], fields[5] = job_id, partition, state
        return fields

    rows = [row("7", "all", "CANCELLED by 1003"), row("7.batch", "", "CANCELLED")]
    assert job_rows(rows) == [rows[0]]
    assert states(rows) == ["CANCELLED"]
    assert is_stub(rows) is False
    assert is_stub([row("7", "", "COMPLETED"), row("7.batch", "", "COMPLETED")]) is True


def test_both_flavours_hold_the_same_files_but_for_the_early_rows() -> None:
    assert _names("22.05") - _names("24.11") == ONLY_22_05
    assert _names("24.11") - _names("22.05") == set()


def test_the_manifest_lists_every_file_and_the_field_lists(flavour: str) -> None:
    manifest = _manifest(flavour)
    assert manifest["flavour"] == flavour
    assert manifest["scheduler"].startswith(f"slurm-wlm {flavour}.")
    assert manifest["sacct_format"] == SACCT_FORMAT
    assert manifest["squeue_format"] == SQUEUE_FORMAT
    assert set(manifest["files"]) | {"manifest.json"} == _names(flavour)


def test_no_capture_is_empty_but_the_empty_queue(flavour: str) -> None:
    empty = {p.name for p in (FIXTURES / flavour).iterdir() if p.read_text() == ""}
    assert empty == EMPTY


def test_every_scenario_of_the_plan_has_a_job_a_submission_and_an_accounting_text(
    flavour: str,
) -> None:
    manifest = _manifest(flavour)
    for scenario in PLAN_SCENARIOS:
        assert manifest["jobs"][scenario].isdigit(), scenario
        submitted = json.loads((FIXTURES / flavour / f"sbatch_{scenario}.json").read_text())
        assert submitted["returncode"] == 0, scenario
        assert submitted["stdout"] == f"Submitted batch job {manifest['jobs'][scenario]}\n"
        assert submitted["stderr"] == ""
        assert f"sacct_{scenario}.txt" in manifest["files"], scenario


def test_the_rejections_have_the_exit_status_and_message_of_both_commands(flavour: str) -> None:
    for scenario in REJECTED_SCENARIOS:
        for command in ("sbatch", "srun"):
            rejected = json.loads((FIXTURES / flavour / f"{command}_{scenario}.json").read_text())
            assert rejected["argv"][0] == command
            assert rejected["returncode"] == 1
            assert rejected["stdout"] == ""
            assert rejected["stderr"].startswith(f"{command}: error: ")


def test_every_sacct_and_squeue_line_has_the_columns_asked_for(flavour: str) -> None:
    for name in sorted(_names(flavour)):
        if name.endswith(".txt") and name.startswith("sacct_"):
            assert {len(row) for row in _rows(flavour, name)} == {22}, name
        if name.endswith(".txt") and name.startswith("squeue_") and name not in EMPTY:
            assert {len(row) for row in _rows(flavour, name)} == {20}, name


def test_the_early_rows_have_no_partition_and_the_settled_ones_do() -> None:
    assert is_stub(_rows("22.05", "sacct_ok_stub.txt")) is True
    assert is_stub(_rows("22.05", "sacct_running_stub.txt")) is True
    for flavour in FLAVOURS:
        for name in sorted(_names(flavour) - ONLY_22_05):
            if name.endswith(".txt") and name.startswith("sacct_"):
                assert is_stub(_rows(flavour, name)) is False, (flavour, name)
