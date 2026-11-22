"""``scheduler.submit(job, wait=True)``: the scheduler's own waiting submit command.

SGE submits with ``qsub -sync y`` and Slurm with ``sbatch --wait``; both
print the job id at once and exit when the job has ended, with its exit
code. What they printed and exited with is captured
(``qsub_sync_<scenario>.json``, ``sbatch_wait_<scenario>.json``), with the
accounting of the same job, so every test here runs on each scheduler and
each flavour (the ``fixtures`` fixture).
"""

from __future__ import annotations

from functools import partialmethod
from pathlib import Path
from unittest.mock import patch

import pytest

from hpc_runner.core.config import HPCConfig
from hpc_runner.core.exceptions import SubmissionError
from hpc_runner.core.result import ArrayJobResult, JobResult, JobStatus
from hpc_runner.schedulers import get_scheduler
from tests.fakes import Fixtures, make_job, make_responder, patch_seam
from tests.unit.test_schedulers.test_subprocess_seam import QSUB_REJECT

S = JobStatus

#: The waiting submit command before the script path.
ARGV = {"sge": ["qsub", "-sync", "y"], "slurm": ["sbatch", "--wait"]}

#: scheduler -> scenario -> (what the submit command exited with, the status read after it).
CASES: dict[str, dict[str, tuple[int, JobStatus]]] = {
    "sge": {
        "ok": (0, S.COMPLETED),
        "exit1": (1, S.FAILED),
        "exit137": (137, S.FAILED),
        # The dependency guard's skip code: qsub returns it as any other.
        "skipped": (113, S.SKIPPED),
        # Deleted while pending: qsub exits 0 and there is never a record.
        "deleted_pending": (0, S.UNKNOWN),
    },
    "slurm": {
        "ok": (0, S.COMPLETED),
        "exit1": (1, S.FAILED),
        "exit137": (137, S.FAILED),
        "out_of_memory": (1, S.KILLED),
        "cancelled_pending": (0, S.CANCELLED),
        "cancelled_running": (1, S.CANCELLED),
        "dependent_skipped": (0, S.SKIPPED),
    },
}
ALL_CASES = sorted({scenario for cases in CASES.values() for scenario in cases})


@pytest.fixture
def scheduler(fixtures, scheduler_env, monkeypatch):
    # A job with no record is given up on at once, not after a minute.
    monkeypatch.setattr(
        JobResult, "wait", partialmethod(JobResult.wait, poll_interval=0.01, unknown_after=0.0)
    )
    return get_scheduler(fixtures.scheduler, config=HPCConfig())


def _expected(fixtures: Fixtures, scenario: str) -> tuple[int, JobStatus]:
    if scenario not in CASES[fixtures.scheduler]:
        pytest.skip(f"no {scenario} capture on {fixtures.scheduler}")
    return CASES[fixtures.scheduler][scenario]


class Waited:
    """The seam answering the waiting submit with *scenario*'s capture."""

    def __init__(self, fixtures: Fixtures, scenario: str) -> None:
        self.fixtures = fixtures
        self.scenario = scenario
        self.job_id = fixtures.job(fixtures.waited(scenario))
        self.events: list[str] = []
        self.submits: list[list[str]] = []
        self.scripts: list[str] = []
        self._rest = make_responder(fixtures)

    def respond(self, argv: list[str]) -> tuple[int, str, str]:
        if argv[0] == self.fixtures.submit_command:
            self.submits.append(argv)
            self.scripts.append(Path(argv[-1]).read_text())
            return self.fixtures.waited_answer(self.scenario)
        self.events.append(argv[0])
        return self._rest(argv)

    def on_submit(self, result: JobResult) -> None:
        self.events.append(f"submitted {result.job_id}")


@pytest.mark.parametrize("scenario", ALL_CASES)
def test_the_exit_code_is_the_submit_commands_and_the_status_is_read_after(
    fixtures, scheduler, tmp_path, scenario
):
    code, status = _expected(fixtures, scenario)
    seam = Waited(fixtures, scenario)
    job = make_job("true", name="w", workdir=str(tmp_path))
    with patch_seam(seam.respond) as mock:
        result = scheduler.submit(job, wait=True, on_submit=seam.on_submit)
        calls = mock.call_count
        assert result.exit_code == code
        assert result.returncode == code
        assert result.final_status is status
        if status.is_terminal:
            # Read once, when the job ended: asking again runs nothing.
            assert result.status is status
            assert mock.call_count == calls
    assert not isinstance(result, ArrayJobResult)
    assert result.job_id == seam.job_id
    assert result.stdout_path == tmp_path / f"w.{seam.job_id}.out"
    (argv,) = seam.submits
    assert argv[:-1] == ARGV[fixtures.scheduler]
    (script,) = seam.scripts
    assert script.startswith("#!/bin/bash\n") and "\ntrue\n" in script
    # The id is announced before anything is asked about the job.
    assert seam.events[0] == f"submitted {seam.job_id}"
    assert seam.events.count(f"submitted {seam.job_id}") == 1
    assert len(seam.events) > 1


