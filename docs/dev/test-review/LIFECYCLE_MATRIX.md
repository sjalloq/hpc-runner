# Job lifecycle matrix: SGE states × readers

Status: survey, 2026-10-31 night. Agreed in the evening session after the
test QA review: the review's escapes were all cases where a reader met a
scheduler state no test had shown it. This table is every state a job can be
in on SGE against every piece of code that reads job state, with the test
that pins each cell. Empty cells are the work list (§3); cells where two
readers disagree are decisions (§4).

Marks:

- **F** unit test on a fixture captured from the testbed
  (`tests/unit/fixtures/sge/`, `hpc-testbed capture`).
- **u** unit test on hand-typed or synthetic data (a record dict, an XML
  string in the test file). Pins the mapping, not the scheduler's words.
- **I** integration test in the testbed (`tests/integration/`).
- **·** nothing. The code path exists but no test has run it with this state.
- **n/a** the reader cannot see this state (a finished job is not in qstat).

## 1. Readers

| Col | Reader | What it reads |
|---|---|---|
| A | `state_to_status` / `qacct_status` (`sge/parser.py`) | one qstat state code / one qacct record |
| B | `get_statuses` / `get_status` / `result()` (`sge/scheduler.py`) | qstat -xml, then qacct -j, then the exit sentinel |
| C | `get_exit_code` | qacct `exit_status`, then the sentinel |
| D | `list_active_jobs` | qstat -xml -u * |
| E | `list_completed_jobs` / `qacct_to_job_info` | qacct -j [-o user] [-b/-e] |
| F | `get_job_details` | qstat -j -xml, else qacct |
| G | `hpc status` (active table / `--history` / `<id>`) | D / E / F+B+C |
| H | `hpc monitor` (TUI) | D for the table, F for the detail panel |
| J | `JobResult.wait()` / `.result()` / `ArrayJobResult` (`status`/`wait()` reduce the tasks, `task_statuses()` drills down) | B, polled |

G, H and J contain no state logic of their own. Their unit tests use a mock
scheduler or hand-built `JobInfo`s and are state-agnostic: `test_status.py`
(table, history, single job, JSON, fallback to B+C), `test_job_table.py::
test_status_formatting` (a style per status), `test_detail_panel.py` (cancel
button enabled for RUNNING, disabled for COMPLETED), `test_result.py` (the
exception per terminal status, SKIPPED, UNKNOWN after `unknown_after`, one
bulk query per tick). They are listed in the table only where an integration
test drives them against a real state.

## 2. The matrix

Capture scenario names are from `tests/unit/fixtures/sge/manifest.json`.

