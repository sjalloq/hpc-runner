"""The Slurm backend: what it reads back (status, exit code, listings, details, cancel).

Every Slurm text comes from the captures in
tests/unit/fixtures/slurm/<flavour>/ (``slurm_fixtures``); each test runs
once per flavour. The waited submission is in test_slurm_wait.py. The
backend's clock is ``slurm_clock`` (tests/unit/conftest.py): no test sleeps.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from hpc_runner.core.config import HPCConfig
from hpc_runner.core.exceptions import JobNotFoundError
from hpc_runner.core.result import JobStatus
from hpc_runner.schedulers.slurm import SlurmScheduler
from hpc_runner.schedulers.slurm.parser import (
    SACCT_FORMAT,
    SQUEUE_FORMAT,
    expand_tasks,
    parse_duration,
    parse_exit_code,
    parse_time,
    state_to_status,
)
from tests.fakes import FakeClock, Responder, SlurmFixtures, patch_seam

S = JobStatus


@pytest.fixture
def fx(slurm_fixtures: SlurmFixtures) -> SlurmFixtures:
    return slurm_fixtures


@pytest.fixture
def scheduler() -> SlurmScheduler:
    return SlurmScheduler(HPCConfig())


def _sacct_argv(job_ids: str) -> list[str]:
    return ["sacct", "-P", "-n", "-j", job_ids, f"--format={SACCT_FORMAT}"]


def _in_turn(fx: SlurmFixtures, *, sacct: list[str], scontrol: list[str]) -> Responder:
    """Each command answers with the next of its texts, the last one from then on.

    A text is a capture's file name; ``""`` is ``sacct`` with no row, and
    ``scontrol`` of a job the controller does not know.
    """
    asked = {"sacct": 0, "scontrol": 0}

    def respond(argv: list[str]) -> tuple[int, str, str]:
        names = {"sacct": sacct, "scontrol": scontrol}[argv[0]]
        name = names[min(asked[argv[0]], len(names) - 1)]
        asked[argv[0]] += 1
        if name:
            return 0, fx.text(name), ""
        return fx.result(f"{argv[0]}_missing.json")

    return respond


def _commands(seam) -> list[str]:
    return [call.args[0][0] for call in seam.call_args_list]


def _stage(fx: SlurmFixtures, stage: str, *, controller: bool = True) -> Responder:
    """``sacct`` and ``scontrol show job`` as they answered at *stage* of a scenario.

    Without *controller*, ``scontrol`` answers as it does once the
    controller has forgotten the job.
    """

    def respond(argv: list[str]) -> tuple[int, str, str]:
        if argv[0] == "sacct":
            return 0, fx.text(f"sacct_{stage}.txt"), ""
        if argv[:3] == ["scontrol", "show", "job"]:
            if controller and f"scontrol_{stage}.txt" in fx.manifest["files"]:
                return 0, fx.text(f"scontrol_{stage}.txt"), ""
            return fx.result("scontrol_missing.json")
        raise AssertionError(argv)

    return respond


class TestFieldLists:
    def test_the_captures_were_taken_with_the_fields_the_backend_sends(self, fx):
        assert fx.manifest["sacct_format"] == SACCT_FORMAT
        assert fx.manifest["squeue_format"] == SQUEUE_FORMAT


class TestStates:
    @pytest.mark.parametrize(
        ("state", "status"),
        [
            ("PENDING", S.PENDING),
            ("REQUEUED", S.PENDING),
            ("REQUEUE_HOLD", S.PENDING),
            ("REQUEUE_FED", S.PENDING),
            ("RESV_DEL_HOLD", S.PENDING),
            ("RUNNING", S.RUNNING),
            ("CONFIGURING", S.RUNNING),
            ("COMPLETING", S.RUNNING),
            ("SIGNALING", S.RUNNING),
            ("STAGE_OUT", S.RUNNING),
            ("RESIZING", S.RUNNING),
            ("SUSPENDED", S.SUSPENDED),
            ("STOPPED", S.SUSPENDED),
            ("COMPLETED", S.COMPLETED),
            ("FAILED", S.FAILED),
            ("NODE_FAIL", S.FAILED),
            ("BOOT_FAIL", S.FAILED),
            ("SPECIAL_EXIT", S.FAILED),
            ("CANCELLED by 1003", S.CANCELLED),
            ("PREEMPTED", S.CANCELLED),
            ("REVOKED", S.CANCELLED),
            ("TIMEOUT", S.TIMEOUT),
            ("DEADLINE", S.TIMEOUT),
            ("OUT_OF_MEMORY", S.KILLED),
            ("CANCELLED", S.SKIPPED),
            ("SOMETHING_NEW", S.UNKNOWN),
            ("", S.UNKNOWN),
        ],
    )
    def test_the_table(self, state, status):
        assert state_to_status(state) is status

    @pytest.mark.parametrize(
        ("text", "code"),
        [("0:0", 0), ("1:0", 1), ("137:0", 137), ("0:9", 137), ("0:15", 143), ("0:125", 253)],
    )
    def test_exit_code_is_what_a_shell_reports(self, text, code):
        assert parse_exit_code(text) == code

    @pytest.mark.parametrize("text", ["", "None", "1", "a:b"])
    def test_no_exit_code(self, text):
        assert parse_exit_code(text) is None

    @pytest.mark.parametrize(
        ("spec", "tasks"),
        [
            ("3", (3,)),
            ("[1-4]", (1, 2, 3, 4)),
            ("[3-4%2]", (3, 4)),
            ("[1,3,7,9]", (1, 3, 7, 9)),
            ("[5]", (5,)),
            ("1-4", (1, 2, 3, 4)),
            ("3-4%2", (3, 4)),
        ],
    )
    def test_task_ids(self, spec, tasks):
        assert expand_tasks(spec) == tasks

    def test_stepped_task_ids(self):
        assert expand_tasks("[1-100:3]") == tuple(range(1, 101, 3))

    def test_times_and_durations(self):
        assert parse_time("2026-11-22T16:57:35") == datetime(2026, 11, 22, 16, 57, 35)
        for text in ("Unknown", "None", "N/A", ""):
            assert parse_time(text) is None
        assert parse_duration("0:04") == timedelta(seconds=4)
        assert parse_duration("00:01:03") == timedelta(minutes=1, seconds=3)
        assert parse_duration("1-02:03:04") == timedelta(days=1, hours=2, minutes=3, seconds=4)
        assert parse_duration("N/A") is None


#: stage, the scenario whose job it is, the status, the exit code while the
#: controller knows the job, and the exit code from ``sacct`` alone.
SCENARIOS = [
    ("ok", "ok", S.COMPLETED, 0, 0),
    ("exit1", "exit1", S.FAILED, 1, 1),
    # sacct prints 137 as 9; scontrol prints 137.
    ("exit137", "exit137", S.FAILED, 137, 9),
    ("signal", "signal", S.FAILED, 137, 137),
    ("timeout", "timeout", S.TIMEOUT, 143, 143),
    ("out_of_memory", "out_of_memory", S.KILLED, 253, 253),
    ("out_of_memory_step", "out_of_memory_step", S.COMPLETED, 0, 0),
    ("cancelled_pending", "cancelled_pending", S.CANCELLED, None, None),
    ("cancelled_running", "cancelled_running", S.CANCELLED, 143, 143),
    ("completing", "completing", S.CANCELLED, 143, 143),
    ("held", "held", S.PENDING, None, None),
    ("running", "running", S.RUNNING, None, None),
    ("suspended", "suspended", S.SUSPENDED, None, None),
    ("pending_resources", "pending_resources", S.PENDING, None, None),
    ("requeued", "requeued", S.PENDING, None, None),
    ("requeued_running", "requeued", S.RUNNING, None, None),
    ("node_fail_requeued", "node_fail_requeued", S.PENDING, None, None),
    ("dependent_ok_before", "dependent_ok", S.PENDING, None, None),
    ("dependent_ok", "dependent_ok", S.COMPLETED, 0, 0),
    ("dependent_skipped_before", "dependent_skipped", S.PENDING, None, None),
    ("dependent_skipped", "dependent_skipped", S.SKIPPED, None, None),
    ("dependent_pending_before", "dependent_pending", S.PENDING, None, None),
    ("dependent_pending", "dependent_pending", S.PENDING, None, None),
    ("dependent_pending_cancelled", "dependent_pending", S.CANCELLED, None, None),
    ("array_pending", "array", S.PENDING, None, None),
    ("array", "array", S.COMPLETED, 0, 0),
    ("array_partial", "array_partial", S.FAILED, 1, 1),
    ("array_throttled", "array_throttled", S.RUNNING, None, None),
    ("array_throttled_cancelled", "array_throttled", S.CANCELLED, 143, 143),
    ("array_cancelled_pending", "array_cancelled_pending", S.CANCELLED, None, None),
    ("array_list", "array_list", S.PENDING, None, None),
    ("array_stepped", "array_stepped", S.PENDING, None, None),
    ("array_single", "array_single", S.PENDING, None, None),
    ("wait_ok", "wait_ok", S.COMPLETED, 0, 0),
    ("wait_exit1", "wait_exit1", S.FAILED, 1, 1),
    ("wait_exit137", "wait_exit137", S.FAILED, 137, 9),
    ("wait_out_of_memory", "wait_out_of_memory", S.KILLED, 253, 253),
    ("wait_cancelled_pending", "wait_cancelled_pending", S.CANCELLED, None, None),
    ("wait_cancelled_running", "wait_cancelled_running", S.CANCELLED, 143, 143),
    ("wait_dependent_skipped", "wait_dependent_skipped", S.SKIPPED, None, None),
    ("wait_array_partial", "wait_array_partial", S.FAILED, 1, 1),
]


class TestStatusAndExitCode:
    @pytest.mark.parametrize(("stage", "scenario", "status", "code", "sacct_code"), SCENARIOS)
    def test_every_scenario(self, scheduler, fx, stage, scenario, status, code, sacct_code):
        job_id = fx.job(scenario)
        with patch_seam(_stage(fx, stage)):
            assert scheduler.get_status(job_id) is status
            assert scheduler.get_exit_code(job_id) == code
        with patch_seam(_stage(fx, stage, controller=False)):
            assert scheduler.get_exit_code(job_id) == sacct_code

    def test_a_lost_node(self, scheduler, fx):
        """FAILED on both versions; the rows differ, and so does the code they give.

        24.11: ``scontrol`` says ``0:1`` and ``sacct`` ``1:0``. 22.05: both
        say ``0:0`` and the ``.batch`` row has no exit code.
        """
        job_id = fx.job("node_fail")
        expected = {"24.11": (129, 1), "22.05": (None, None)}[fx.flavour]
        with patch_seam(_stage(fx, "node_fail")):
            assert scheduler.get_status(job_id) is S.FAILED
            assert scheduler.get_exit_code(job_id) == expected[0]
        with patch_seam(_stage(fx, "node_fail", controller=False)):
            assert scheduler.get_exit_code(job_id) == expected[1]

    def test_the_early_rows_of_22_05_are_read_as_they_stand(self, scheduler, fx):
        if fx.flavour != "22.05":
            pytest.skip("only 22.05 has shown the early row")
        with patch_seam(_stage(fx, "ok_stub")):
            assert scheduler.get_status(fx.job("ok_stub")) is S.COMPLETED
            assert scheduler.get_exit_code(fx.job("ok_stub")) == 0
        # The job's own row says PENDING while its .batch row says RUNNING.
        with patch_seam(_stage(fx, "running_stub")):
            assert scheduler.get_status(fx.job("running_stub")) is S.PENDING
            assert scheduler.get_exit_code(fx.job("running_stub")) is None

    def test_a_job_sacct_does_not_have_is_unknown(self, scheduler, fx):
        def respond(argv):
            return fx.result(
                "sacct_missing.json" if argv[0] == "sacct" else "scontrol_missing.json"
            )

        with patch_seam(respond):
            assert scheduler.get_status("999999") is S.UNKNOWN
            assert scheduler.get_exit_code("999999") is None

    def test_one_sacct_for_several_jobs_and_no_x(self, scheduler, fx):
        ids = [fx.job("ok"), fx.job("exit1"), fx.job("array_partial")]
        with patch_seam(lambda argv: (0, fx.text("sacct_several.txt"), "")) as seam:
            statuses = scheduler.get_statuses([*ids, "999999"])
        assert statuses == {
            ids[0]: S.COMPLETED,
            ids[1]: S.FAILED,
            ids[2]: S.FAILED,
            "999999": S.UNKNOWN,
        }
        first, *again = (call.args[0] for call in seam.call_args_list)
        assert first == [
            "sacct",
            "-P",
            "-n",
            "-j",
            ",".join([*ids, "999999"]),
            f"--format={SACCT_FORMAT}",
        ]
        # The id with no row is asked for again, alone (TestAskingAgain).
        assert again == [_sacct_argv("999999")] * 10

    def test_tasks_are_asked_by_the_id_of_their_array(self, scheduler, fx):
        base = fx.job("array_throttled")
        tasks = [f"{base}_{n}" for n in (1, 2, 3, 4)]
        with patch_seam(_stage(fx, "array_throttled")) as seam:
            statuses = scheduler.get_statuses(tasks)
        # Tasks 3 and 4 have not started: they share the row <id>_[3-4%2].
        assert [statuses[t] for t in tasks] == [S.RUNNING, S.RUNNING, S.PENDING, S.PENDING]
        assert seam.call_args.args[0][3:5] == ["-j", base]

    def test_each_task_has_its_own_status_and_code(self, scheduler, fx):
        base = fx.job("array_partial")
        with patch_seam(_stage(fx, "array_partial")):
            assert [scheduler.get_status(f"{base}_{n}") for n in (1, 2, 3)] == [
                S.COMPLETED,
                S.COMPLETED,
                S.FAILED,
            ]
            assert [scheduler.get_exit_code(f"{base}_{n}") for n in (1, 2, 3)] == [0, 0, 1]
            assert scheduler.get_status(f"{base}_4") is S.UNKNOWN

    def test_tasks_written_as_a_list_or_with_a_step(self, scheduler, fx):
        listed = fx.job("array_list")
        with patch_seam(_stage(fx, "array_list")):
            statuses = scheduler.get_statuses([f"{listed}_{n}" for n in (1, 2, 3, 7, 9)])
        assert list(statuses.values()) == [S.PENDING, S.UNKNOWN, S.PENDING, S.PENDING, S.PENDING]
        stepped = fx.job("array_stepped")
        with patch_seam(_stage(fx, "array_stepped")):
            statuses = scheduler.get_statuses([f"{stepped}_{n}" for n in (1, 2, 4, 100, 101)])
        assert list(statuses.values()) == [S.PENDING, S.UNKNOWN, S.PENDING, S.PENDING, S.UNKNOWN]

    def test_one_command_when_the_controller_settles_the_code(self, scheduler, fx):
        with patch_seam(_stage(fx, "exit137")) as seam:
            assert scheduler.get_exit_code(fx.job("exit137")) == 137
        assert [call.args[0][0] for call in seam.call_args_list] == ["scontrol"]
        with patch_seam(_stage(fx, "running")) as seam:
            assert scheduler.get_exit_code(fx.job("running")) is None
        assert [call.args[0][0] for call in seam.call_args_list] == ["scontrol"]

    def test_sacct_is_asked_when_the_controller_does_not_settle_it(self, scheduler, fx):
        for stage in ("cancelled_pending", "array"):
            with patch_seam(_stage(fx, stage)) as seam:
                scheduler.get_exit_code(fx.job(stage))
            assert [call.args[0][0] for call in seam.call_args_list] == ["scontrol", "sacct"]


class TestAskingAgain:
    """A new job reaches ``sacct`` a moment after the queue, and a job just
    ended is ``COMPLETING`` to the controller for a moment: the same command
    is asked again after a sleep."""

    def test_sacct_with_no_row_answers_nothing(self, fx):
        assert fx.result("sacct_missing.json") == (0, "", "")

    def test_a_row_that_comes_on_the_second_ask(self, scheduler, fx, slurm_clock: FakeClock):
        ok, late = fx.job("ok"), fx.job("exit1")
        respond = _in_turn(fx, sacct=["sacct_ok.txt", "sacct_exit1.txt"], scontrol=[])
        with patch_seam(respond) as seam:
            statuses = scheduler.get_statuses([ok, late])
        assert statuses == {ok: S.COMPLETED, late: S.FAILED}
        assert [call.args[0] for call in seam.call_args_list] == [
            _sacct_argv(f"{ok},{late}"),
            _sacct_argv(late),
        ]
        assert slurm_clock.sleeps == [1.0]

    def test_a_row_that_never_comes_is_unknown_after_ten_seconds(
        self, scheduler, fx, slurm_clock: FakeClock
    ):
        ok = fx.job("ok")
        with patch_seam(_in_turn(fx, sacct=["sacct_ok.txt", ""], scontrol=[])) as seam:
            statuses = scheduler.get_statuses([ok, "999999"])
        assert statuses == {ok: S.COMPLETED, "999999": S.UNKNOWN}
        assert [call.args[0] for call in seam.call_args_list] == [
            _sacct_argv(f"{ok},999999"),
            *([_sacct_argv("999999")] * 10),
        ]
        assert slurm_clock.sleeps == [1.0] * 10

    def test_one_sacct_when_every_job_has_a_row(self, scheduler, fx, slurm_clock: FakeClock):
        ids = [fx.job("ok"), fx.job("exit1"), fx.job("array_partial")]
        with patch_seam(lambda argv: (0, fx.text("sacct_several.txt"), "")) as seam:
            statuses = scheduler.get_statuses(ids)
        assert list(statuses.values()) == [S.COMPLETED, S.FAILED, S.FAILED]
        assert seam.call_count == 1
        assert slurm_clock.sleeps == []

    def test_details_never_wait_for_a_row(self, scheduler, fx, slurm_clock: FakeClock):
        """No row yet: the status is the controller's, and nothing is asked twice."""
        job_id = fx.job("held")
        respond = _in_turn(fx, sacct=[""], scontrol=["scontrol_held.txt"])
        with patch_seam(respond) as seam:
            info, extra = scheduler.get_job_details(job_id)
        assert (info.job_id, info.name, info.status) == (job_id, "held", S.PENDING)
        assert extra["state"] == "PENDING"
        assert _commands(seam) == ["scontrol", "sacct"]
        assert slurm_clock.sleeps == []

    def test_a_job_neither_command_knows_is_not_waited_for(
        self, scheduler, fx, slurm_clock: FakeClock
    ):
        with (
            patch_seam(_in_turn(fx, sacct=[""], scontrol=[""])) as seam,
            pytest.raises(JobNotFoundError, match="job 999999 not found"),
        ):
            scheduler.get_job_details("999999")
        assert _commands(seam) == ["scontrol", "sacct"]
        assert slurm_clock.sleeps == []

    def test_the_capture_of_a_job_just_cancelled_says_completing(self, fx):
        assert "JobState=COMPLETING" in fx.text("scontrol_completing_just_cancelled.txt")
        assert "JobState=CANCELLED" in fx.text("scontrol_completing.txt")

    def test_the_exit_code_of_a_job_that_is_completing(self, scheduler, fx, slurm_clock: FakeClock):
        respond = _in_turn(
            fx,
            sacct=[],
            scontrol=["scontrol_completing_just_cancelled.txt", "scontrol_completing.txt"],
        )
        with patch_seam(respond) as seam:
            assert scheduler.get_exit_code(fx.job("completing")) == 143
        assert _commands(seam) == ["scontrol", "scontrol"]
        assert slurm_clock.sleeps == [0.5]

    def test_the_details_of_a_job_that_is_completing(self, scheduler, fx, slurm_clock: FakeClock):
        """Details never wait: the code is read from the ``COMPLETING`` record."""
        respond = _in_turn(
            fx,
            sacct=["sacct_completing_just_cancelled.txt"],
            scontrol=["scontrol_completing_just_cancelled.txt"],
        )
        with patch_seam(respond) as seam:
            info, _ = scheduler.get_job_details(fx.job("completing"))
        assert (info.status, info.exit_code) == (S.CANCELLED, 143)
        assert _commands(seam) == ["scontrol", "sacct"]
        assert slurm_clock.sleeps == []

    def test_completing_and_then_forgotten_is_sacct_to_answer(
        self, scheduler, fx, slurm_clock: FakeClock
    ):
        respond = _in_turn(
            fx,
            sacct=["sacct_completing.txt"],
            scontrol=["scontrol_completing_just_cancelled.txt", ""],
        )
        with patch_seam(respond) as seam:
            assert scheduler.get_exit_code(fx.job("completing")) == 143
        assert _commands(seam) == ["scontrol", "scontrol", "sacct"]
        assert slurm_clock.sleeps == [0.5]

    def test_still_completing_after_five_seconds_has_no_exit_code(
        self, scheduler, fx, slurm_clock: FakeClock
    ):
        respond = _in_turn(fx, sacct=[], scontrol=["scontrol_completing_just_cancelled.txt"])
        with patch_seam(respond) as seam:
            assert scheduler.get_exit_code(fx.job("completing")) is None
        assert _commands(seam) == ["scontrol"] * 11
        assert slurm_clock.sleeps == [0.5] * 10

    @pytest.mark.parametrize("scenario", ["running", "held"])
    def test_a_running_or_pending_job_is_not_waited_for(
        self, scheduler, fx, slurm_clock: FakeClock, scenario
    ):
        with patch_seam(_stage(fx, scenario)) as seam:
            assert scheduler.get_exit_code(fx.job(scenario)) is None
        assert _commands(seam) == ["scontrol"]
        assert slurm_clock.sleeps == []


