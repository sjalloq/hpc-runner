# Test QA review: v0.13.1 .. v0.15.1

Date: 2026-10-31. Scope: every test added or changed between `v0.13.1` and
`v0.15.1` (18 commits, 64 test files, +8222/-2394 lines), reviewed for
whether the tests check behaviour or merely restate it. The TUI is out of
scope (it is to be reworked). Nothing was fixed in this review; the list
below is input for a decision pass. Section 2.13 onwards and the
extra questions in §4 come from a second-opinion review of this document
(2026-10-31), with each claim re-verified here.

Method: read tests against source; compare every fake with the real thing it
stands in for; run manual mutations (break the product, see what fails);
branch coverage of the unit suite. Unit suite at HEAD: 1018 passed, 20 s,
75% line / branch-partial 167.

## 1. Mutation results

38 mutations, 9 survived. A survivor is product code that can be broken
without any test noticing.

| # | Mutation | Result |
|---|---|---|
| C1 | resolve: spec layer applied first | killed (test_merge TestSpecLayer) |
| C2 | merge: `Set` appends instead of replacing | killed |
| C3 | merge: `Edit` never removes | killed |
| C4 | units: `G` = MiB | killed (CLI dry-run test) |
| C5 | expand: undefined `$VAR` expands to "" | killed |
| C6 | config: tool's own name always matches even with `commands` | killed |
| C7 | config: `<git-root>/hpc-runner.toml` never discovered | **SURVIVED** |
| C8 | result: `wait()` returns on first poll | killed |
| C9 | result: exit 113 maps to FAILED not SKIPPED | killed |
| P1 | run: unknown `--long` forwarded instead of rejected | killed |
| P2 | run: `-x cmd` without `--` accepted | killed |
| P3 | run: `--wait` without `--batch` accepted | killed |
| P4 | run: passthrough stored under wrong scheduler key | killed |
| P5 | run: `--array` no longer forces batch | killed |
| P6 | run: `--` not split, args before it join the command | killed |
| W1 | pipeline: per-step dependency type ignored | killed |
| W2 | pipeline: step name not prefixed with pipeline name | killed |
| W3 | pipeline: cycle not raised | killed |
| W4 | pipeline: a step's own `spec.dependencies` (outside the pipeline) dropped | **SURVIVED** |
| M1 | sge: drop `-now no` from qrsh argv | killed (exactly one test) |
| M2 | sge: swap `-o`/`-e` | killed (goldens + TestSGEOutputPaths) |
| M3 | sge: `memory_per="slot"` never divides | killed |
| M4 | sge parser: state `r` -> PENDING | killed |
| M5 | sge: skip qacct fallback in `get_statuses` | killed |
| M6 | sge: `qdel` with `check=False` | killed |
| M7 | sge: drop `-t task` from per-task qacct | killed |
| M8 | template: remove `\| sh` from `module load` | **SURVIVED** |
| M9 | sge: drop `-u *` from `list_active_jobs` | **SURVIVED** |
| M10 | sge: qrsh run with `capture=True` (output swallowed) | **SURVIVED** |
| M11 | sge: qrsh gets the 60 s query timeout instead of `None` | **SURVIVED** |
| M12 | sge: `-hold_jid` joined with `;` | killed |
| M13 | sge: `get_exit_code` ignores qacct | killed |
| M14 | sge parser: `qstat -j` stdout path not resolved against cwd | killed |
| M15 | sge: passthrough rendered one directive per arg | killed |
| M16 | seam: `FileNotFoundError` not converted | killed |
| M17 | local: `cancel()` returns True without terminating | **SURVIVED** |
| S1 | sge: sentinel sweep deletes every `.rc` regardless of age | **SURVIVED** |
| S2 | sge: sentinel sweep deletes nothing | **SURVIVED** |

## 2. Findings, most severe first

### 2.1 A golden file froze a real bug (GOLDEN-HYGIENE, product bug)

`tests/unit/golden/sge_batch_array_with_dependencies.sh:13` pins
`#$ -t 1-10:2%3`. `ArrayRange.range_str` (`core/array.py:49-56`) appends
Slurm's `%max_concurrent` suffix; SGE's `-t` grammar is `n[-m[:s]]` and
concurrency is a separate `-tc N`. qsub will reject the directive.
`test_array_job` (`test_sge.py:913-921`) also only checks the directive
string. The integration suite never submits an array with `%`. This is the
textbook case of a golden test guarding "what we emit" instead of "what the
scheduler accepts": the right fix is a per-scheduler rendering of the array
spec plus one integration test with `array="1-10%2"`.

