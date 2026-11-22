# Job lifecycle matrix: Slurm states against readers

Every state a job can be in on Slurm against every piece of code that
reads job state, with the test behind each cell. The SGE table is
`../test-review/LIFECYCLE_MATRIX.md`. Written 2026-11-22 from the test
files and the captures as they stood at `58b8e3f`.

Marks:

- **F** unit test on a capture from the testbed
  (`tests/unit/fixtures/slurm/<flavour>/`; each such test runs on 24.11
  and on 22.05).
- **u** unit test on hand-typed data. It pins the mapping, not Slurm's
  words.
- **I** integration test in the testbed, passed on 24.11 and 22.05
  (`testbed/slurm/README.md`, "Support matrix").
- **c** a capture of this state exists and no test reads it through this
  reader.
- **·** nothing: no test and no capture.
- **n/a** the reader cannot see this state.

## 1. Readers

| Col | What the user runs | Code | Slurm command |
|---|---|---|---|
| S | `result.status`, every reader below | `get_statuses` / `get_status` | `sacct -P -n -j` (no `-X`) |
| X | `result.returncode` | `get_exit_code` | `scontrol show job`, then `sacct` |
| L | `hpc status` | `list_active_jobs` | `squeue -h -o` |
| D | `hpc status <id>` | `get_job_details` | `scontrol show job` and `sacct` |
| H | `hpc status --history` | `list_completed_jobs` | `sacct -S` |
| W | `result.wait()` | S, polled | |
| V | `hpc run --batch --wait`, `submit(job, wait=True)` | `_sbatch_wait`, then W | `sbatch --wait` |
| M | `hpc monitor` listing | L, shown by `JobTable` | |

- L, D and H cells name a test of the scheduler method unless they say
  "CLI": then the test runs the `hpc` command itself.
- W in an active state means `wait()` was seen not to return in it.
- Unit test names are in `tests/unit/test_schedulers/test_slurm_reading.py`
  unless a file is given. `test_every_scenario` is the table `SCENARIOS`
  there: status, exit code with the controller, exit code from `sacct`
  alone.
- In column V, `[ok]`, `[exit1]` and the like are the cases of
  `test_the_exit_code_is_the_submit_commands_and_the_status_is_read_after`
  (`test_waited_submission.py`) and of `test_batch_wait_over_captured_jobs`
  (`test_cli/test_run.py`), which read the `sbatch_wait_<scenario>.json`
  captures.
- The mapping of a state name to a status (`state_to_status`) is pinned
  for every name by `TestStates.test_the_table` (u), and by `tests/unit/test_docs.py`
  against the table in the user docs.

## 2. The matrix