class TestActiveListing:
    def test_the_snapshot(self, scheduler, fx):
        with patch_seam(lambda argv: (0, fx.text("squeue_all.txt"), "")) as seam:
            jobs = scheduler.list_active_jobs(user="tester")
        assert seam.call_args.args[0] == ["squeue", "-h", "-u", "tester", "-o", SQUEUE_FORMAT]
        by_name = {job.name: job for job in jobs}
        assert {job.job_id: job.name for job in jobs} == {
            fx.job(name): name
            for name in ("pending_resources", "held", "filler", "running", "suspended")
        }
        assert {name: (j.status, j.queue, j.node, j.cpu) for name, j in by_name.items()} == {
            "pending_resources": (S.PENDING, "all", None, 2),
            "held": (S.PENDING, "all", None, 1),
            "filler": (S.RUNNING, "all", "exec2", 2),
            "running": (S.RUNNING, "all", "exec1", 1),
            "suspended": (S.SUSPENDED, "all", "exec1", 1),
        }
        for job in jobs:
            assert job.user == "tester"
            assert job.working_dir == Path("/work/capture-jobs")
            assert job.submit_time is not None
            assert job.array_task_id is None
            started = job.status in (S.RUNNING, S.SUSPENDED)
            assert (job.start_time is not None) is started
            assert (job.runtime is not None) is started

    def test_all_users_and_the_filters(self, scheduler, fx):
        with patch_seam(lambda argv: (0, fx.text("squeue_all.txt"), "")) as seam:
            pending = scheduler.list_active_jobs(status={S.PENDING})
            assert scheduler.list_active_jobs(queue="short") == []
            assert len(scheduler.list_active_jobs(queue="all")) == 5
        assert seam.call_args.args[0] == ["squeue", "-h", "-o", SQUEUE_FORMAT]
        assert sorted(job.name for job in pending) == ["held", "pending_resources"]

    def test_nothing_queued(self, scheduler, fx):
        with patch_seam(lambda argv: (0, fx.text("squeue_empty.txt"), "")):
            assert scheduler.list_active_jobs() == []

    def test_time_used(self, scheduler, fx):
        text = fx.text("squeue_running.txt")
        used = text.split("|")[11]
        minutes, seconds = used.split(":")
        with patch_seam(lambda argv: (0, text, "")):
            (job,) = scheduler.list_active_jobs()
        assert job.runtime == timedelta(minutes=int(minutes), seconds=int(seconds))
        assert job.start_time == parse_time(text.split("|")[9])

    def test_an_array_is_one_entry_under_its_own_id(self, scheduler, fx):
        """Two running tasks and the row of the two waiting ones: RUNNING, on a node."""
        with patch_seam(lambda argv: (0, fx.text("squeue_array_throttled.txt"), "")):
            (job,) = scheduler.list_active_jobs()
        assert job.job_id == fx.job("array_throttled")
        assert job.name == "array_throttled"
        assert job.status is S.RUNNING
        assert job.node in ("exec1", "exec2")
        assert job.array_task_id in (1, 2)

    def test_a_pending_array(self, scheduler, fx):
        with patch_seam(lambda argv: (0, fx.text("squeue_array_pending.txt"), "")):
            (job,) = scheduler.list_active_jobs()
        assert (job.job_id, job.status, job.node, job.array_task_id) == (
            fx.job("array"),
            S.PENDING,
            None,
            None,
        )

    def test_a_pending_jobs_start_time_is_not_read(self, scheduler, fx):
        """A requeued job is PENDING with the time Slurm expects to start it."""
        text = fx.text("squeue_requeued.txt")
        assert parse_time(text.split("|")[9]) is not None
        with patch_seam(lambda argv: (0, text, "")):
            (job,) = scheduler.list_active_jobs()
        assert (job.status, job.start_time, job.runtime) == (S.PENDING, None, None)


