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
2026-11-16. There is no PBS backend (`schedulers/__init__.py` names a
`pbs` module that does not exist), and the Slurm backend renders and
submits but reads no status yet, so most of this cannot be closed today.
It is the list of what a backend must bring with it.
For Slurm that work is step 2 of `slurm/PLAN.md`, "Order of
work". For PBS nothing is planned.

### Integration suite (`tests/integration/`)

- The tests are sorted by folder: generic ones in `tests/integration/`
  (run on every scheduler, scheduler words from the cluster class), SGE's
  own in `tests/integration/sge/`. No generic test can run on Slurm or PBS
  yet:
  - Slurm has its testbed config and its cluster class; what is missing is
    the backend's reading of status, exit codes and listings
    (`slurm/PLAN.md`, "Order of work").
  - PBS: `cluster.config()` reads
    `testbed/<scheduler>/<flavour>/hpc-runner-testbed.toml`
    (`cluster.py`, `config_path`) and `pbs/openpbs` has no such file; its
    compose file does not export `HPC_RUNNER_CONFIG` either.
  - PBS: the members the generic tests ask of the cluster class
    (`cluster.py`, "the scheduler's own words") are not answered;
    `PBSCluster` raises `NotImplementedError`.
- The SGE folder has no counterpart for another scheduler. Same feature,
  to be tested the other scheduler's way: `test_goldens` (`qsub -w v`;
  Slurm has `sbatch --test-only`), `test_resources` (PE, `h_rt`),
  `test_passthrough`, `test_hold_flag_forwards`, `test_node_killed`,
  `test_deleted_while_pending_waits_out_to_unknown` (Slurm gets its own,
  expecting CANCELLED). SGE only, with the reason in
  `testbed/sge/README.md`: `test_slow_accounting_window`, `test_aged_out`,
  `test_memory_per_consumable`, `test_h_vmem`.
- `TestbedCluster.accounting_truncate` is implemented for SGE only. Slurm
  has no answer (`testbed/slurm/README.md`); the PBS class inherits
  `NotImplementedError` (`cluster.py`) with no reason written.
- `--slow-accounting` exists for SGE only
  (`SLOW_ACCOUNTING_SCHEDULERS`, `_testbed.py:37`). Slurm needs none
  (`slurm/PLAN.md`, "Status and exit codes"); PBS is not measured.

### Captures and fixtures

- `hpc-testbed capture` works for SGE and Slurm (`CAPTURE_MODULES`,
  `_testbed.py`). `tests/integration/capture.py` is one 600-line SGE
  script; nothing in it is shared with `tests/integration/capture_slurm.py`.
  - Slurm: the module takes every scenario of `slurm/PLAN.md`, "Captures
    to take", on both flavours (`testbed/slurm/README.md`, "Captures").
  - PBS: not planned anywhere.
- `tests/unit/fixtures/` holds `sge/soge` and `sge/ocs` (46 files each),
  `slurm/24.11` (119 files) and `slurm/22.05` (121 files). Of the Slurm
  ones only the `sbatch_*.json` files are read (`test_slurm.py`: the job
  id, the two rejections); `test_slurm_capture_files.py` checks the file
  set.
- Slurm texts no capture provides:
  - A completed-jobs listing (`sacct` by user and start time, what
    `hpc status --history` will ask). The argv is the backend's to choose;
    the capture follows it.
- `hpc-testbed capture` now empties the fixture folder before it copies.
  Not run on SGE since that change.
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

- Goldens exist for `sge_batch`, `sge_interactive`, `slurm_batch`,
  `slurm_interactive` and `local` (`tests/unit/golden/`, `CASES` in
  `test_golden.py`). None for PBS.
- Scheduler unit tests are `test_sge*.py`, `test_slurm.py` (rendering and
  submission only) and `test_local.py` (`tests/unit/test_schedulers/`).
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
- Not run since the container renaming of 2026-11-16: the CI workflow's
  log-copy step. The PBS, `ocs` and
  Slurm 22.05 stacks were each brought up and down cleanly on 2026-11-17
  (flag research).