### 2.2 The interactive path is pinned by one argv-prefix assertion (M1, M10, M11)

`test_sge.py:923-956` is the only test of the qrsh call and it inspects argv
only. Swallowing the user's terminal output (`capture=True`) or giving every
interactive session a 60 s timeout both pass the suite. Assert the seam's
kwargs (`timeout=None, check=False, capture=False, cwd=...`) exactly, as
`test_query_commands_get_default_timeout` already does for the query path.

### 2.3 `mock_sge_commands` answers by `cmd[0]` only (FAKE-FIDELITY, M9)

`tests/conftest.py:107-170` returns the same canned text whatever the
arguments. Its 14 users in `test_sge.py` (`test_submit_job`,
`test_cancel_job`, `test_get_status`, all `test_list_active_jobs*`, ...)
cannot distinguish `qstat -j 12345` from `qstat -xml` from a wrong id, so
dropping `-u *` from the listing argv survives. `TestSGEStatuses._respond`
(`test_sge.py:753-768`) shows the right pattern: the responder asserts the
expected argv. Either migrate those 14 tests to argv-asserting responders or
delete the ones that duplicate stronger tests later in the file
(`test_generate_script`, `test_submit_job` add nothing).

### 2.4 The canned SGE text is not what gridengine 8.1.9 emits (FAKE-FIDELITY)

The qacct block has no `failed` line and `exit_status  0` only; real qacct
writes `exit_status 137 (Killed)` on signals (BUG-T5) and `failed 19 : before
writing exit_status` for lost nodes (BUG-T3). `get_exit_code`
(`sge/scheduler.py:652-657`) does `int(exit_status)` and is never fed the
`"137 (Killed)"` form. The qstat XML fixture has no `<tasks>` element and no
`Eqw`/`hqw`/`dr` states; `parse_qstat_xml` keeps one entry per id so a
running+pending array collapses silently and nothing tests it. One-line
parser tests with the real forms would have caught T3 and T5 before the
testbed did. These are cheap to add now as xfail(strict) pins, flipping with
the fixes.

### 2.5 `bash -n` does not catch the quoting bug it is documented to catch (M8)

Removing `| sh` from `module load` in `_environment.sh.j2:54` is valid
shell, every golden uses space-free module names, `test_order` checks the
quoting of `module use` only, and `TestQuotingRoundTrip` uses a stub
`module` that accepts anything. The conftest comment should say "syntax
errors", and one round-trip case needs a module name containing a space.

### 2.6 Config discovery from the git root is untested (C7)

`find_config_files` with `<git-root>/hpc-runner.toml` and no
`HPC_RUNNER_CONFIG` has no test; `test_find_config_files_from_env_var`
tests the override only. `TestConfigDiscovery` also uses `os.chdir` into a
tmp dir rather than `monkeypatch.chdir`, and would misbehave if `TMPDIR`
were inside a git repo. Add a test that creates `tmp/.git`, a config at
that root, chdirs into a subdirectory and asserts both levels in order
(project, then local override).

### 2.7 Pipeline drops a step's external dependencies silently (W4)

`pipeline.py:178` starts from `pjob.spec.dependencies`, but no test gives
a step a dependency on a job outside the pipeline, so replacing that with
`[]` survives. Also untested: a `submit()` that raises part-way (what the
already-submitted steps look like; `test_partial_failure_can_retry` covers
retry, not the failure itself). The MagicMock scheduler is acceptable here
because `resolve()` runs for real and the contract to the scheduler is
only `submit(job)`; the two `assert_called_once()` at `:155` and `:242`
should check the Job passed.

### 2.8 Local `cancel()` is asserted only as `True` (M17)

`test_local.py:112-120`. Assert the status is no longer RUNNING, or that
the process has exited.

### 2.9 `hpc status` is tested only against a MagicMock scheduler