class TestHistory:
    def _history(self, scheduler, fx, **filters):
        since = datetime.strptime(fx.manifest["history_since"], "%Y-%m-%dT%H:%M:%S")
        with patch_seam(lambda argv: (0, fx.text("sacct_history.txt"), "")) as seam:
            jobs = scheduler.list_completed_jobs(since=since, **filters)
        return jobs, seam.call_args.args[0]

    def test_it_is_true(self, scheduler):
        assert scheduler.has_accounting() is True

    def test_the_command_is_the_one_the_capture_was_taken_with(self, scheduler, fx):
        _, argv = self._history(scheduler, fx, user="tester")
        assert argv == fx.manifest["files"]["sacct_history.txt"]["argv"]
        assert argv == [
            "sacct",
            "-P",
            "-n",
            "-u",
            "tester",
            "-S",
            fx.manifest["history_since"],
            f"--format={SACCT_FORMAT}",
        ]

    def test_all_users_and_until(self, scheduler):
        with patch_seam(lambda argv: (0, "", "")) as seam:
            jobs = scheduler.list_completed_jobs(
                since=datetime(2026, 1, 2, 3, 4, 5), until=datetime(2026, 1, 3)
            )
        assert jobs == []
        assert seam.call_args.args[0] == [
            "sacct",
            "-P",
            "-n",
            "-a",
            "-S",
            "2026-01-02T03:04:05",
            "-E",
            "2026-01-03T00:00:00",
            f"--format={SACCT_FORMAT}",
        ]

    def test_finished_jobs_with_their_status_and_code(self, scheduler, fx):
        jobs, _ = self._history(scheduler, fx, user="tester", limit=1000)
        by_id = {job.job_id: job for job in jobs}
        shown = {
            name: (by_id[fx.job(name)].status, by_id[fx.job(name)].exit_code)
            for name in (
                "ok",
                "exit1",
                "exit137",
                "signal",
                "timeout",
                "out_of_memory",
                "out_of_memory_step",
                "cancelled_running",
                "dependent_ok",
            )
        }
        assert shown == {
            "ok": (S.COMPLETED, 0),
            "exit1": (S.FAILED, 1),
            "exit137": (S.FAILED, 9),  # sacct's own number for 137
            "signal": (S.FAILED, 137),
            "timeout": (S.TIMEOUT, 143),
            "out_of_memory": (S.KILLED, 253),
            "out_of_memory_step": (S.COMPLETED, 0),
            "cancelled_running": (S.CANCELLED, 143),
            "dependent_ok": (S.COMPLETED, 0),
        }
        ok = by_id[fx.job("ok")]
        assert (ok.name, ok.user, ok.queue, ok.cpu) == ("ok", "tester", "all", 1)
        assert ok.node in ("exec1", "exec2")
        assert ok.end_time is not None and ok.runtime is not None

    def test_jobs_that_have_not_ended_are_left_out(self, scheduler, fx):
        jobs, _ = self._history(scheduler, fx, limit=1000)
        names = {job.name for job in jobs}
        text = fx.text("sacct_history.txt")
        for live in ("running", "suspended", "filler", "pending_resources"):
            assert f"|{live}\n" in text
            assert live not in names
        assert all(job.status.is_terminal for job in jobs)

    def test_a_job_that_was_never_eligible_to_run_is_not_in_what_sacct_gives(self, fx):
        """Held or waiting on a dependency until it was cancelled: ``sacct -S`` has no row."""
        text = fx.text("sacct_history.txt")
        for scenario in ("cancelled_pending", "dependent_skipped", "array_cancelled_pending"):
            assert f"|{scenario}\n" not in text

    def test_each_array_task_is_an_entry(self, scheduler, fx):
        jobs, _ = self._history(scheduler, fx, limit=1000)
        base = fx.job("array_partial")
        tasks = {job.job_id: job for job in jobs if job.job_id.startswith(f"{base}_")}
        assert {
            job_id: (job.array_task_id, job.status, job.exit_code) for job_id, job in tasks.items()
        } == {
            f"{base}_1": (1, S.COMPLETED, 0),
            f"{base}_2": (2, S.COMPLETED, 0),
            f"{base}_3": (3, S.FAILED, 1),
        }

    def test_most_recent_first_and_the_limit(self, scheduler, fx):
        jobs, _ = self._history(scheduler, fx, limit=1000)
        ends = [job.end_time for job in jobs]
        assert None not in ends
        assert ends == sorted(ends, reverse=True)
        few, _ = self._history(scheduler, fx, limit=3)
        assert [job.job_id for job in few] == [job.job_id for job in jobs[:3]]

    def test_exit_code_and_queue_filters(self, scheduler, fx):
        jobs, _ = self._history(scheduler, fx, exit_code=137, limit=1000)
        assert [job.name for job in jobs] == ["signal"]
        assert self._history(scheduler, fx, queue="short")[0] == []