| # | State | hpc-runner status | Capture | S | X | L | D | H | W | V | M |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Pending for resources | PENDING | `pending_resources` | F, I (`test_licences_devices_and_features`) | F: none | F (`test_the_snapshot`) | · | F: left out (`test_jobs_that_have_not_ended_are_left_out`) | · | n/a | F (`TestJobTableOnEveryScheduler`) |
| 2 | Held (`--hold`) | PENDING | `held` | F, I (`test_queued_running_done`) | F: none | F (`test_the_snapshot`); I, CLI (`test_hold_flag_forwards`, `test_status_active_history_and_details`) | · | c: no row in `sacct_history.txt` | · | n/a | F |
| 3 | Waiting on a dependency, parent not finished | PENDING | `dependent_ok_before`, `dependent_skipped_before`, `dependent_pending_before` | F | F: none | c (`squeue_dependent_*_before.txt`) | · | · | · | n/a | · |
| 4 | Dependency can never be met, not killed (`kill_invalid_dependents = false`) | PENDING | `dependent_pending` | F, I (`test_without_kill_invalid_dependents_the_child_stays_pending`) | F, I: none | c (`squeue_dependent_pending.txt`) | · | · | · | · | · |
| 5 | Running | RUNNING | `running` | F, I | F: none | F (`test_the_snapshot`, `test_time_used`), I (`test_two_users`) | F (`test_a_running_job`), I (`test_two_users`, `test_details_of_another_users_job_have_state_project_and_command`) | F: left out | I (`test_array_1_to_5` waits from submission to the end) | n/a | F |
| 6 | Early `sacct` row, 22.05 only: a running job's own row says `PENDING` | PENDING for those seconds | `running_stub`, `ok_stub` | F (`test_the_early_rows_of_22_05_are_read_as_they_stand`) | F | n/a | F (`test_the_early_row_of_22_05`, the finished one) | · | · | · | n/a |
| 7 | Known to the controller, no `sacct` row yet | UNKNOWN | `scontrol_running.txt` with `sacct_missing.json` | F (`test_a_job_sacct_does_not_have_is_unknown`) | F: none | n/a | F (`test_a_job_accounting_does_not_have_yet`) | n/a | u (`test_core/test_result.py`, `unknown_after`; no Slurm text) | · | n/a |
| 8 | Suspended (`scontrol suspend`) | SUSPENDED | `suspended` | F, I (`test_suspended_job_is_waited_through`) | F: none | F (`test_the_snapshot`) | · | F: left out | I (`test_suspended_job_is_waited_through`) | n/a | F (`SUSPEND`) |
| 9 | Requeued (`scontrol requeue`), then running again | PENDING, then RUNNING | `requeued`, `requeued_running` | F, I (`test_requeued_job_is_waited_through_to_its_second_run`) | F, I: none | F (`test_a_pending_jobs_start_time_is_not_read`) | · | I, CLI: listed once, COMPLETED | I | · | · |
| 10 | Node lost, requeued by Slurm | PENDING | `node_fail_requeued` | F | F: none | c (`squeue_node_fail_requeued.txt`) | · | · | · | · | · |
| 11 | Completed | COMPLETED | `ok` | F, I | F, I: 0 | n/a | F (`test_a_finished_job_the_controller_knows`, `test_a_job_the_controller_has_forgotten_has_no_output_path`) | F (`test_finished_jobs_with_their_status_and_code`), I (`test_two_users`; CLI `test_tool_alias_and_config_resolve`) | I (`test_suspended_job_is_waited_through`, `test_array_concurrency_limit_is_accepted`) | F (`test_waited_submission.py` `[ok]`, `test_run.py` `test_batch_wait_over_captured_jobs[ok]`) | n/a |
| 12 | Command exits 1 to 127 | FAILED | `exit1` | F, I | F, I | n/a | I, CLI (`test_status_active_history_and_details`: exit 3) | F; I, CLI (the same test) | I (`test_array_1_to_5`) | F (`[exit1]`); I (`test_hpc_run_wait_exits_as_the_job_did[exit 3]`, `test_run_exits_with_the_jobs_exit_code`) | n/a |
| 13 | Script exits above 128 | FAILED | `exit137` | F, I | F: 137 from the controller, 9 from `sacct`; `test_one_command_when_the_controller_settles_the_code` | n/a | F (`test_the_exit_code_is_the_controllers_while_it_knows_the_job`); I, CLI (`test_history_has_sacct_exit_code_and_details_the_controllers`: 137) | F: 9; I, CLI: 9 | · | F: 137 (`[exit137]`, `TestSlurmCommands`); I: 137, and 139 for a segfault | n/a |
| 14 | Batch shell killed by a signal | FAILED | `signal` | F | F: 137 | n/a | · | F: 137 | · | · | n/a |
| 15 | Killed at its time limit | TIMEOUT | `timeout` | F, I (`test_time_limit_kill`) | F, I: 143 | n/a | I | F; I, CLI: 143 | I | I: 1 (`test_a_time_limit_exits_1_with_timeout`); no capture | n/a |
| 16 | Killed for memory | KILLED | `out_of_memory` | F, I (`test_over_its_memory_is_killed`) | F, I: 253 | n/a | F (`test_out_of_memory_is_killed_and_slurms_state_is_kept`); I; I, CLI (`test_status_shows_slurms_own_state`) | F; I, CLI: 253 | · | F: 1 (`[out_of_memory]`); I: 1 | n/a |
| 17 | Out of memory inside an `srun` step, script exits 0 | COMPLETED | `out_of_memory_step` | F | F: 0 | n/a | · | F | · | · | n/a |
| 18 | Cancelled while pending (held) | CANCELLED | `cancelled_pending` | F, I (`test_cancelled_while_pending_is_cancelled_with_no_exit_code`) | F, I: none | n/a | I | F: no row in what `sacct -S` gives (`test_a_job_that_was_never_eligible_to_run_is_not_in_what_sacct_gives`) | I | F: no code, `hpc run` exits 1 (`[cancelled_pending]`) | n/a |
| 19 | Cancelled while running | CANCELLED | `cancelled_running` | F, I (`test_cancelled_while_running`) | F, I: 143 | n/a | F (`test_a_cancelled_job_names_who`); I, CLI (`test_status_shows_slurms_own_state`) | F; I, CLI: 143 | I | F: 1 (`[cancelled_running]`) | n/a |
| 20 | Dependent killed by `--kill-on-invalid-dep` | SKIPPED | `dependent_skipped` | F; I (`test_every_skipped_job_is_cancelled_by_slurm_with_no_exit_code`, the generic `test_dependencies.py`) | F, I: none | n/a | F (`test_a_skipped_dependent`), I | F: no row in what `sacct -S` gives | · | F: no code, `hpc run` exits 1 (`[dependent_skipped]`); I (`test_a_skipped_job_exits_1`) | n/a |
| 21 | Dependent cancelled by its owner while it waits | CANCELLED | `dependent_pending_cancelled` | F | F: none | n/a | · | · | · | · | n/a |
| 22 | Node lost, submitted with `--no-requeue` | FAILED | `node_fail` | F (`test_a_lost_node`), I (`test_node_killed`) | F: 129 then 1 on 24.11, none on 22.05; I: not 0 | n/a | I | c (`sacct_history.txt` has the row); I, CLI: FAILED, code not 0 | · | · | n/a |
| 23 | An id neither command knows | UNKNOWN | `sacct_missing.json`, `scontrol_missing.json` | F | F: none | n/a | F (`test_a_job_neither_command_knows`: `JobNotFoundError`) | n/a | u (`test_core/test_result.py`) | · | n/a |
| 24 | Array, no task started | PENDING | `array` (stage `pending`), `array_list`, `array_stepped`, `array_single` | F (base id; task ids in `test_tasks_written_as_a_list_or_with_a_step`) | F: none | F (`test_a_pending_array`) | F (`test_tasks_that_never_started_have_no_output_path`) | · | · | n/a | · |
| 25 | Array, tasks running and tasks waiting | RUNNING; each task its own | `array_throttled` | F (`test_tasks_are_asked_by_the_id_of_their_array`) | F: none | F (`test_an_array_is_one_entry_under_its_own_id`) | F (`test_an_array_with_started_and_waiting_tasks`) | · | I (`test_array_concurrency_limit_is_accepted`) | n/a | · |
| 26 | Array finished, every task completed | COMPLETED | `array` | F, I (`test_array_concurrency_limit_is_accepted`) | F: 0 | n/a | · | c (four rows in `sacct_history.txt`) | I | · | n/a |
| 27 | Array finished, one task failed | FAILED; each task its own | `array_partial` | F (`test_each_task_has_its_own_status_and_code`), I (`test_array_1_to_5`) | F, I: 1 | n/a | F (`test_a_finished_array_and_its_tasks`, `test_one_task_by_its_own_id`), I | F (`test_each_array_task_is_an_entry`) | I | F (`test_an_array_has_every_tasks_status`, `test_array_wait_over_a_captured_array`) | n/a |
| 28 | Array cancelled before any task started | CANCELLED | `array_cancelled_pending` | F | F: none | n/a | F (`test_tasks_that_never_started_have_no_output_path`) | F: no row in what `sacct -S` gives | · | · | n/a |
| 29 | Array cancelled while tasks ran | CANCELLED | `array_throttled` (stage `cancelled`) | F | F: 143 | n/a | · | c (two task rows in `sacct_history.txt`) | · | · | n/a |
| 30 | Interactive job (`srun`) | none: an exit code only | `srun_bad_partition.json`, `srun_bad_account.json` (rejections) | n/a | u (`test_slurm.py` `test_srun_runs_the_script_and_its_exit_code_is_the_results`), I (`test_interactive`, `test_run_exits_with_the_jobs_exit_code`) | n/a | n/a | n/a | n/a | n/a | n/a |
| 31 | `REQUEUE_HOLD`, `REQUEUE_FED`, `RESV_DEL_HOLD`, `CONFIGURING`, `COMPLETING`, `SIGNALING`, `STAGE_OUT`, `RESIZING`, `STOPPED`, `BOOT_FAIL`, `SPECIAL_EXIT`, `PREEMPTED`, `REVOKED`, `DEADLINE` | by name only | none | u (`TestStates.test_the_table`) | · | · | · | · | · | · | · |

