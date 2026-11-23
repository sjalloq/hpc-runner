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
2026-11-16, brought up to date for Slurm on 2026-11-22. There is no PBS
backend (`schedulers/__init__.py` names a `pbs` module that does not
exist), so the PBS half of this cannot be closed today. It is the list of
what a backend must bring with it. For PBS nothing is planned.

### Integration suite (`tests/integration/`)

- The tests are sorted by folder: generic ones in `tests/integration/`
  (run on every scheduler, scheduler words from the cluster class), SGE's
  own in `tests/integration/sge/`, Slurm's own in
  `tests/integration/slurm/`. No generic test can run on PBS yet:
  - PBS: `cluster.config()` reads
    `testbed/<scheduler>/<flavour>/hpc-runner-testbed.toml`
    (`cluster.py`, `config_path`) and `pbs/openpbs` has no such file; its
    compose file does not export `HPC_RUNNER_CONFIG` either.
  - PBS: the members the generic tests ask of the cluster class
    (`cluster.py`, "the scheduler's own words") are not answered;
    `PBSCluster` raises `NotImplementedError`.
- The SGE and Slurm folders have no counterpart for PBS. The SGE tests
  with no Slurm test, each with its reason, are listed in
  `testbed/slurm/README.md`.
- Three of the four Slurm batch goldens ask for more than the Slurm testbed
  has (four CPUs, 8 GB, SGE flags in `scheduler_args`), so `sbatch
  --test-only` accepts one and refuses three with the expected verdict
  (`slurm/test_goldens.py`). The same holds for two of SGE's four.
- The Slurm suite has three slow tests: two wait out a one-minute time
  limit (about 105 s each) and one a requeue (about 155 s, Slurm's own
  delay before a requeued job starts again).
- A job on a killed node (`slurm/test_failures.py`): the exit code is not
  asserted beyond "not 0", because it differs between the versions and is
  not decided ("Slurm: facts from the captures" below).
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
- `tests/unit/fixtures/` holds `sge/soge` and `sge/ocs` (60 files each),
  `slurm/24.11` (158 files) and `slurm/22.05` (160 files), all taken again
  on 2026-11-22.
- The SGE captures were taken again on both flavours on 2026-11-22 (the
  first run since the containers were renamed; the run ended clean). The
  texts differ from the ones before in more than ids and times: the order
  of jobs in a snapshot, which array task is `r` and which `t`, column
  widths, host names. Nobody has read the whole diff.
- A waited SGE submission has no capture of a rejection (`qsub -sync y`
  refused); its test uses the text the other SGE tests type by hand
  (`QSUB_REJECT`, `test_subprocess_seam.py`).

### Unit suite

- Goldens exist for `sge_batch`, `sge_interactive`, `slurm_batch`,
  `slurm_interactive` and `local` (`tests/unit/golden/`, `CASES` in
  `test_golden.py`). None for PBS.
- Scheduler unit tests are `test_sge*.py`, `test_slurm.py` (rendering and
  submission), `test_slurm_reading.py`, `test_waited_submission.py` (both
  schedulers) and `test_local.py` (`tests/unit/test_schedulers/`).
- The unit tests that are not about one scheduler and run on every
  flavour of both: the waited submission, `TestExitCode` in
  `test_cli/test_run.py`, `test_trace/test_invocation.py`, `TestTraceFlag`
  in `test_trace/test_config_and_cli.py` and one status-column test in
  `test_tui/test_job_table.py`. The other tests that use `sge_fixtures`
  or `mock_sge_commands` are SGE's own.
- Every job state against every reader, with the test for each cell:
  `test-review/LIFECYCLE_MATRIX.md` for SGE, `slurm/LIFECYCLE_MATRIX.md`
  for Slurm. The Slurm table lists its empty cells in its section 3;
  nothing there is planned. There is no table for PBS.

### Testbed stacks

- What each stack lacks beside the SGE ones:
  - `hpc-runner-testbed.toml`: OpenPBS.
  - A support matrix of test against flavour: `testbed/sge/README.md` and
    `testbed/slurm/README.md` have one. There is no `testbed/pbs/README.md`.
- The three clusters are not configured alike, so a test cannot be
  replicated as it stands:
  - Time-limited queue: SGE `short.q` 1 minute (`cell/q-short.conf`),
    Slurm `short` 10 minutes (`slurm.conf:37`), OpenPBS none
    (no `create queue` in its `entrypoint.sh`).
  - Resources to request: Slurm and PBS have a licence `lic`, a GPU on
    exec1 and a feature `fast`; PBS also `scratch`. SGE has `gpu` and
    `xcelium_lic` in `complexes.conf`, no `fast`, no `scratch`.
