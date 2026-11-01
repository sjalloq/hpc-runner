# Closing the lifecycle-matrix gaps

Plan for the cells `LIFECYCLE_MATRIX.md` §3.1 lists as still empty (as of
39e1fc4, 2026-11-01). Every SGE state the testbed can produce has a
capture; what is missing is a *reader* fed that capture. Working through
the list turned up two behaviours that need a decision and one missing
feature, so those come first.

## 1. Findings from the survey

**F1. `hpc status --history` lists a requeued job twice.** It reads
`list_completed_jobs`, which turns every qacct record into a row. The
reschedule record of a requeued job becomes a second row: UNKNOWN, exit 0,
`25 rescheduling`. Checked against `qacct_requeued.txt`:

```
263 FAILED  137 100 assumedly after job
263 UNKNOWN   0  25 rescheduling
```

`_qacct` (status, exit code, details) already keeps only the last record a
status can be read from. The history listing never got that rule.

**F2. One failed poll ends `wait()`.** `wait_for_statuses` calls
`get_statuses` each tick and does not catch errors, so a
`SchedulerUnavailableError` from a single failed qstat ends a wait that
may have run for hours. That covers a qmaster restart or a failover
window. Nothing tests this either way (row 23, column J).

**F3. The monitor has no Completed view.** `hpc monitor` has a
"Completed" tab, but it holds a placeholder (`tui/app.py:113`).
`JobProvider.get_completed_jobs` exists and nothing calls it. So column H
for rows 11-27 is a missing feature, not a missing test.

Separately, the provider catches every exception: `get_completed_jobs`
returns `[]` and `get_job_details` returns `None`. When the qmaster is
down, the detail panel cannot tell that apart from "no such job".

**F4. One stale cell.** Row 20, column C is marked empty, but
`test_aged_out` already asserts `get_exit_code(...) is None`. The fix is
`I`.

## 2. Decisions (user, 2026-11-01; D-T45)

| # | Question | Decision |
|---|---|---|
| D1 (F1) | History drops the records that are not a job's end? | Fix it if it's easy. Done: `_final_records()` is the one rule for `_qacct` and the history listing. The record is permanent, so a requeued job showed twice in its history for as long as accounting kept it. |
| D2 (F2) | `wait()` keeps polling through a scheduler outage? | Don't care. With the qmaster down, polling is the least of a user's worries. Row 23, column J is n/a. |
| D3 (F3) | Build the Completed tab, or mark H n/a? | n/a. A TUI rewrite is coming. Column H is n/a for rows 11-27, and W8 is dropped. |
| D4 | Row 26a (synthetic `failed 24` requeue) | Folded into row 19. The synthetic unit test stays: it is harmless and covers the `24` code. |

## 3. Work items

Ordered cheapest first. Effort is a rough count of new tests or capture
steps. Each item updates its matrix cells to `F` or `I` when it lands.

### W1. Exit codes of states with no record (C 4, 20, 22, 23). Small.

- Row 4: `get_exit_code` of the `eqw` job, with the mixed snapshot
  responder, returns None.
- Row 22: unknown id gives `qacct_missing`, so None.
- Row 20: mark `I` (F4).
- Row 23: depends on W2's capture. qacct reads the accounting file, so it
  may well work with the qmaster down. The test pins whichever it does.

### W2. Master-down accounting (C, E, F 23). Small capture, three tests.

Extend the `master_down` scenario to capture `qacct -j <ok>` and
`qacct -j -o tester` while the qmaster is stopped, as JSON like
`qstat_master_down.json`. Then pin `get_exit_code`, `list_completed_jobs`
and `get_job_details` against it. `get_job_details` runs `qstat -j`
first, so it should raise `SchedulerUnavailableError`, not report
"not found".

### W3. History over every finished capture (E 19, G 13, 15-19). Small. The requeue part is done (D1).

- The fake's `qacct -j` (all records) concatenates only ok, exit1 and
  signal. Make it concatenate every finished capture: timeout, vmem,
  lost_node, skipped, requeued, the array base.
- One parametrized `hpc status --history --json` test: each job appears
  once, with the status, exit code and failed reason of its final record.
- One table test checks the human output. With D1 in place this pins F1's
  fix.

### W4. `hpc status <id>` of every finished capture (G 13, 15-19). Small.

Parametrize over the `TestFinishedJobDetails` scenarios. The test drives
`hpc status <id>` and `--json`, and checks the status, exit code and
failed reason in the output. This adds deleted_pending (row 18, which
should print UNKNOWN or "not found", not a traceback) and `hpc status
<base>.<task>` for row 26.

### W5. `qstat -j` for every active state (F 1-4, 6, 8, 10, 24, 25). Small capture, one test.

- In `snapshot`, `lifecycle` and `transient_states`, loop over the jobs
  alive at the snapshot and capture `qstat -j <id> -xml` for each (about
  ten files).
- Route them in the fake by job id.
- One parametrized test runs `get_job_details` per state against its
  snapshot. Check the status from the listing, the details merged from
  `qstat -j`, and that the dr, dt and Eqw jobs come back from `qstat -j`
  at all.
- This makes `TestLifecycleStates.test_hpc_status_of_one_suspended_job`
  general.

### W6. The array rows in `hpc status` and `wait()` (G 1, 6, 24-26; J 6). Small.

- Add `array` (RUNNING, the reduction of its r, t and qw tasks) to the
  `SNAPSHOTS` mixed entry. `test_hpc_status_table` and
  `test_wait_holds_on..` then cover rows 1, 6, 24 and 25.
- Row 26: `hpc status <base>.2` over `qacct_array_task.txt`.

### W7. `wait()` edge cases (J 21, 22). Small. Row 23 is n/a (D2).

- Row 21 (sentinel only): use a responder with an empty qstat and
  qacct "not found", plus a sentinel file in `HPC_EXIT_DIR`. `wait()`
  returns the sentinel's status at once.
- Row 22: an unknown id gives UNKNOWN after `unknown_after`. An existing
  unit test covers this; reference it in the matrix.
- Row 23: a responder that fails N polls with the `master_down` capture,
  then answers. Under D2, `wait()` completes. A responder that never
  recovers raises once the limit has passed.

### W8. Monitor (H 11-27). Not test work, per D3.

If the Completed tab is built:
- feed it `list_completed_jobs` over the W3 responder;
- check the status column per finished state, as
  `TestJobTableFromCapturedSnapshots` does for active states;
- check the detail panel's cancel button is disabled for each.

Also fix the provider's catch-all (F3) so an unreachable scheduler shows
as unreachable.

## 4. Order

1. Remaining: W1, W2, W3, W4, W5, W6 and W7 (rows 21 and 22). None of
   them needs a decision or a `src/` change unless a test finds a bug.
   W2 and W5 need a capture run.
2. W8 is dropped (D3).

After these, every cell in §3.1 is `F`, `I` or `n/a`.