## 3. Empty cells

Nothing here is planned. The list is what a new test or capture would
fill.

- **`hpc status <id>` of a job that has not started** (rows 1 to 4, 8 to
  10): no details test for a pending-for-resources, held,
  dependency-waiting, suspended or requeued job. The captures
  (`scontrol_<scenario>.txt`) exist.
- **`hpc status` and `hpc monitor` over the dependency and array
  snapshots** (rows 3, 4, 10, 24, 25 for M): the `squeue` captures exist;
  the monitor test reads the one snapshot `squeue_all.txt`.
- **`wait()` against a job that sits pending** (rows 1 to 4): only seen
  indirectly.
- **The waited submission for a time limit** (row 15) has an integration
  test and no capture; **for a batch shell killed by a signal** (row 14)
  and **for a lost node** (row 22) it has neither.
- **Row 31**: fourteen state names are mapped from `squeue --helpstate` on
  24.11.5 and have never been seen in a capture (`../FOLLOW_UPS.md`, "Left
  open by the reading step").
- **`COMPLETING` straight after `scancel`**: the status is final and there
  is no exit code for about 0.2 s. No capture; the integration tests poll
  through it (`exit_code_once_known`).
- **`hpc monitor`** is tested for its listing only. Its detail panel and
  its Completed tab have not been run on Slurm
  (`MANUAL_CHECKLIST.md`).

## 4. Where two readers give different answers

Each is as built. None is decided otherwise; `../FOLLOW_UPS.md` has the
detail.

- **The exit code** (rows 13, 15, 16, 19): V returns what `sbatch --wait`
  returned, X the controller's number, H `sacct`'s. One job can show 1,
  143 and 143 (time limit), or 137, 137 and 9 (script exits 137).
  `../FOLLOW_UPS.md`, "The exit code of a batch job".
- **A lost node** (row 22): the exit code is 129, then 1, on 24.11 and
  none on 22.05.
- **Jobs that were never eligible to run** (rows 2, 18, 20, 28) are not in
  H; D shows them.
- **An array** is one entry in L and M, one entry per task in H, and a
  whole with its tasks in D.
- **Slurm's own state** (`OUT_OF_MEMORY`, `CANCELLED by <uid>`) is in D
  only, under the extra details.

## 5. How to use this

- A new reader adds a column; a new state or capture scenario adds a row.
  Every cell gets a mark before the work is called done.
- A capture scenario goes in `tests/integration/capture_slurm.py`, and the
  capture-backed test replaces a hand-typed one for that row.