- CI has cells for `soge`, `ocs`, Slurm `24.11` and Slurm `22.05`
  (`.github/workflows/ci.yml`, the `integration` matrix). The PBS image is
  never built there, so a broken one is not noticed.
- Not run since the container renaming of 2026-11-16: the CI workflow's
  log-copy step, rewritten 2026-11-22 to take the scheduler and the
  flavour from the matrix. The PBS, `ocs` and
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
  `137:0`. A batch shell killed by SIGKILL is `0:9` in both. Built: the
  exit code is read from `scontrol show job` while the controller knows
  the job. After that, and in `hpc status --history` always, it is
  `sacct`'s number: 137 shows as 9.
- A job whose node dies is requeued by default (`JobRequeue=1`): its
  `sacct` row is `PENDING` again, and `NODE_FAIL` is seen only with `-D`
  (`sacct_node_fail_requeued*.txt`). It ends `NODE_FAIL` only when
  submitted with `--no-requeue`.
- `NODE_FAIL` has `ExitCode 1:0` on 24.11 and `0:0` on 22.05 on the job's
  own row; the `.batch` row is `CANCELLED` with an empty `ExitCode`.
  `scontrol show job` says `0:1` on 24.11 and `0:0` on 22.05. So the exit
  code hpc-runner gives a job whose node died is 129 while the 24.11
  controller knows it, 1 after that, and none on 22.05.
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
- Of the items above only the first half of the `kill_invalid_dependents`
  one has an integration test (`slurm/test_dependencies.py`: the child
  stays PENDING on the testbed, whose own setting does not cancel it).
  Whether the others get one is not decided.

## The exit code of a batch job: to be revisited

The user may revisit this (2026-11-22: "it doesn't seem clean"). Built as
decided that day; everything below was run on the testbeds or is in the
captures.

What was decided:

- The exit code is what a shell would have reported for the job's command.
- `hpc run --batch --wait` and `submit(job, wait=True)` submit with the
  scheduler's own wait (`sbatch --wait`, `qsub -sync y`) and return what
  it returns. hpc-runner does not poll and then ask.
- `result.wait()` on a job submitted earlier polls, then reads the code
  from the scheduler's records.

Why it is not clean: one job can have three different numbers, depending
on who is asked.

| Job (Slurm, both versions) | `sbatch --wait` returns | `scontrol show job` | `sacct` |
|---|---|---|---|
| Script exits 3 | 3 | `3:0` | `3:0` |
| Script exits 137, or its tool is killed by SIGKILL | 137 | `137:0` | `9:0` |
| Tool segfaults | 139 | not captured | `11:0` |
| Script's own shell killed by SIGKILL | 1 | `0:9` | `0:9` |
| Time limit | 1 | `0:15` | job row `0:0`, `.batch` row `0:15` |
| Cancelled while running | 1 | `0:15` | job row `0:0`, `.batch` row `0:15` |
| Over its memory | 1 | `0:125` | `0:125` |
| Cancelled while pending, or skipped | 0 | `0:0` | `0:0` |

So, as built:

| Where the user looks | Time limit | Over memory | Exit 137 |
|---|---|---|---|
| `hpc run --batch --wait`, `submit(job, wait=True)` | 1 | 1 | 137 |
| `result.wait()` then `result.returncode`; `hpc status <id>` while Slurm remembers the job | 143 | 253 | 137 |
| `hpc status <id>` after Slurm has forgotten the job; `hpc status --history` always | 143 | 253 | 9 |