- Cases the stacks cannot show, found by the flag research of 2026-11-17:
  - `soge` has no project, no capacity for `xcelium_lic` and no host with
    `display_win_gui` set, so `-P`, licence counting and features ran on
    `ocs` only (with a config change put back). A request for one of them
    on `soge` pends for ever.
  - OpenPBS: the GPU node exec1 is also every job's default node, so a GPU
    request cannot be seen moving a job; `qstat` is not on `PATH` inside a
    `qsub -I -- script` job; `qsub -I` needs a terminal (`script -qec`
    stands in for one).
  - Jobs started through `docker exec` get no `USER` or `LOGNAME` from the
    caller, so an environment check must not read those as the job's own.
- A third SGE flavour, `daimh/sge`, is not started.
- PBS has one flavour (OpenPBS). OpenPBS against PBS Professional is not
  examined.

## SGE: facts from the flag research (2026-11-17)

Found by the flag research, run on `soge` and `ocs`. Nothing is decided.

- `env_passthrough` values reach the job wrong in two cases. A comma splits
  the value into a second variable, on `qsub` (`batch.sh.j2:8`) and on
  `qrsh` (`sge/scheduler.py:623-624`). An apostrophe is lost in the `#$ -v`
  line. `_check_directive_values` does not look at `env_passthrough`.
- Licence and device counts are multiplied by the slot count when the site's
  complex is a `YES` consumable (`-pe smp 2 -l gpu=1` needs 2 gpus).
  `_requests` sends the count unchanged; `mem` has `memory_per` for this,
  licences and devices have nothing.
- `_check_directive_values` says `#$` lines have "no quoting" (docstring
  `sge/scheduler.py:472-473`, error text :495). Single quotes are honoured.
- The always-sent `#$ -S /bin/bash` has no effect in a `unix_behavior`
  queue; the script's `#!/bin/bash` is what makes it bash.

## Slurm: facts from the flag research (2026-11-17)

Run on 24.11 and 22.05. Each bears on a line of `slurm/PLAN.md`; nothing
is decided.

- A dependency on a job the controller no longer knows: 24.11 refuses the
  submission, 22.05 accepts it and runs the job at once. The plan says only
  that this is not supported.
- `sbatch --export=NAME=value,...` builds a login environment, as
  `--export=NONE` does. The plan says so only for NONE.
- `srun --export=NONE` builds no login environment on 22.05 either; the
  plan says "24.11 only".
- `srun --pty` with no terminal does not fail: two error lines, then it
  runs without a pty.
- 22.05 `srun` does not set `SLURM_MEM_PER_NODE` or `SLURM_MEM_PER_CPU` in
  the task; 24.11 does.
- Not shown on the testbed: `--mem-per-cpu` against `MaxMemPerCPU`.

## Slurm: facts from the captures (2026-11-22)

Read from `tests/unit/fixtures/slurm/`, both flavours unless one is named.
Each bears on a line of `slurm/PLAN.md`; nothing is decided.

- `sacct` prints an exit status above 128 less 128: a script that exits 137
  is `ExitCode 9:0` (`sacct_exit137.txt`); `scontrol show job` says
  `137:0`. A batch shell killed by SIGKILL is `0:9` in both. The plan's
  rule gives 9 for the first and 137 for the second.
- A job whose node dies is requeued by default (`JobRequeue=1`): its
  `sacct` row is `PENDING` again, and `NODE_FAIL` is seen only with `-D`
  (`sacct_node_fail_requeued*.txt`). It ends `NODE_FAIL` only when
  submitted with `--no-requeue`.
- `NODE_FAIL` has `ExitCode 1:0` on 24.11 and `0:0` on 22.05 on the job's
  own row; the `.batch` row is `CANCELLED` with an empty `ExitCode`.
- On 22.05 the early `sacct` row of a running job says `PENDING` while its
  `.batch` row says `RUNNING` (`sacct_running_stub.txt`). The plan says
  the state in that row is right.
- On 22.05 a held array cancelled before it starts is one row named
  `<id>_[1-4]` (`sacct_array_cancelled_pending.txt`). The plan says the bare
  id.
- The `sacct` Reason column differs between the versions for more than
  dependencies: a held job is `None` on 24.11 and `JobHeldUser` on 22.05;
  the finished `dependent_ok` job is `Dependency` on both.
- `scontrol show job` of a pending array gives `StdOut` with the task id
  unfilled (`array.<id>.4294967294.out`); each started task has its own
  record with the path filled in.