`test_cli/test_status.py` builds every table and JSON view from
`MagicMock()` return values (JobInfo lists the test invents). The rendering
is tested; the path from real `qstat -xml`/`qacct` text through the parser
to the table is not, in unit or integration. BUG-T2 (qacct owner summary
parsed as a job) lives exactly there. `get_job_details` (`scheduler.py:809-896`),
`list_completed_jobs` (`:744-803`) and `parse_qstat_j_xml` have no
happy-path unit test (coverage: scheduler 74%, parser 78% with 50 partial
branches, `cli/kill.py` 17%, `cli/cancel.py` 36%, `cli/config.py` 45%).
Fixture captures must come from the testbed, never the work cluster.

### 2.10 Integration suite: xfail and skip hygiene

- `test_failures.py::test_master_stopped` (BUG-T6) is `strict=False` and
  its "pass" path is a 60 s hang ending in `SchedulerTimeoutError`, which is
  not the behaviour wanted. An XPASS proves nothing and the T6 fix flips
  nothing visible; ~95 s per CI cell. Make it strict with `elapsed < 10`.
  The behaviour wanted is a fast `SchedulerError` built from qstat's
  stderr, so `elapsed < 10` is the oracle and the 60 s path must be a
  failure, not a pass.
- The other four xfails (`test_cwd_with_space`, `test_signal_exit_code`,
  `test_completed_listing_by_user`, `test_node_killed`) each have 3-6
  assertions before the one pinning the bug and no `raises=`, so a harness
  regression registers as the same xfail. Split the bug assertion out.
- Neither Makefile nor `addopts` passes `-r sxX`, so skips and xpasses never
  reach the CI log. The two failure tests skip when `sudo docker info`
  fails, and the slow-accounting cycle skips its only test unless
  `qconf -sconf` contains `flush_time=00:00:15`: both can silently reduce
  CI to zero without a red job. Fold them into `--require-cluster`.
- `--flavour` is free text and marker names are never validated, so a typo
  skips a test forever (unused today, D-T35).
- `test_interactive_no_slot` uses `waited > 5.0` as its oracle; the exit
  code is the real one and the timing is flake surface. Observe `qw` in
  qstat instead.

### 2.11 Live behaviours with no integration counterpart, all cheap

Merged stderr (`#$ -j y`; no job writes to stderr; `_assert_skipped`
already reads a file whose guard writes to stderr), separate `stderr=`,
`JobSpec(time=...)` enforcement (`test_h_rt_kill` relies on the queue's
`h_rt`), `memory_per="slot"`, `cancel()` of a pending or array job,
`env_passthrough` on the qrsh path (`-v` on argv), `hpc status <id>` and
`--history` on real output, `keep_script`, `JobResult.result()`.

Added by the second review:

- Reconnecting by id: `scheduler.result(job_id)` and `task_job_id` are the
  API for "the job I submitted yesterday", including output-path recovery
  from `qstat -j`. The integration suite holds every handle it creates, so
  this path is unit-only, against the canned text faulted in 2.3 and 2.4.
- Job-name sanitisation (`--name "9 lives"` -> `j9_lives` in `-N`,
  `$JOB_NAME` and the recorded output path) is documented in `sge.rst` and
  unit-tested at `test_sge.py:1024` and `:1091`, never submitted.
- `[tools.x] commands` has no live use: the testbed config has no
  `commands` entry, so `hpc run xmvlog` resolving to `[tools.xcelium]` and
  `hpc config resolve` showing the label are unit-only.
- `memory_per` cannot be proven live either way: the testbed's `mem_free`
  is a requestable, so `qstat -j` reads back whatever was sent and the slot
  division would be invisible. Proving "divide for a consumable" needs a
  consumable memory complex in `complexes.conf` and a readback that shows
  SGE's multiplication; that also tests the `h_vmem` claim in `sge.rst`.
- Detect-then-refuse for Slurm/PBS: `test_detect_slurm` proves detection
  returns `"slurm"` and `test_subprocess_seam.py:168` proves
  `get_scheduler("slurm")` raises, but nothing runs the chain a Slurm-site
  user would hit (`hpc run echo hi` with sbatch on PATH). The error should
  probably name `HPC_SCHEDULER=local` as the way out.

### 2.12 Smaller items

- `test_local.py` runs the rendered script, which exits 1 without
  Environment Modules; ~15 tests fail (not skip) on a bare host and the
  `requires_modules` marker is misleading. CI installs the package.
- `test_generate_script` asserts `"#$ -pe" in script` without slots or
  the `-l` values it set; `test_configurable_pe_name`, `test_scheduler_name`,
  `test_task_job_id` are attribute echoes. ~25 integration sites do
  `assert cluster.wait_for(id, S) is S`, which `wait_for` guarantees.