- The cause of the last column: `sacct` takes 128 off an exit status above
  128 when it prints (`src/sacct/print.c`, "if (tmp_int >= 128) tmp_int -=
  128", in 22.05.8 and 24.11.5). The stored value is right; `scontrol`
  prints it as it is.
- The 0 that `sbatch --wait` and `qsub -sync y` return for a job that never
  ran or was deleted is never passed up: the status is read after the wait
  and a job that did not complete exits 1.
- SGE has one number everywhere: `qacct` holds 137 as 137, and `qsub -sync
  y` returns it. What SGE costs instead: on `ocs` 9.1.7 an ordinary
  user's `qsub -sync y` returns 32 s after the job has ended, every job
  (a manager's in 1 s; `soge` in 1 s for anyone); an interrupted `qsub
  -sync y` exits 1 and removes the job, where `sbatch --wait` exits 130
  and leaves it running.
- Getting the job id at submit time needed a pseudo-terminal on the waiting
  command's stdout: `qsub -sync y` writing to a pipe prints the id only
  when the job has ended.
- Not run: Ctrl-C during a waited `hpc run` on either scheduler. For
  Slurm it is an item of `slurm/MANUAL_CHECKLIST.md`.

Undecided: the exit code of a job that never ran (user, 2026-11-22).

- Built: `None`, in both the polled and the waited result. The waiting
  submit returns 0 for such a job (Slurm: a skipped dependent, a job
  cancelled while pending; SGE: a job deleted before or while it ran) and
  `_waited` (`schedulers/base.py`) turns a 0 on a job that did not
  complete into `None`. `hpc run` prints the status and exits 1.
- The alternative is 1, as `hpc run` exits.

| Caller's check | `None` | 1 |
|---|---|---|
| `if result.exit_code == 0:` success | not success | not success |
| `if not result.exit_code:` success | wrongly success | not success |
| Can tell "never ran" from a tool that exited 1, by the number | yes | no |

- Either way the status says what happened (`SKIPPED`, `CANCELLED`) and
  `result.result()` raises `JobFailedError`.
- Uneven today: a skipped job on SGE has `exit_code` 113 in a waited
  Python result (the dependency guard's code, which `qsub -sync y`
  returns) and `None` on Slurm. `hpc run` exits 1 on both. The user said
  on 2026-11-17 that the special codes are internal to hpc-runner.

Ways it could be made one number, none chosen:

- Make the waited form agree with the records: after the wait, report the
  code from `scontrol` (143 for a time limit) and use the scheduler's
  return only for what the records get wrong.
- Have the job script write its own exit code to a file on the shared
  filesystem and read that everywhere, as SGE's dependency guard already
  does for skipped jobs.
- Correct `--history` with one `scontrol show job` for jobs Slurm still
  remembers.

## Left open by the reading step and the waited submission (2026-11-22)

Read from the captures or run on the testbeds that day. Nothing here is
decided.

- `hpc run --batch --wait` exits with what the scheduler's waiting submit
  returns, and a handle polled later gives the code in accounting. They
  differ for a job the scheduler ended. Slurm, both versions: time limit 1
  against 143, cancelled while running 1 against 143, out of memory 1
  against 253.
- After the waiting submit returns the status is read with the polling
  wait, not with one query. Seen on 22.05: straight after `sbatch --wait`
  of an array returned, `sacct` had one row, named by the bare job id, in
  place of the three task rows. On SGE the accounting record of a job is
  written some time after `qsub -sync y` returns. One query is what
  happens when the record is already whole.
- A job deleted while pending under `qsub -sync y`: `qsub` exits 0 and
  there is never an accounting record, so `hpc run --batch --wait` prints
  UNKNOWN and exits 1 only after the 60 s the wait gives an unknown job.
- `sacct -S <time>` leaves out a job that was never eligible to run: one
  held, or waiting on a dependency, until it was cancelled (`sacct_history.txt`
  has no `cancelled_pending`, `dependent_skipped` or `array_cancelled_pending`).
  So `hpc status --history` on Slurm does not show a skipped job. A job
  cancelled while pending for resources is shown.
- `hpc status <id>` shows Slurm's own state (`OUT_OF_MEMORY`,
  `CANCELLED by 1003`) only in the extra details: with `--verbose`, and
  under `details` in `--json`. The plain table of a job killed for memory
  says KILLED and nothing more.
- `hpc status <id>` of an array lists every task, also while tasks are
  pending or running. SGE lists tasks for a finished array only.
- `hpc status`, `hpc monitor` and `hpc cancel` see an array as one entry
  under its own id, as on SGE. One task cannot be cancelled by `hpc
  cancel <id>_<n>`: the id is not in the listing.
- `JobInfo.memory` is not filled on Slurm. `sacct` ReqMem and `squeue`
  MinMemory are per CPU or per node depending on how the job asked, and
  the early row of 22.05 prints `16?`.
- `scancel` of an id that never existed exits 0 without a word
  (`scancel_missing.json`), so `cancel()` returns True for it.
- The waiting submit command's stdout is a pseudo-terminal, because `qsub
  -sync y` writing to a pipe prints the job id only when the job has
  ended. Not run: whether a site's `qsub` or `sbatch` wrapper behaves
  differently when its stdout is a terminal.
- Slurm states with no capture, mapped from the names `squeue --helpstate`
  lists on 24.11.5 (22.05 has no such option): `REQUEUE_HOLD`,
  `REQUEUE_FED`, `RESV_DEL_HOLD`, `CONFIGURING`, `COMPLETING`,
  `SIGNALING`, `STAGE_OUT`, `RESIZING`, `STOPPED`, `BOOT_FAIL`,
  `SPECIAL_EXIT`, `PREEMPTED`, `REVOKED`, `DEADLINE`. `REQUEUED` is seen
  only with `sacct -D`, which hpc-runner does not ask for.

## Slurm: found while writing the integration suite (2026-11-22)

Run on 24.11 and 22.05 through hpc-runner in the `login` containers.
Nothing here is decided.

- `hpc status --history` did not list a job for about a second after
  `sacct -j <id>` had it as FAILED (22.05, seen once; the cause was not
  looked for). The tests poll the history.
- A `time` longer than the partition allows is accepted by `sbatch` and the
  job pends for ever (`short`, `time = "20m"`: reason `PartitionTimeLimit`
  on 22.05). `hpc run --batch --wait` of one would not return. Not tested.
- `sacct -X` straight after `scontrol requeue` said `REQUEUED` on 22.05 and
  `PENDING` on 24.11; hpc-runner reports PENDING for both.

## Slurm: left out of the user docs for want of a test (2026-11-22)

`docs/source/slurm.rst` states only what a test proves. Each item below is
something the plan wanted said, or a reason the page would have given,
that no test proves. It is unproven until the test named exists; the page
then gets the sentence. Items the testbed cannot show are in
`slurm/MANUAL_CHECKLIST.md`.

- An interactive job with `inherit_env = false` has `HOME`, `USER` and
  `LOGNAME` empty and bash's default `PATH` (`srun --export=NONE` builds
  no login environment). Seen in research runs only. The page gives the
  advice (name them in `env_passthrough`) and not the fact. Test: an
  interactive job in the testbed that prints the three. Note "Testbed
  stacks" above: a job started through `docker exec` gets no `USER` or
  `LOGNAME` from the caller, so the test must set them first.
- `SBATCH_*` variables in the submitting shell override the `#SBATCH`
  lines hpc-runner writes. Seen in a research run only. The page says not
  to combine them and does not say what happens. Test: `SBATCH_PARTITION`
  set to `short`, a job with `queue = "all"`, `scontrol show job` says
  which partition.
- `${hpc.job_id}` in the job script is the id `sbatch` printed for every
  task of an array. The rendering (`${SLURM_ARRAY_JOB_ID:-$SLURM_JOB_ID}`)
  is tested and the value on a node is not; the output-path form (`%A`)
  is proven by `test_array_1_to_5`. Test: an array whose tasks echo
  `${hpc.job_id}` through `env_vars`.
- `$TMPDIR` on Slurm is shared between jobs and not cleaned. The page
  says only that hpc-runner makes and removes nothing. The testbed sets
  no `TMPDIR`; this is a manual check.
- `scheduler_args` with `--nodes` or `--ntasks` replaces the
  `--ntasks=1` hpc-runner always sends. The page tells an MPI job to use
  `scheduler_args` and shows an example; that the later flag wins is not
  proven. Test: `scheduler_args = ["--ntasks=2"]`, then `NumTasks` in
  `scontrol show job`.
- What `SRUN_CPUS_PER_TASK` and `SLURM_EXPORT_ENV=ALL` do for an `srun`
  inside a job. The page says the script exports them, and what for, and
  nothing about their effect. Test: a job whose command is `srun env`
  with a module loaded, and one with `srun nproc`.
- `--mem=0` means the whole node, which is why the request is never 0.
  The page says "never 0" with no reason.
- `sbatch --export=NONE` still gives the job the user's login environment
  on the node. The page says only that the submitting shell's variables do
  not reach the job.
- The default script directory, `~/.cache/hpc-runner/scripts`
  (`schedulers/base.py`, `get_script_dir`): every test sets
  `HPC_SCRIPT_DIR`. The page names the variable only.
- `scancel <array id>` cancels every task: the capture shows it
  (`sacct_array_throttled_cancelled.txt`) and no test says so task by
  task. The page says the array is then CANCELLED.
- The `after` dependency type is rendered (`--dependency=after:<id>`) and
  has never been run on the testbed.
- How long the controller remembers a finished job, and how far back
  accounting goes: site settings, in the manual checklist.
- `hpc monitor` beyond its listing (the detail panel, the Completed tab)
  has not been run on Slurm: manual checklist.

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