- `sacct` Timelimit is `Partition_Limit` for a job that has not started
  and, on some rows of jobs that started, `365-00:00:00` where the
  submission set no limit. Which rows changes from run to run.
- `sacct` AllocCPUS of a pending job is 0 on 24.11 and the CPUs asked for
  on 22.05.

## Slurm: left open by the rendering step (2026-11-22)

Run on 24.11 and 22.05 while the backend's rendering was built. Nothing
here is decided; where something was built to keep a job from going wrong
silently, it says so.

- `${hpc.host}` is `$HOSTNAME` (`slurm/PLAN.md`, "`${hpc.*}` variables").
  In an interactive job that inherits the environment it is the submit
  host's name: `srun` passes `HOSTNAME=login` on. `$SLURMD_NODENAME` was
  the node in all four cases run (`sbatch` and `srun`, `--export=ALL` and
  `NONE`).
- A `#SBATCH` line does not carry every character: `'`, `"` and `\` are
  taken out of the value, `#` ends the line, an unmatched `'` is fatal.
  The plan refuses whitespace only. Built: those four are refused with
  whitespace (`_check_directive_values`, `slurm/scheduler.py`) and become
  `_` in a job name.
- The refusal message is Slurm's own wording, not SGE's text: SGE's names
  SGE and `#$`.
- An `env_passthrough` value with a space is not refused on an SGE batch
  job (`_check_directive_values` there does not look at it) and is refused
  on a Slurm one.
- Several features are sent as `-C a&b`. The plan joins licences and
  devices and does not say how features are joined. Run: `-C fast&fast`
  is accepted, `-C fast&nosuch` is rejected. Two different features on
  one node were not run; the testbed has one feature.
- Two different licence names or device names in one option were not run:
  the testbed has one of each.
- A `%` in a user's output pattern is read by Slurm as one of its own
  symbols; the path recorded on the result is then not the file written.
  Nothing is built for it.
- `kill_invalid_dependents = false` sends no `--kill-on-invalid-dep`, so
  the site's own setting decides. A site set to cancel such jobs was not
  run.
- The plan says `SRUN_CPUS_PER_TASK` is needed on 22.05. Not reproduced
  here: with cgroups on, an `srun` step inside a two-CPU job saw two CPUs
  without it on both versions. It is exported as the plan says.
- Nothing above has an integration test; they belong with step 2 of
  `slurm/PLAN.md`.

## Trace

- A record does not say which scheduler release produced the output.
  Raised 2026-11-15, not decided.

## PBS

No backend is planned. These are for when one is.

- PBS records -29 for a job that hit its time limit, in place of a
  signal. What exit code hpc-runner reports for it is not decided.
- Not run on the testbed: host exclusion, soft requests, `place=scatter`.
- `qsub -V` adds nothing in OpenPBS 23.06.06: `qsub.c:env_array_to_varlist`
  returns NULL on every path.
  `-v` works.
- An interactive job's exit code cannot be recovered: `qsub -I` exits 0 and
  the job records `Exit_status=0` whatever the script exits with. Only batch
  `qsub -W block=true` returns it.
  How a PBS backend runs interactive jobs is not decided.

## LSF

- Dropped: there is no way to get a cluster to test on.

## Core

- Pipeline lifecycle after submission (restructure decision D16) is
  parked. One question hangs off it: what "pipeline retry" means.
- Stale exit sentinels on job id reuse could mislead the afterok guard
  (noted under D19, not decided).

## `hpc run` (user report, 2026-11-17)

Not decided.

- A job-side value cannot be put in the command line.
  `hpc run cargo build -j $NSLOTS` sends an empty `-j`. A quoted `$NSLOTS`
  or `${hpc.cpus}` reaches the script as a literal string, because the
  command is `shlex.join`ed and never expanded (`resolve.py:98`). Only
  `bash -c '…'` works, and only on SGE.
- `cpu` takes no range (`cpu = "8-16"` → `-pe smp 8-16`). It worked
  unchecked until the typed schema (`43d6488`, v0.14.0) and was dropped
  with no decision recorded.
- An interactive `--dry-run` shows neither the CPU count nor the qrsh
  arguments (`_show_dry_run`, `cli/run.py:322`).

## `hpc monitor`

- A rework is intended. No plan exists.