- `test_submit.py:70` pins SoGE's `'unknown queue "nope.q"'` wording.
- `test_status.py:105` truncates the shared accounting file mid-session;
  later tests depend on file order.
- `_check_sge_via_qstat` positive path (no `SGE_ROOT`, `qstat -help` says
  Grid Engine) is never exercised.
- `core/types.py` is unreferenced (0% coverage); delete or use.
- `test_smoke.py::test_batch_echo_hello` duplicates `test_submit.py::test_batch_echo`.
- `FORCE_COLOR=1` in the developer's shell fails 10 tests (re-run
  2026-10-31: 10 failed, 1008 passed): the CLI modules build `Console()`
  at import, before the `TERM=dumb` fixture runs. Green in CI, red on a
  laptop with a common setting. Fix is to build the console lazily or set
  `TERM` in `pytest_configure` before the CLI imports.
- `tests/unit/test_golden.py:47` pins `memory_per = "slot"` in the golden
  config, so the default `"job"` rendering is covered only by a string
  assertion in `test_sge.py`. The goldens are what a reviewer reads; one
  should show the default.

### 2.13 Untested code on the submit path and the extension point

- **Sentinel sweep** (`sge/scheduler.py:96-132`). `get_exit_dir` removes
  `.rc` files older than `sweep_ttl_days` on every call, against a shared
  directory. No test touches it: S1 (delete everything) and S2 (delete
  nothing) both survive. It needs a test with faked mtimes on both sides
  of the cutoff, and a check that it cannot remove the sentinel of a job
  still referenced by a pending `-hold_jid` child (the guard reads the
  parent's `.rc`; a parent that finished eight days ago with a child held
  on a full queue would lose it).
- **`register_scheduler`** (`schedulers/__init__.py:86`) is the documented
  site-plugin extension point and has no test: nothing exercises a
  registered name reaching `get_scheduler`, `_scheduler_config_keys` or
  `[schedulers.<name>]` validation.

### 2.14 Process holes the suite cannot see

- **Publishing is not gated on CI.** `publish.yml` runs on any `v*` tag and
  `needs: build` only. `v0.14.0` went to PyPI off a red unit-test run
  (the `GITHUB_ACTIONS` colour failure) and nothing stopped it. Options:
  `workflow_run` on CI success for the commit, or run the unit suite inside
  the publish job. One-line policy choice for the decision pass.