| # | State (SGE) | → JobStatus | Capture | A | B | C | D | E | F | G | H | J |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | queued `qw` | PENDING | `array` tail tasks | F | F | n/a | F | n/a | · | · | · | I (`wait_for`) |
| 2 | user hold `hqw` | PENDING | `held` | F | F | n/a | F | n/a | · | I (`test_hold_flag_forwards`, `test_status_active_history_and_details`) | · | I (`test_queued_running_done`) |
| 3 | dependency hold (`-hold_jid`) `hqw` | PENDING | · | u (same code as 2) | I (`test_dependencies`) | n/a | · | n/a | · | · | · | I |
| 4 | error `Eqw` | FAILED | `eqw` | F | F | · (no record; sentinel missing → None) | F | n/a | · | · | · | · |
| 5 | running `r` | RUNNING | `running` | F | F | n/a | F | n/a | F (`test_running_job_details`, `..merges_listing_and_details`) | I (`test_status_active..`) | · | I |
| 6 | transferring `t` | RUNNING | `array` (some tasks) | F | F | n/a | F | n/a | · | · | · | · |
| 7 | user suspended `s` (`qmod -sj`) | SUSPENDED | · | u (`..suspended_codes..`) | · | n/a | · | n/a | · | · | · | · |
| 8 | queue suspended `S` (`qmod -sq`) | SUSPENDED | · | u | · | n/a | · | n/a | · | · | · | · |
| 9 | threshold `T` / restarted `Rr` `Rq` | SUSPENDED / RUNNING / PENDING | · | u | · | n/a | · | n/a | · | · | · | · |
| 10 | deleting `dr` `dt` | CANCELLED | · | u (`dr` only) | · | n/a | · | n/a | · | · | · | · |
| 11 | done, exit 0 | COMPLETED | `ok` | F | F | F | n/a | F (`test_records`), I (`test_two_users`) | F (`TestFinishedJobDetails`) | I (`test_tool_alias..`) | · | I |
| 12 | done, exit ≠ 0 | FAILED | `exit1` | F | F | F | n/a | F | F (`TestFinishedJobDetails`) | I (`test_status_active_history_and_details`: exit 3 in history and `<id>`) | · | I |
| 13 | guard skipped, exit 113 | SKIPPED | · | u | u (`test_qacct_113_is_skipped`), I (`test_dependencies`) | u | n/a | u (`..skipped_and_timeout`) | · | · | · | I |
| 14 | qdel while running: `failed 100`, `exit 137` | FAILED | `signal` | F | F | F | n/a | F, I (`test_status_shows_the_schedulers_failure_reason`) | F (`TestFinishedJobDetails`) | I (history shows `100: assumedly after job`) | · | I (`test_signal_exit_code` accepts CANCELLED **or** FAILED) |
| 15 | `h_rt` kill: `failed 37`, `exit 137` | TIMEOUT | · | u | u (`test_qacct_failed_37_is_timeout`), I (`test_h_rt_kill`) | I | n/a | u (`..skipped_and_timeout`) | · | · | · | I |
| 16 | `h_vmem` kill (execd) | FAILED | · | u (`test_execd_h_rt_kill_stays_failed`) | I (`test_h_vmem`) | I | n/a | · | · | · | · | I |
| 17 | node lost: `failed 19`, `exit 0` | FAILED | `lost_node` | F | F, I (`test_node_killed`) | F | n/a | F, I | F (`TestFinishedJobDetails`) | · | · | I |
| 18 | deleted while pending (`qdel` of `qw`) | ? | · | · | · | · | · | · | · | · | · | · |
| 19 | rescheduled / requeued: `failed 24`/`25`, then `Rq`/`Rr` | None → next run | · | u (24, 25 → None) | · | · | · | · | · | · | · | · |
| 20 | aged out of qacct, sentinel gone | UNKNOWN | · | n/a | I (`test_aged_out`) | · | n/a | n/a | · | · | · | u (`test_unknown_after..`) |
| 21 | sentinel only (qacct not yet flushed) | from sentinel | · | n/a | u (`test_qacct_without_exit_status_uses_sentinel`, `..wins_over_sentinel`), I (`test_slow_accounting_window`, slow stack) | u, I | n/a | n/a | · | · | · | · |
| 22 | unknown id | UNKNOWN / `JobNotFoundError` | `qacct_missing`, `qstat_j_missing` | F | F (`test_get_status_unknown_id`) | · | n/a | n/a | F (`test_unknown_id_is_not_found`) | · | · | · |
| 23 | qmaster unreachable | `SchedulerUnavailableError` | `master_down` | F | F (`test_get_status_raises`) | · | F (`test_list_active_jobs_raises`) | · | · | · | · | · |
| 24 | array, tasks running + queued, task id | per task | `array` | F | F (`array`,1 / `array`,9) | n/a | F | n/a | · | · | · | I (`test_array_1_to_5`) |
| 25 | array, base id while active | reduction of the tasks (RUNNING > SUSPENDED > PENDING) | `array` | F (`..running_array_wins`) | F, u (`test_base_id_of_active_array_is_the_reduction..`) | n/a | F | n/a | · | · | · | u (`TestArrayJobResult`) |
| 26 | array, finished task id | per task | `array_done`,2 | F | F | F | n/a | F (`test_array_task_record_names_its_task`) | · | · | · | I |
| 26a | requeued job: `failed 24` record then the real one | the final record | · | u | u (`test_requeued_job_reads_its_final_record`) | u | n/a | · | · | · | · | · |
| 27 | array, finished **base** id | reduction of the tasks (COMPLETED only if all are) | `array_done` (`qacct_array_base`) | F (`..one_record_per_task`) | F (`TestFinishedArrayBaseId`), u (`..reduces_over_every_qacct_record`), I (`test_array_1_to_5`) | F, I | n/a | F | F (`..whole_and_every_task`), I | F (`..prints_the_task_table`, `..json_lists_the_tasks`), I | · | u, I |
| 28 | interactive (`qrsh`) | exit code only | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |

## 3. Empty cells worth a capture

Ordered by how likely the gap hides an escape of the kind the QA review
found (a reader meeting words it has never seen).

1. ~~**Row 27, finished array base id.**~~ **Done 2026-11-01** (§4.3).
   `qacct_array_base.txt` is the real `qacct -j` of the three-task
   `array_done` (task 3 exits 1): the records come in finish order (2, 3,
   1), not index order, so the old fold really did depend on timing.
   `_qacct` now returns the final record per task and every base-id reader
   reduces over them.