def test_an_array_has_every_tasks_status(fixtures, scheduler, tmp_path):
    seam = Waited(fixtures, "array_partial")
    job = make_job("true", name="w", array="1-3", workdir=str(tmp_path))
    with patch_seam(seam.respond) as mock:
        result = scheduler.submit(job, wait=True, on_submit=seam.on_submit)
        calls = mock.call_count
        assert isinstance(result, ArrayJobResult)
        assert result.final_status is S.FAILED
        assert result.status is S.FAILED
        assert result.task_statuses().tasks == {1: S.COMPLETED, 2: S.COMPLETED, 3: S.FAILED}
        assert result.task_statuses().failed == [3]
        assert mock.call_count == calls
    # Task 3 exited 1, and so did the submit command.
    assert result.exit_code == 1
    assert result.job_id == seam.job_id
    assert seam.events[0] == f"submitted {seam.job_id}"


def test_the_submit_command_has_no_timeout_and_its_output_is_read_as_printed(
    fixtures, scheduler, tmp_path
):
    seam = Waited(fixtures, "ok")
    with patch_seam(seam.respond) as mock:
        scheduler.submit(make_job("true", workdir=str(tmp_path)), wait=True)
    call = mock.call_args_list[0]
    assert call.args[0][0] == fixtures.submit_command
    assert call.kwargs["timeout"] is None
    assert call.kwargs["check"] is False
    assert call.kwargs["cwd"] == tmp_path
    assert callable(call.kwargs["on_stdout_line"])
    # Every other command is called as before, without the keyword.
    assert all("on_stdout_line" not in c.kwargs for c in mock.call_args_list[1:])


def test_the_script_is_removed_unless_kept(fixtures, scheduler, tmp_path):
    seam = Waited(fixtures, "ok")
    with patch_seam(seam.respond):
        scheduler.submit(make_job("true", workdir=str(tmp_path)), wait=True)
        assert list((tmp_path / "scripts").iterdir()) == []
        scheduler.submit(make_job("true", workdir=str(tmp_path)), wait=True, keep_script=True)
    assert len(list((tmp_path / "scripts").iterdir())) == 1


def test_the_trace_gets_the_script(fixtures, scheduler, tmp_path):
    module = f"hpc_runner.schedulers.{fixtures.scheduler}.scheduler.recorder.note_script"
    seam = Waited(fixtures, "ok")
    with patch(module) as note, patch_seam(seam.respond):
        scheduler.submit(make_job("true", workdir=str(tmp_path)), wait=True)
    ((path, text),) = (call.args for call in note.call_args_list)
    assert str(path) == seam.submits[0][-1]
    assert text == seam.scripts[0]


def test_a_rejected_submission_is_an_error_and_nothing_is_announced(fixtures, scheduler, tmp_path):
    if fixtures.scheduler == "slurm":
        rejected = fixtures.result("sbatch_bad_partition.json")
    else:
        rejected = (1, "", QSUB_REJECT)
    announced: list[JobResult] = []
    with patch_seam(lambda argv: rejected), pytest.raises(SubmissionError) as err:
        scheduler.submit(
            make_job("true", workdir=str(tmp_path)), wait=True, on_submit=announced.append
        )
    assert announced == []
    assert err.value.stderr == rejected[2]
    assert err.value.argv is not None and err.value.argv[:-1] == ARGV[fixtures.scheduler]


def test_without_wait_the_plain_command_is_used_and_the_handle_polls(
    fixtures, mock_commands, tmp_path
):
    scheduler = get_scheduler(fixtures.scheduler, config=HPCConfig())
    announced: list[JobResult] = []
    result = scheduler.submit(make_job("true", workdir=str(tmp_path)), on_submit=announced.append)
    assert announced == [result]
    assert mock_commands.call_args_list[0].args[0][:-1] == [fixtures.submit_command]
    assert result.final_status is None and result.exit_code is None


class TestSlurmCommands:
    """How many commands a waited Slurm job costs."""

    def test_a_job_is_sbatch_and_one_sacct(self, slurm_fixtures, monkeypatch, tmp_path):
        from hpc_runner.schedulers.slurm import SlurmScheduler

        monkeypatch.setenv("HPC_SCRIPT_DIR", str(tmp_path / "scripts"))
        seam = Waited(slurm_fixtures, "exit137")
        with patch_seam(seam.respond) as mock:
            result = SlurmScheduler(HPCConfig()).submit(
                make_job("true", workdir=str(tmp_path)), wait=True
            )
        assert [call.args[0][0] for call in mock.call_args_list] == ["sbatch", "sacct"]
        # 137 from sbatch; sacct alone would have said 9.
        assert result.exit_code == 137
