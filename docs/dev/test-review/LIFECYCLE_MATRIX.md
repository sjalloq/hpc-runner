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
| 1 | queued `qw` | PENDING | `array` tail tasks | F | F | n/a | F | n/a | · | · | F (`TestJobTableFromCapturedSnapshots`) | I (`wait_for`) |
| 2 | user hold `hqw` | PENDING | `held` | F | F | n/a | F | n/a | · | I (`test_hold_flag_forwards`, `test_status_active_history_and_details`), F (`test_hpc_status_table`) | F (`TestJobTableFromCapturedSnapshots`) | I (`test_queued_running_done`), F (`test_wait_holds_on..`) |
| 3 | dependency hold (`-hold_jid`) `hqw` | PENDING | `dependent` (`qstat_mixed`) | F (the same `hqw` as 2) | F (`test_every_reader_agrees`), I (`test_dependencies`) | n/a | F | n/a | · | F (`test_hpc_status_table`) | F (`TestJobTableFromCapturedSnapshots`) | F (`test_wait_holds_on..`), I |
| 4 | error `Eqw` | FAILED | `eqw` | F | F | · (no record; sentinel missing → None) | F | n/a | · | F (`test_hpc_status_table`) | F (`TestJobTableFromCapturedSnapshots`) | F (`test_wait_holds_on..`: returns FAILED) |
| 5 | running `r` | RUNNING | `running` | F | F | n/a | F | n/a | F (`test_running_job_details`, `..merges_listing_and_details`) | I (`test_status_active..`) | F (`TestJobTableFromCapturedSnapshots`) | I |
| 6 | transferring `t` | RUNNING | `array` (some tasks) | F | F | n/a | F | n/a | · | · | F (via `array`) | · |
| 7 | user suspended `s` (`qmod -sj`) | SUSPENDED | `suspended` (`qstat_lifecycle`, `qstat_j_suspended`) | F | F (`TestLifecycleStates`) | n/a | F | n/a | F (`qstat_j_suspended`) | F (`test_hpc_status_table`, `..of_one_suspended_job`) | F (`TestJobTableFromCapturedSnapshots`) | F (`test_wait_holds_on..`), I (`test_suspended_job_is_waited_through`) |
| 8 | queue suspended `S` (`qmod -sq`) | SUSPENDED | `queue_suspended` | F | F | n/a | F | n/a | · | F (`test_hpc_status_table`) | F (`TestJobTableFromCapturedSnapshots`) | F (`test_wait_holds_on..`) |
| 9 | threshold `T` / restarted `Rr` `Rq` | SUSPENDED / RUNNING / PENDING | `threshold` (`T`, `qstat_transient`, `qstat_j_threshold`), `requeued` (`Rr`), `requeue_pending` (`Rq`) | F | F (`test_every_reader_agrees`) | n/a | F | n/a | F (`T`) | F (`test_hpc_status_table`, `..of_one_suspended_job`) | F (`TestJobTableFromCapturedSnapshots`) | F (`test_wait_holds_on..`) |
| 10 | deleting `dr` `dt` | CANCELLED | `deleting` (`qstat_deleting`, node paused), `deleting_transfer` (`dt`, `qstat_transient`) | F | F (`test_deleting_is_cancelled..`, `test_every_reader_agrees`) | n/a | F | n/a | · | F (`test_hpc_status_table`) | F (`TestJobTableFromCapturedSnapshots`) | F (`test_wait_holds_on..`: returns CANCELLED) |
| 11 | done, exit 0 | COMPLETED | `ok` | F | F | F | n/a | F (`test_records`), I (`test_two_users`) | F (`TestFinishedJobDetails`) | I (`test_tool_alias..`) | · | I |
| 12 | done, exit ≠ 0 | FAILED | `exit1` | F | F | F | n/a | F | F (`TestFinishedJobDetails`) | I (`test_status_active_history_and_details`: exit 3 in history and `<id>`) | · | I |
| 13 | guard skipped, exit 113 | SKIPPED | `skipped` | F (`test_records`) | F (`TestFinishedJobDetails`), u, I (`test_dependencies`) | F | n/a | F (`test_records`), u | F (`TestFinishedJobDetails`) | · | · | I |
| 14 | qdel while running: `failed 100`, `exit 137` | FAILED | `signal` | F | F | F | n/a | F, I (`test_status_shows_the_schedulers_failure_reason`) | F (`TestFinishedJobDetails`) | I (history shows `100: assumedly after job`) | · | I (`test_signal_exit_code` accepts CANCELLED **or** FAILED) |
| 15 | `h_rt` kill: `failed 37`, `exit 137` | TIMEOUT | `timeout` | F | F, u, I (`test_h_rt_kill`) | F, I | n/a | F (`test_records`, `..carries_sges_reason`) | F (`TestFinishedJobDetails`) | · | · | I |
| 16 | `h_vmem` over limit | FAILED (`failed 0`, exit 2: setrlimit, the process fails itself; no execd kill on this stack) | `vmem` | F | F, I (`test_h_vmem`) | F, I | n/a | F | F | · | · | I |
| 17 | node lost: `failed 19`, `exit 0` | FAILED | `lost_node` | F | F, I (`test_node_killed`) | F | n/a | F, I | F (`TestFinishedJobDetails`) | · | · | I |
| 18 | deleted while pending (`qdel` of `hqw`) | UNKNOWN (no record ever; accepted, §4.2) | `deleted_pending` | n/a | F (`test_deleted_while_pending..`) | F | n/a | n/a | F (`JobNotFoundError`) | · | · | I (`test_deleted_while_pending_waits_out_to_unknown`) |
| 19 | rescheduled / requeued: `failed 25` record, `Rq` → `Rr`, then its end | RUNNING while active, then the final record | `requeued` (`qacct_requeued`: 25 then 100) | F | F, u | F | F (`Rr`) | · | F | · | · | I (`test_requeued_job_ends_with_its_final_record`) |
| 20 | aged out of qacct, sentinel gone | UNKNOWN | · | n/a | I (`test_aged_out`) | · | n/a | n/a | · | · | · | u (`test_unknown_after..`) |
| 21 | sentinel only (qacct not yet flushed) | from sentinel | · | n/a | u (`test_qacct_without_exit_status_uses_sentinel`, `..wins_over_sentinel`), I (`test_slow_accounting_window`, slow stack) | u, I | n/a | n/a | · | · | · | · |
| 22 | unknown id | UNKNOWN / `JobNotFoundError` | `qacct_missing`, `qstat_j_missing` | F | F (`test_get_status_unknown_id`) | · | n/a | n/a | F (`test_unknown_id_is_not_found`) | · | · | · |
| 23 | qmaster unreachable | `SchedulerUnavailableError` | `master_down` | F | F (`test_get_status_raises`) | · | F (`test_list_active_jobs_raises`) | · | · | · | · | · |
| 24 | array, tasks running + queued, task id | per task | `array` | F | F (`array`,1 / `array`,9) | n/a | F | n/a | · | · | · | I (`test_array_1_to_5`) |
| 25 | array, base id while active | reduction of the tasks (RUNNING > SUSPENDED > PENDING) | `array` | F (`..running_array_wins`) | F, u (`test_base_id_of_active_array_is_the_reduction..`) | n/a | F | n/a | · | · | · | u (`TestArrayJobResult`) |
| 26 | array, finished task id | per task | `array_done`,2 | F | F | F | n/a | F (`test_array_task_record_names_its_task`) | · | · | · | I |
| 26a | requeued job: synthetic `failed 24` record then the real one | the final record | superseded by row 19's real capture (`failed 25`) | u | u (`test_requeued_job_reads_its_final_record`) | u | n/a | · | · | · | · | · |
| 27 | array, finished **base** id | reduction of the tasks (COMPLETED only if all are) | `array_done` (`qacct_array_base`) | F (`..one_record_per_task`) | F (`TestFinishedArrayBaseId`), u (`..reduces_over_every_qacct_record`), I (`test_array_1_to_5`) | F, I | n/a | F | F (`..whole_and_every_task`), I | F (`..prints_the_task_table`, `..json_lists_the_tasks`), I | · | u, I |
| 28 | interactive (`qrsh`) | exit code only | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |

## 3. Empty cells worth a capture

Every item from the first pass is captured (2026-11-01, `hpc-testbed
capture` scenarios `timeout`, `vmem`, `deleted_pending`, `suspended`,
`queue_suspended`, `requeued`, `deleting`) and pinned by fixture tests
(`TestLifecycleStates`, `TestQacct`, `TestFinishedJobDetails`,
`TestJobTableFromCapturedSnapshots`). What the captures showed:

- **Finished array base id** (row 27): records come in finish order; the
  old fold was timing-dependent. Fixed (D-T44).
- **Deleted while pending** (row 18): `qdel` says "has deleted job", qacct
  never has a record (checked 5 s after, well past the flush). UNKNOWN,
  accepted.
- **`h_rt`** (row 15): the real line is `failed 37 : qmaster enforced h_rt,
  h_cpu, or h_vmem limit`, `exit_status 137 (Killed)`.
- **`h_vmem`** (row 16): `failed 0`, exit 2. On this stack the limit is a
  setrlimit and the allocation fails inside the process; there is no
  execd kill, so the parser docstring's "execd limit kill is `failed 100`"
  is still unverified (a site that enforces by polling usage may differ).
- **Suspended** (rows 7-8): `s` and `S` in both qstat forms.
- **`dr`** (row 10): only lasts while the execd is away; captured by
  pausing the node. `qdel` then says "has registered the job for deletion".