2. **Row 18, deleted while pending.** SGE writes no accounting record for a
   job that never started, so B falls to the sentinel, which the job never
   wrote: UNKNOWN, until `unknown_after` turns it into a result. If that is
   right it needs a test that says so; if not, CANCELLED needs a source
   (§4.2). Capture: `qdel` of a held job, then `qacct -j` (expect "not
   found") and `qstat -j` (expect `<unknown_jobs>`).
3. **Row 15, `h_rt` timeout record.** TIMEOUT is pinned by synthetic records
   and a live test, but no fixture holds the real `failed 37 : qmaster
   enforced h_rt, h_cpu, or h_vmem limit` line, so `hpc trace compare
   --fixtures` cannot vouch for it and the parser tests never see the real
   text. Capture: a `-l h_rt=0:0:5 sleep 60` job, wait for `failed 37`.
4. **Rows 7 and 8, suspended.** `JobStatus.SUSPENDED` exists since
   2026-11-01 (§4.1) and the codes are unit-tested; still no capture of the
   real `s`/`S` in qstat output. Capture: `qmod -sj` on `running` and `qmod
   -sq all.q@exec2` in the snapshot scenario.
5. **Row 10, `dr`.** Only `dr` → CANCELLED is unit-tested, from a typed
   string. A snapshot taken right after `qdel` of a running job would show
   the real `dr` and pin D/G for the deleting window (it is seconds long,
   so the capture must poll for it).
6. **Row 19, requeue.** `failed 24/25` → None is pinned on synthetic
   records; nothing shows what qstat says (`Rq`, `Rr`) or what B reports
   between the two runs. Capture: `qmod -rj` on a `-r y` job.
7. **Row 16, `h_vmem` record.** Live test only. The `failed` code the execd
   kill writes is asserted nowhere (the parser docstring's "100" is a
   research claim). Capture: the `test_h_vmem` job's `qacct -j`.
8. ~~**Rows 4, 12, 15, 17 in F.**~~ **Done 2026-11-01**:
   `TestFinishedJobDetails` runs `get_job_details` over every captured
   finished job (rows 11, 12, 14, 17). Row 15 still has no fixture (item 3).
9. **Rows 1-6 in H.** The TUI has never been shown real `JobInfo`s from the
   `qstat_mixed` fixture. A snapshot test that feeds `list_active_jobs` over
   `mock_sge_commands` into `JobTable` would pin the table for every active
   state at once.

## 4. Decisions in flight

1. **Suspended is PENDING?** A suspended job has started and holds a slot,
   but reads as PENDING (`s`, `S`) or RUNNING (`ts`, `tS`, since `t` is
   checked first). **Decided 2026-11-01: add `JobStatus.SUSPENDED`** (not
   terminal; every scheduler has the state: Slurm `S`, PBS `S`, LSF
   `PSUSP`/`USUSP`/`SSUSP`) and map SGE `s`/`S`/`T` and their `t` forms to
   it. Look at `Eqw` in the same pass: it maps to FAILED though the job is
   still queued awaiting `qmod -cj`.
2. **A cancelled job ends FAILED.** Row 10 reports CANCELLED only while qstat
   still shows `dr`; once qacct has the record the same job is FAILED (row
   14), and a job deleted while pending has no record at all (row 18,
   UNKNOWN). **Decided 2026-11-01: don't care.** `hpc cancel` returns on
   qdel's answer and that is all the caller needs; nobody else's job can
   depend on one you cancelled, since you cannot qdel another user's job.
   Nothing in the code branches on CANCELLED vs FAILED (both terminal,
   `result()` raises on both); the difference is a label. No marker, no
   `failed 100` + SIGKILL mapping, no doc note. `test_signal_exit_code`
   keeps accepting either.
3. **A finished array's base id.** The user's question is "did the whole
   array pass?", asked as `hpc status 42` or `result.status` after
   `hpc run -b -a 1-100 ./run_test.sh`; today both read the last task's
   qacct record (last record wins, so the answer depends on finish order).
   **Decided 2026-11-01:** the programmatic workflow is submit, poll one
   status until terminal, and only if it is not COMPLETED drill into the
   tasks. So:
   - `ArrayJobResult.status` and `wait()` return one `JobStatus`, the
     reduction over the tasks, as `JobResult` does (no more type-ignored
     override); `result()` is defined for arrays and raises naming the
     failed indices. `task_statuses()` returns the drill-down
     (`ArrayJobStatus`: index→status, counts, failed, is_terminal).
   - One reduction rule in `core/result.py`, used by the array handle, the
     pipeline (replacing `_step_status`) and the scheduler's base-id reads:
     any active task makes the array active (RUNNING > SUSPENDED >
     PENDING); all terminal: FAILED/TIMEOUT/CANCELLED > SKIPPED >
     COMPLETED; UNKNOWN anywhere → UNKNOWN.
   - `get_statuses(["42"])` returns that reduction over qstat's task
     entries while active and over the per-task qacct records once
     finished; `_active_state`'s own "a running task if any" rule goes.
   - `parse_qacct_output` stops merging records (one per task); captured
     `qacct -j <base>` fixture with mixed exit codes.
   - `hpc status <base>` prints the overall status and a per-task table
     (index, status, exit code, failed code).

## 5. How to use this

- A new reader or a new scheduler flavour adds a column or a table; every
  row must get a mark or an n/a before the work is called done.
- A capture scenario added for §3 goes in `tests/integration/capture.py`
  with its argv in the manifest, and the fixture-backed unit test replaces
  the synthetic one for that row (synthetic stays only where the testbed
  cannot produce the record).
- Rows 10, 18 and 19 are where the next escape lives; rows 7-9 have the
  mapping but no real capture. Row 27 was a real bug (fixed 2026-11-01).
