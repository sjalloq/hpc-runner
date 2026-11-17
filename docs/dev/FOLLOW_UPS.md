# Follow-ups

Things known to be missing, unverified or undecided that belong to no
current plan. Slurm build work is in `slurm/PLAN.md`, not here: where an
item below has a Slurm half, the plan's step is named and this file is
only the inventory.

- Each item says what is open, where it was seen, and what closes it.
- An item is deleted when it is done or when it moves into a plan.
- Add an item whenever work is left unported, unrun or undecided. Nothing
  is left for someone to find by accident.

## Tests and testbed: one scheduler's setup with no counterpart

The rule (user, 2026-11-16): every test is replicated on SGE, Slurm and
PBS. The only exceptions are a feature a scheduler does not have, and a
feature that has to be tested differently on it. Each exception is written
down with its reason.

Where things stand, from a pass over `testbed/`, `tests/` and CI on
2026-11-16. There is no Slurm or PBS backend yet (`schedulers/__init__.py`
names `slurm` and `pbs` modules that do not exist), so most of this cannot
be closed today. It is the list of what a backend must bring with it.
For Slurm that work is steps 3, 4 and 8 of `slurm/PLAN.md`, "Order of
work". For PBS nothing is planned.

### Integration suite (`tests/integration/`)

- 48 of the 49 tests are marked `scheduler("sge")`: every module except
  `test_smoke.py`. On Slurm or PBS the whole suite is one test.
- Even that one cannot run there: `cluster.config()` reads
  `testbed/<scheduler>/<flavour>/hpc-runner-testbed.toml`
  (`cluster.py`, `config_path`), and only `sge/soge` and `sge/ocs` have
  the file. The Slurm and PBS compose files do not export
  `HPC_RUNNER_CONFIG` either.
- The marker is per module, so it hides which tests are about SGE and
  which are about hpc-runner. A first sorting, by reading the modules'
  SGE-specific lines (not every test body; to be confirmed per test when
  ported):
  - Promise the same thing on every scheduler, marked SGE only because
    the body uses SGE words (`$SGE_TASK_ID`, `qstat`/`qacct` checks,
    `all.q`, `-l hostname=`): `test_arrays`, `test_dependencies`,
    `test_environment`, `test_listing`, `test_cli`, `test_trace`,
    `test_failures` (node killed), most of `test_submit` and
    `test_status`. These need the scheduler's words moved behind the
    cluster class, or a counterpart per scheduler.
  - Same feature, tested differently: `test_goldens` (`qsub -w v`; Slurm
    has `sbatch --test-only`), `test_resources` (PE, `h_vmem`, `h_rt`,
    queue selection), `test_passthrough`, `test_hold_flag_forwards`.
  - Possibly SGE only, each needs a decision and a written reason:
    `test_slow_accounting_window`, `test_memory_per_consumable`,
    `test_aged_out`, `test_deleted_while_pending_waits_out_to_unknown`.
- `TestbedCluster.accounting_truncate` is implemented for SGE only; the
  Slurm and PBS classes inherit `NotImplementedError`
  (`cluster.py`), though `testbed/README.md` lists it as part of a new
  scheduler's class.
- `--slow-accounting` exists for SGE only
  (`SLOW_ACCOUNTING_SCHEDULERS`, `_testbed.py:37`). Slurm needs none
  (`slurm/PLAN.md`, "Status and exit codes"); PBS is not measured.

### Captures and fixtures

- `hpc-testbed capture` works for SGE only (`CAPTURE_MODULES`,
  `_testbed.py:39`). `tests/integration/capture.py` is one 600-line SGE
  script; nothing in it is shared.
  - Slurm: planned (`slurm/PLAN.md`, "Order of work" steps 4 and 5).
  - PBS: not planned anywhere.
- `tests/unit/fixtures/` holds `sge/soge` and `sge/ocs` (46 files each)
  and nothing else.
- The helpers that serve fixtures are SGE by name and by content:
  `SGEFixtures`, `make_sge_responder` (`tests/fakes.py`), the
  `sge_fixtures` and `mock_sge_commands` fixtures (`tests/conftest.py`).
  The tests that are not about SGE but run on them see only SGE answers:
  `test_tui/test_job_table.py`, `test_trace/test_invocation.py`,
  `test_trace/test_config_and_cli.py`.
- `capture` has not been run since the containers were renamed
  (2026-11-16). Its two `pause_node`/`unpause_node` calls are new and
  untested. Closes with one run of `hpc-testbed --scheduler sge capture`
  and an empty or explained fixture diff.

### Unit suite

- Goldens exist for `sge_batch`, `sge_interactive` and `local`
  (`tests/unit/golden/`, `CASES` in `test_golden.py`). None for Slurm or
  PBS.
- Scheduler unit tests are `test_sge*.py` and `test_local.py` only
  (`tests/unit/test_schedulers/`).
- `test-review/LIFECYCLE_MATRIX.md` (every job state against every reader,
  with the test for each cell) covers SGE only. It is what caught the
  gaps on SGE; there is no such table for another scheduler.

### Testbed stacks

- What each stack lacks beside the SGE ones:
  - `hpc-runner-testbed.toml` (and the second-layer
    `-consumable.toml`): Slurm 22.05, Slurm 24.11, OpenPBS.
  - A support matrix of test against flavour: only `testbed/sge/README.md`
    has one. There is no `testbed/slurm/README.md` or
    `testbed/pbs/README.md`.
- The three clusters are not configured alike, so a test cannot be
  replicated as it stands:
  - Time-limited queue: SGE `short.q` 1 minute (`cell/q-short.conf`),
    Slurm `short` 10 minutes (`slurm.conf:37`), OpenPBS none
    (no `create queue` in its `entrypoint.sh`).
  - Resources to request: Slurm and PBS have a licence `lic`, a GPU on
    exec1 and a feature `fast`; PBS also `scratch`. SGE has `gpu` and
    `xcelium_lic` in `complexes.conf`, no `fast`, no `scratch`.
- CI runs the integration job for `soge` and `ocs` only
  (`.github/workflows/ci.yml`, the `integration` matrix). The Slurm and
  PBS images are never built there, so a broken one is not noticed.
- Not run since the container renaming of 2026-11-16: the PBS stack (not
  started at all), the `ocs` integration suite, the CI workflow's
  log-copy step.
- The work of 2026-11-17 (`hpc run` exit code, the five removed options,
  the array output file name, the option table) has run on `soge` only.
  Closes with one run of the `ocs` integration suite.
- A third SGE flavour, `daimh/sge`, is not started.
- PBS has one flavour (OpenPBS). OpenPBS against PBS Professional is not
  examined.

## Trace

- A record does not say which scheduler release produced the output.
  Raised 2026-11-15, not decided.

## PBS

No backend is planned. These are for when one is.

- PBS records -29 for a job that hit its time limit, in place of a
  signal. What exit code hpc-runner reports for it is not decided.
- Not run on the testbed: host exclusion, soft requests, `place=scatter`,
  why `qsub -V` did not carry a variable.

## LSF

- Dropped: there is no way to get a cluster to test on.

## Core

- Pipeline lifecycle after submission (restructure decision D16) is
  parked. One question hangs off it: what "pipeline retry" means.
- Stale exit sentinels on job id reuse could mislead the afterok guard
  (noted under D19, not decided).

## `hpc monitor`

- A rework is intended. No plan exists.