- **Requeue** (row 19): `qmod -rj` gives `Rq` then `Rr`; qacct holds a
  `failed 25 : rescheduling` record while the job runs again, then the real
  end. SGE will not rerun on the same host until it has cleaned up.

- **`T`** (row 9): a queue `suspend_thresholds np_load_avg=0.0` trips at
  once (`>=`); it must be set after the job starts, since a queue in
  suspend alarm dispatches nothing.
- **`Rq`** (row 9): a rescheduled job hard-pinned to its host stays `Rq`
  until that host has cleaned up.
- **`dt`** (row 10): a held job pinned to a paused node, released, is
  dispatched but undeliverable (`t`); `qdel` then gives `dt`.

- **Dependency hold** (row 3): `-hold_jid` shows as plain `hqw`, the same
  code as a user hold.
- **Skip** (row 13): exit 113 is `failed 0`, `exit_status 113`: SKIPPED.

Every state the testbed can produce now has a capture, and `hpc status`
(G) and `wait()` (J) are pinned over every captured active snapshot
(`TestLifecycleStates`), plus live for suspend, requeue and deleted while
pending (`tests/integration/test_status.py`).

### 3.1 Cells still empty

No capture is possible for rows 20 and 21: they are defined by a record
being *absent* (aged out; not yet flushed), so they are covered live
(`test_aged_out`, `test_slow_accounting_window`). Row 26a is superseded by
row 19. The `·` cells left are reader coverage, not captures:

| Col | Empty in rows | What is missing |
|---|---|---|
| C | 4, 20, 22, 23 | exit code of an Eqw, aged-out, unknown or unreachable job (`None` or raise; untested) |
| E | 19, 23, 26a | the history listing of a requeued job's two records; the listing with the master down |
| F | 1-4, 6, 8, 10, 24, 25; 20, 21, 23, 26 | active: a `qstat -j -xml` capture per state (the state comes from the listing, which is pinned; only the merge of details is untested). Finished: details of an aged-out, sentinel-only, unreachable job and of one array task |
| G | 1, 6, 24-26; 13, 15-23, 26a | `hpc status` over the queued and array snapshots, and `<id>` / `--history` over the qacct captures (12, 14, 27 have it) |
| H | 11-27 | the TUI's completed-jobs view (`list_completed_jobs`) and detail panel are never fed a captured record |
| J | 6, 21-23, 26a | 6 is an array task in `t` (row 24 covers the array); 22 and 23 are errors rather than states |

None of these is known to hide a bug.

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
- The remaining empty cells are listed at the end of §3. Row 27 was a real
  bug (fixed 2026-11-01, D-T44).