- **The integration job is advisory in practice.** Even with `-r sxX`,
  six minutes of log per Python version will not be read. A JUnit summary
  step or a hard floor ("at least N tests ran, 0 skipped outside the
  known list") turns a silently shrinking suite into a red job.

### 2.15 Deferred functionality: tested as absent, or just absent?

The decision pass should know which refusals have a test, because an
untested refusal becomes silent acceptance the day someone half-implements
the feature.

Tested as refused:

- `after` (start-after) raising `NotSupportedError` on SGE; interactive
  jobs with dependencies.
- Array jobs on the local scheduler; arrays always batch.
- `emulate_afterok = false` refusing afterok.
- `get_scheduler("slurm"|"pbs")` raising a clean `ConfigError`.

Not refused, mis-rendered: array concurrency on SGE (2.1). `range_str` is
shared by core and the SGE arg, so the fix is a per-scheduler render with
`-tc N` on SGE, and an integration case submitting `array="1-10%2"`.

Documented as not done, no test asserts anything: elementwise array
dependencies (`-hold_jid_ad`), `--mem-per-cpu`, D6 hooks, D16 lifecycle.
Acceptable, but written down here so the decision pass can say so.

## 3. What is strong

- `TestSGEDependencies.test_guard_runtime_behaviour` and friends render the
  real batch script and run it under bash with sentinel files: afterok /
  afternotok emulation, exit 113, published `.rc`, end to end.
- `TestSGEStatuses`: responder asserts exact qstat argv, multi-job XML with
  array `<tasks>`, exact `qacct -j 500 -t 1` sequence, `failed 37` -> TIMEOUT.
- `TestRunCommand`: the seam against real processes, all three exceptions.
- CLI `run` tests drive the real local scheduler and SGE dry runs, not a
  mocked scheduler; every passthrough rule died to a mutation.
- Core: every layer-order, merge-verb, expansion and unit mutation died,
  often to a CLI-level test as well as a unit one.
- Integration `test_env_order` (hostile values round-trip through
  expansion, the `sh` filter and a real shell), `test_queued_running_done`
  (wraps the real seam to count one qstat for N ids), `test_array_parent`.

## 4. Questions for the decision pass

1. Array concurrency on SGE: render `-tc N`, reject `%N` for SGE, or keep
   the Slurm syntax in `range_str` and translate per scheduler?
2. Add unit-level parser pins for BUG-T3/T5/T6 now as strict xfails, or
   wait for the fixes? The real qacct forms are already quoted in
   `TESTBED_REVIEW.md`, so the pins can be written from that text without
   new captures.
3. `test_master_stopped`: strict with `elapsed < 10` (hard xfail until T6)?
4. Make Docker and slow-accounting skips fail under `--require-cluster`?
5. Should `hpc status`/`--history` be exercised on real testbed output,
   via `cluster.hpc_cli`, and should a small set of testbed-captured
   qstat/qacct fixtures replace `mock_sge_commands`?
6. `test_local.py`: require Environment Modules (document it) or stub
   `module` throughout?
7. Sentinel sweep: is seven days the right TTL, and should the sweep skip
   sentinels whose job id still appears in `qstat` (pending children), or
   is that too expensive for a submit-path side effect?
8. Publish gating: `workflow_run` on green CI, or run the unit suite in
   `publish.yml`?
9. Integration floor: a minimum test count per cycle, or a JUnit summary
   step, or both?
10. `memory_per` live proof: add a consumable memory complex to the
    testbed (`complexes.conf`) and a second config, or accept unit-only?
11. Testbed config: add a `[tools.xcelium] commands = [...]` entry so the
    alias path and `hpc config resolve` run live?
12. Slurm/PBS detect-then-refuse: one CLI test with `sbatch` on a fake
    PATH, and should the error name `HPC_SCHEDULER=local`?
13. Can the unit suite, or at least everything it renders, be run against
    the testbed? The `%N` golden was wrong because nothing outside the
    package ever read it. Candidates: a CI step that runs the unit suite
    inside `login` with the real `qsub` on PATH; a test that feeds every
    golden script (and every script the conftest `bash -n` hook sees) to
    `qsub -w v`, which parses directives and verifies without submitting;
    or submitting each golden for real with a trivial command. Raised by
    the user during the Q&A; pick up at the end.

## 5. Decisions (Q&A, 2026-10-31)

Walked with the user, one question per message. These are the inputs to
the fix pass; the pass itself lands each fix with its tests.

1. **Array concurrency.** Keep the user grammar `START-END[:STEP][%MAX]`
   (Slurm and LSF spelling; every scheduler shares `:STEP`). Each scheduler
   renders all of its own grammar: SGE emits `-t START-END[:STEP]` and a
   separate `-tc MAX`; PBS will use `-W max_run_subjobs`; Slurm and LSF
   pass `%MAX` through. `range_str` no longer produces scheduler text.
   Unit test asserts both SGE directives, the golden changes (commit
   message says it was wrong), integration test submits `array="1-6%2"`
   and at least proves acceptance. Audit `core/` for other scheduler
   spellings while there.
2. **BUG-T3/T5/T6 pins.** This session fixes all three; each fix lands with
   its unit test written from the recorded testbed text, no xfail markers.
   T3 is not a one-line special case: parse the full SGE `failed` code
   set (table taken from the gridengine 8.1.9 source or `accounting(5)`
   in the testbed image, not from memory), keep the raw code and text on
   the result so `hpc status` can show it, map to status through one
   table (37 TIMEOUT, 24/25 not terminal, 100 ran-but-exit-unknown, the
   rest FAILED), parametrised test over the table.
3. **Qmaster down.** Minimal fix: in `_active_states`, a non-zero qstat
   whose stderr mentions qmaster/commlib/connection raises
   `SchedulerUnavailableError`; empty-queue tolerance kept. Unit test at
   the seam; integration xfail made strict, no timeout tuning.
4. **No conditional skips in the integration suite.** The testbed is the
   environment; a missing precondition is a broken testbed and the test
   fails. Remove the Docker and `flush_time` checks from test bodies.
   Variants are selected, not detected: `slow_accounting` marker,
   deselected on the default cycle, selected on the slow one. Flavour
   marker names validated at collection. Skip summary (`-r sxX`) printed.
   Question 9 (test-count floor) closed by this: nothing to count.
5. **`hpc status` on real output.** (a) Integration tests run the real
   `hpc status`, `--history` and `<id>` through `cluster.hpc_cli`. (b)
   Unit fixtures for the parsers are *generated from the testbed* by a
   `capture` subcommand (scenarios: running, pending, array mixed, Eqw,
   signal kill, lost node, owner summary), checked in under
   `tests/unit/fixtures/sge/`, regenerated on demand like the goldens;
   `mock_sge_commands` and the parser tests read them, responder selects
   by argv.
6. **Local scheduler.** Passed; its future is a separate conversation.
7. **Sentinel sweep.** TTL configurable under `[schedulers.sge]`, default
   30 days; unit test with faked mtimes either side of the cutoff; no
   qstat lookup at sweep time. (Sweep runs at scheduler construction,
   not per submit; the review text above is corrected by this note.)
8. **Publish gating.** Done by the other session (publish waits for a
   green CI run of the tagged commit).
9. Closed by 4.
10. **`memory_per` live proof.** Add a consumable memory complex to the
    testbed `complexes.conf` and a second config layer; integration test
    reads back SGE's per-slot multiplication for `"slot"` and pass-through
    for `"job"`; also verifies the `h_vmem` claim in `sge.rst`.
11. **`commands` live.** Add a `[tools.x] commands = [...]` entry to
    `hpc-runner-testbed.toml` using binaries that exist in the container;
    one integration test for the alias and `hpc config resolve`.
12. **Unimplemented schedulers.** No tests for detect-then-refuse; Slurm
    and PBS are to be implemented soon.
13. **Rendered output against the real qsub.** One integration test copies
    every SGE golden into `login` and runs `qsub -w v` on it (verify mode:
    directive parsing and resource check, no submission). Not submitting
    goldens for real, not running the unit suite inside the container.
    Plus the capture subcommand from 5b.

**Testbed driver.** Replace the Makefile with a Python entry point
(`hpc-testbed`, under the `test` extra) with subcommands `up`, `down`,
`pytest`, `shell`, `logs`, `capture`, driving Compose from Python
(testcontainers `DockerCompose` or plain `docker compose` via subprocess),
with `--slow-accounting`, `--py`, `--keep`. CI calls it. The inside-out
design stays: tests run in `login` because the product must call the real
`qsub`; the inner fixtures are unchanged. Prior art checked: Slurm's ATF
fixtures start daemons in one container (single node, so possible),
Flux's `docker-run-checks.sh` and OpenPBS's PTL are outer shell wrappers;
none has fixtures that create containers. Running the suite on a real
site login node was considered and parked: specialise the image instead.

## 6. Fix pass order (agreed 2026-10-31, each item one commit with tests)

**Done 2026-10-31**, seven commits c515697..08df189 on `main` in this
order. Beyond the list, the pass found and fixed: `ru_wallclock 7s`
unparsed (no runtimes in history), a running array listed as pending, a
lost job sorted off the history limit, `hpc status` reading `$USER` only,
`hpc status <id>` of a finished job ignoring qacct, and a consumable
`h_vmem` default of 0 giving unrequesting jobs a 10 MB limit (testbed).
T1, T2 and T4 remain (D-T36..D-T39 in the testbed review).

1. Array concurrency: per-scheduler render, `-tc` on SGE, golden
   corrected, integration test; audit `core/` for other scheduler text.
2. Testbed driver: `hpc-testbed` entry point replaces the Makefile, CI
   switched. Before 3-6 so new tests and `capture` land on the new driver.
3. No conditional skips; `slow_accounting` marker; flavour validation;
   `-r sxX`.
4. SGE parser: `failed` code table (T3), `137 (Killed)` (T5), qmaster
   down (T6); `capture` subcommand generates the unit fixtures;
   `mock_sge_commands` rewired to them. Largest item.
5. Sentinel sweep TTL, config key, test.
6. Testbed additions: consumable memory complex, `commands` entry,
   `hpc status` live tests, `qsub -w v` over the goldens.
7. Remaining unit gaps: qrsh seam kwargs, git-root config discovery,
   pipeline external dependencies, module-name quoting round trip, local
   `cancel()` if the local scheduler survives.