class TestDetails:
    def test_a_finished_job_the_controller_knows(self, scheduler, fx):
        job_id = fx.job("ok")
        with patch_seam(_stage(fx, "ok")) as seam:
            info, extra = scheduler.get_job_details(job_id)
        assert [call.args[0][0] for call in seam.call_args_list] == ["scontrol", "sacct"]
        assert seam.call_args_list[0].args[0] == ["scontrol", "show", "job", job_id]
        assert (info.job_id, info.name, info.user, info.status, info.exit_code) == (
            job_id,
            "ok",
            "tester",
            S.COMPLETED,
            0,
        )
        assert (info.queue, info.cpu, info.array_task_id) == ("all", 1, None)
        assert info.node in ("exec1", "exec2")
        assert info.working_dir == Path("/work/capture-jobs")
        assert info.stdout_path == Path(f"/work/capture-jobs/ok.{job_id}.out")
        assert info.stderr_path is None  # the same file as stdout
        assert info.submit_time is not None and info.end_time is not None
        assert extra == {
            "state": "COMPLETED",
            "project": "proj",
            "command": "/work/capture-jobs/ok.sh",
        }

    def test_a_job_the_controller_has_forgotten_has_no_output_path(self, scheduler, fx):
        with patch_seam(_stage(fx, "ok", controller=False)):
            info, extra = scheduler.get_job_details(fx.job("ok"))
        assert (info.status, info.exit_code, info.name) == (S.COMPLETED, 0, "ok")
        assert (info.stdout_path, info.stderr_path) == (None, None)
        assert extra == {"state": "COMPLETED", "project": "proj"}

    def test_the_exit_code_is_the_controllers_while_it_knows_the_job(self, scheduler, fx):
        with patch_seam(_stage(fx, "exit137")):
            assert scheduler.get_job_details(fx.job("exit137"))[0].exit_code == 137
        with patch_seam(_stage(fx, "exit137", controller=False)):
            assert scheduler.get_job_details(fx.job("exit137"))[0].exit_code == 9

    def test_out_of_memory_is_killed_and_slurms_state_is_kept(self, scheduler, fx):
        with patch_seam(_stage(fx, "out_of_memory")):
            info, extra = scheduler.get_job_details(fx.job("out_of_memory"))
        assert (info.status, info.exit_code) == (S.KILLED, 253)
        assert (extra["state"], extra["reason"]) == ("OUT_OF_MEMORY", "OutOfMemory")
        with patch_seam(_stage(fx, "out_of_memory", controller=False)):
            _, extra = scheduler.get_job_details(fx.job("out_of_memory"))
        assert extra == {"state": "OUT_OF_MEMORY", "project": "proj"}

    def test_a_skipped_dependent(self, scheduler, fx):
        with patch_seam(_stage(fx, "dependent_skipped")):
            info, extra = scheduler.get_job_details(fx.job("dependent_skipped"))
        assert (info.status, info.exit_code, info.node) == (S.SKIPPED, None, None)
        assert extra == {
            "state": "CANCELLED",
            "project": "proj",
            "reason": "DependencyNeverSatisfied",
            "dependencies": f"afterok:{fx.job('dependent_skipped_parent')}(failed)",
            "command": "/work/capture-jobs/dependent_skipped.sh",
        }

    def test_a_cancelled_job_names_who(self, scheduler, fx):
        with patch_seam(_stage(fx, "cancelled_running")):
            info, extra = scheduler.get_job_details(fx.job("cancelled_running"))
        assert (info.status, info.exit_code) == (S.CANCELLED, 143)
        assert extra["state"] == "CANCELLED by 1003"

    def test_a_running_job(self, scheduler, fx):
        job_id = fx.job("running")
        with patch_seam(_stage(fx, "running")):
            info, extra = scheduler.get_job_details(job_id)
        assert (info.status, info.exit_code, info.node) == (S.RUNNING, None, "exec1")
        assert info.start_time is not None and info.end_time is None
        assert info.stdout_path == Path(f"/work/capture-jobs/running.{job_id}.out")
        assert extra["state"] == "RUNNING"

    def test_a_finished_array_and_its_tasks(self, scheduler, fx):
        base = fx.job("array_partial")
        with patch_seam(_stage(fx, "array_partial")):
            info, extra = scheduler.get_job_details(base)
        assert (info.job_id, info.name, info.status, info.exit_code) == (
            base,
            "array_partial",
            S.FAILED,
            1,
        )
        assert info.stdout_path is None
        tasks = extra["tasks"]
        assert isinstance(tasks, list)
        assert [
            (t.job_id, t.array_task_id, t.status, t.exit_code, t.stdout_path) for t in tasks
        ] == [
            (
                f"{base}_1",
                1,
                S.COMPLETED,
                0,
                Path(f"/work/capture-jobs/array_partial.{base}.1.out"),
            ),
            (
                f"{base}_2",
                2,
                S.COMPLETED,
                0,
                Path(f"/work/capture-jobs/array_partial.{base}.2.out"),
            ),
            (f"{base}_3", 3, S.FAILED, 1, Path(f"/work/capture-jobs/array_partial.{base}.3.out")),
        ]
        assert extra["state"] == "FAILED"

    def test_one_task_by_its_own_id(self, scheduler, fx):
        base = fx.job("array_partial")
        with patch_seam(_stage(fx, "array_partial")):
            info, extra = scheduler.get_job_details(f"{base}_3")
        assert (info.job_id, info.array_task_id, info.status, info.exit_code) == (
            f"{base}_3",
            3,
            S.FAILED,
            1,
        )
        assert info.stdout_path == Path(f"/work/capture-jobs/array_partial.{base}.3.out")
        assert "tasks" not in extra

    @pytest.mark.parametrize(
        ("stage", "scenario", "indices"),
        [
            ("array_pending", "array", [1, 2, 3, 4]),
            ("array_single", "array_single", [5]),
            ("array_cancelled_pending", "array_cancelled_pending", [1, 2, 3, 4]),
        ],
    )
    def test_tasks_that_never_started_have_no_output_path(
        self, scheduler, fx, stage, scenario, indices
    ):
        """The controller prints 4294967294 where the task id would be."""
        assert "4294967294" in fx.text(f"scontrol_{stage}.txt")
        base = fx.job(scenario)
        with patch_seam(_stage(fx, stage)):
            info, extra = scheduler.get_job_details(base)
        tasks = extra["tasks"]
        assert isinstance(tasks, list)
        assert [t.job_id for t in tasks] == [f"{base}_{n}" for n in indices]
        assert [t.array_task_id for t in tasks] == indices
        assert info.stdout_path is None
        assert {t.stdout_path for t in tasks} == {None}

    def test_an_array_with_started_and_waiting_tasks(self, scheduler, fx):
        base = fx.job("array_throttled")
        with patch_seam(_stage(fx, "array_throttled")):
            info, extra = scheduler.get_job_details(base)
        assert (info.status, info.exit_code) == (S.RUNNING, None)
        tasks = extra["tasks"]
        assert isinstance(tasks, list)
        assert [(t.job_id, t.status, t.stdout_path) for t in tasks] == [
            (f"{base}_1", S.RUNNING, Path(f"/work/capture-jobs/array_throttled.{base}.1.out")),
            (f"{base}_2", S.RUNNING, Path(f"/work/capture-jobs/array_throttled.{base}.2.out")),
            (f"{base}_3", S.PENDING, None),
            (f"{base}_4", S.PENDING, None),
        ]

    def test_a_job_neither_command_knows(self, scheduler, fx):
        def respond(argv):
            return fx.result(
                "sacct_missing.json" if argv[0] == "sacct" else "scontrol_missing.json"
            )

        with patch_seam(respond), pytest.raises(JobNotFoundError, match="job 999999 not found"):
            scheduler.get_job_details("999999")

    def test_a_job_accounting_does_not_have_yet(self, scheduler, fx, slurm_clock: FakeClock):
        """The controller knows it and sacct does not yet: the controller's
        status, with each command asked once."""
        job_id = fx.job("running")

        def respond(argv):
            if argv[0] == "sacct":
                return fx.result("sacct_missing.json")
            return 0, fx.text("scontrol_running.txt"), ""

        with patch_seam(respond) as seam:
            info, extra = scheduler.get_job_details(job_id)
        assert _commands(seam) == ["scontrol", "sacct"]
        assert slurm_clock.sleeps == []
        assert (info.job_id, info.name, info.user, info.status) == (
            job_id,
            "running",
            "tester",
            S.RUNNING,
        )
        assert (info.queue, info.node, info.cpu) == ("all", "exec1", 1)
        assert info.stdout_path == Path(f"/work/capture-jobs/running.{job_id}.out")
        assert extra == {
            "state": "RUNNING",
            "project": "proj",
            "command": "/work/capture-jobs/running.sh",
        }

    def test_the_early_row_of_22_05(self, scheduler, fx):
        if fx.flavour != "22.05":
            pytest.skip("only 22.05 has shown the early row")
        with patch_seam(_stage(fx, "ok_stub")):
            info, _ = scheduler.get_job_details(fx.job("ok_stub"))
        assert (info.name, info.queue, info.status, info.exit_code) == (
            "allocation",
            None,
            S.COMPLETED,
            0,
        )


class TestCancel:
    @pytest.mark.parametrize(
        "scenario",
        [
            "cancelled_pending",
            "cancelled_running",
            "array_cancelled_pending",
            "array_throttled",
            "dependent_pending",
        ],
    )
    def test_scancel_of_the_job_id(self, scheduler, fx, scenario):
        captured = fx.result(f"scancel_{scenario}.json")
        assert captured == (0, "", "")
        with patch_seam(lambda argv: captured) as seam:
            assert scheduler.cancel(fx.job(scenario)) is True
        assert seam.call_args.args[0] == ["scancel", fx.job(scenario)]

    def test_an_id_that_never_existed_is_not_refused(self, scheduler, fx):
        captured = fx.result("scancel_missing.json")
        assert captured == (0, "", "")
        with patch_seam(lambda argv: captured):
            assert scheduler.cancel("999999") is True

    def test_somebody_elses_job_is_refused(self, scheduler, fx):
        returncode, stdout, stderr = fx.result("scancel_not_owner.json")
        assert returncode == 210 and stdout == ""
        assert stderr.endswith("Access/permission denied\n")
        with patch_seam(lambda argv: (returncode, stdout, stderr)):
            assert scheduler.cancel("12") is False
