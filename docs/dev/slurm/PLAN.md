# Slurm backend: the plan

Facts behind it:

- Slurm against SGE: `SGE_SLURM_DIFFERENCES.md`.
- Resource requests on SGE, Slurm, LSF, PBS: `RESOURCES_COMPARISON.md`.
- Both were re-checked claim by claim against vendor docs and source on
  2026-11-13. What could not be confirmed is marked UNVERIFIED in place.
  SGE, Slurm (24.11, 22.05) and OpenPBS claims were then run on the
  testbeds. Nothing has been run on LSF.

## How options work, on every scheduler

hpc-runner accepts options (`cpu`, `mem`, `time`, `queue`, ...) and maps
them to the underlying scheduler's arguments.

- An option means the same thing on every scheduler.
- Anything without an option goes in `scheduler_args` / `-S`, passed
  through untouched to the one scheduler named.
- A scheduler that cannot honour an option set on a job fails the
  submission. The error names the option and points to `scheduler_args`.
- The few options a job kind or the local scheduler ignores are rules,
  listed with their reasons in `../SCHEDULER_OPTIONS.md` and in the user
  docs ("Rules for every job").
- A job is one process on one node. `nodes` and `tasks` are removed. An MPI
  job uses `-S '-N 2 -n 32'`.
- `priority` is removed (decided 2026-11-15). The number is each
  scheduler's own scale, so it does not mean the same thing everywhere. A
  site sets it per scheduler:
  `scheduler_args = { sge = ["-p", "-500"], slurm = ["--nice=500"] }`.
- Adding an option:
  - Its meaning is one sentence that names no scheduler.
  - Every supported scheduler either maps it or rejects it.
  - The mapping is backed by vendor docs or a testbed capture.
  - It is optional.
- Names a site chooses (SGE parallel environment, memory complex) live in
  `[schedulers.<name>]`. A new scheduler adds its own section and touches no
  other.
- One test table: scheduler against option, each cell "maps" or "rejects".
  A new option or scheduler fails the suite until the table is complete.
- Built 2026-11-17: each scheduler class declares `BATCH_OPTIONS` and
  `INTERACTIVE_OPTIONS` over `SCHEDULER_OPTIONS` (`core/job.py`);
  `check_options` (`schedulers/base.py`) raises the error;
  `tests/unit/test_schedulers/test_option_table.py` walks every cell.
  The rules themselves, for SGE, Slurm and local, are the table in
  `../SCHEDULER_OPTIONS.md`; the test checks the code against it.
  - A cell has a third value, "nothing to map", with a reason. Used
    where an option asks nothing of the scheduler: an output file on an
    interactive job (`qrsh -o` exits 0 and writes no file, run on `soge`
    2026-11-17), and `cpu`, `mem`, `time`, `queue`,
    `project` on the local scheduler, which has nothing to reserve. The
    local scheduler goes on running a site config as it is.
  - Options the job script carries the same way everywhere (modules,
    `env_*`, venv) are not in the table.
  - `resources` is not in the table: the three maps take its place there
    in the next step.

## How config layers combine

Decided by the user 2026-11-21 and built the same day. The layers are
`[defaults]`, tools, a type, the command line, a `JobSpec`.

| Keys | Rule | Why |
|---|---|---|
| `modules`, `licenses`, `devices` | Add up across layers. Same licence or device name: the later count wins | The job consumes them; a type that pulls in two tools needs both tools' modules and licences |
| Every other key, including `features`, `env_vars`, `env_prepend`, `env_append`, `env_unset`, `env_passthrough`, `scheduler_args` | A layer that sets the key replaces what earlier layers set; a layer that does not set it inherits | They say where and how the job runs |
| `modules_path`, `modules_path_append` | Set in `[defaults]` only; an unknown key in a tool or a type. Stay on `JobSpec`, for a Python caller with no config file; a `JobSpec` value replaces the one in `[defaults]` | They say where the site's modules are read from, once |

- A layer cannot drop a module, licence or device it inherited.
  `[defaults]` holds only what every job wants.
- `{ set = [...] }`, `{ remove = [...], add = [...] }` and the Python `Set`
  and `Edit` are removed outright.

- `[]` or `{}` on a key that overrides clears it. On `modules`,
  `licenses` or `devices` it adds nothing.
- `scheduler_args` is a plain list, `scheduler_args = ["-p", "-500"]`,
  in config and on `JobSpec`; the table keyed by scheduler is removed. A
  config file is for one scheduler. local ignores `scheduler_args`: a rule
  for `../SCHEDULER_OPTIONS.md`, a test and the user docs.

- `extends` is removed (user, 2026-11-21): the key, `_resolve_extends`,
  its tests and its docs.

- The local file (`./hpc-runner.toml`) stays: it overrides a job's config
  for one directory. A section in both the project file and the local file
  combines per key by the table above, local after project.

- The local file may hold `[tools.*]` and `[types.*]` only (and
  `config_version`). Any other key or section there is a config error
  naming the key and the file. The scheduler setup, `[defaults]` and the
  module paths come from the project file alone.

- The search resolves to one project file: `$HPC_RUNNER_CONFIG`, else
  the git root's `hpc-runner.toml`, else (none there, or no git root)
  `./hpc-runner.toml`, read as a full config. `./hpc-runner.toml` is a
  local file only when a different project file was found.

## Removed options

- A removed option is simply gone, from config, command line and Python
  API (user, 2026-11-15). No message names what it used to be or what
  replaces it: a config that still sets one gets the ordinary
  unknown-key error, the command line gets click's unknown-option error.
- Removed in this plan: `nodes`, `tasks`, `priority`, `use_cwd`, `shell`,
  `resources` (`-R/--resource`). The first five are gone (2026-11-17),
  with the `[types.mpi]` entry of `defaults/config.toml`, which set only
  `nodes` and `tasks`. `resources` goes with the three maps.

## Scope

- Slurm gets what SGE has today: batch and interactive `hpc run`, arrays
  with a throttle, dependencies, status, wait, result, exit codes, cancel,
  active and completed listings, job details, trace.
- Out: heterogeneous jobs, federation, job steps, `sbcast`, `salloc`,
  `--x11`, changing a throttle after submit.
- hpc-runner is for a working cluster. Nothing is designed around a
  scheduler service being down.
- A shared filesystem is assumed.
- Slurm 22.05 is the oldest version supported.

## Options to Slurm arguments

| Option | Slurm |
|---|---|
| `cpu = N` | `--cpus-per-task=N --ntasks=1`; always sent, N is 1 when `cpu` is unset |
| `mem` | `--mem=<MB>` (never 0: Slurm reads 0 as "all of the node") |
| `mem` with `memory_per = "cpu"` | `--mem-per-cpu=<mem / cpu, rounded up>` |
| `time` | `--time=D-HH:MM:SS` |
| `queue` | `--partition` |
| `project` | `--account` |
| `inherit_env = true` | `--export=ALL` |
| `inherit_env = false` | `--export=NONE` |
| `inherit_env = false` + `env_passthrough` | `--export=NAME=val,...`; a value containing `,` is an error |
| dependencies | `--dependency=afterok:1:2,afterany:3` |
| array | task ids are `<base>_<index>` |
| output file | `%x.%j.out`; arrays `%x.%A.%a.out` |
| `stderr` | `--error`, only when set (Slurm merges by default) |
| cancel | `scancel <id>` |

- The batch script exports `SRUN_CPUS_PER_TASK=$SLURM_CPUS_PER_TASK`
  (needed on 22.05).
- The batch script exports `SLURM_EXPORT_ENV=ALL` after the environment
  block, so `srun` inside a user's script sees the loaded modules.
- `--export=NONE` still gives the user's login environment on the node.
  `--export=NIL` (23.02.3+, leaves only `SLURM_*`) is not used.
- `cpu` and `mem` are a first pass. The user docs must explain the Slurm
  cpu/task model clearly.

### Licences, devices, features

`resources = [...]` and `--resource` are replaced by three maps, because
three of the four schedulers need to know what kind of thing is requested.

```toml
[tools.xcelium]
licenses = { xcelium = 1 }
devices  = { gpu = 2 }
features = ["bigmem"]
```

| | licence | device | feature |
|---|---|---|---|
| SGE | `-l lic=1` | `-l gpu=2` | `-l feat=1` |
| Slurm | `-L lic:1` | `--gres=gpu:2` | `-C feat` |
| LSF | `-R "rusage[lic=1]"` | `-gpu "num=2"` | `-R "select[feat]"` |
| PBS | `-l lic=1` | `-l select=1:ngpus=2` | `-l select=1:feat=True` |

- CLI: `--license NAME=N`, `--device NAME=N`, `--feature NAME`.
- A name is only the name of the thing; the scheduler's backend writes
  the request around it. A GPU type (`gpu:a100:2`) or a licence server
  (`name@db`) is one scheduler's syntax, so it is not part of the maps
  and goes in `scheduler_args` (user, 2026-11-17).
- A count is a whole number, 1 or more; anything else is an error before
  submission (decided 2026-11-15).
- The same name twice on a command line: the last one wins, as `-R` does
  today (decided 2026-11-15). A name on the command line replaces the same
  name from config.
- `resources`, `-R/--resource` and the Python `Resource`/`ResourceSet`
  are removed outright.
- A request that is none of the three (scratch space, a host name, a
  memory limit by site name) has no option: it goes in `scheduler_args`,
  per scheduler (user, 2026-11-16).
- Interactive jobs carry all three on every scheduler (decided
  2026-11-15): no difference between schedulers, or between batch and
  interactive. Today SGE drops them for interactive jobs
  (`sge/scheduler.py:576-590` never reads `job.resources`); fixed in the
  three-maps step, whose test table covers both job kinds.
- SGE accepts `-l feat=1` for a boolean and reads it as `TRUE` (run on the
  SGE testbed).
- OpenPBS 23.06 has no built-in `ngpus`; the site creates it (testbed
  run). The PBS device name is therefore a site name, as on SGE.
- What a device count is per: node (Slurm), chunk (PBS), host by default
  (LSF), slot or job depending on site setup (SGE). All the same number for
  one process on one node.

## `hpc run` passes the exit code up

Decided 2026-11-15. `hpc run` must look like running the tool by hand:
the tool's exit code is in `$?`.

- Interactive runs and `--batch --wait` exit with the job's exit code, on
  every scheduler.
- `--batch` without `--wait` exits 0 once the job is submitted.
- A job that failed with no exit code (cancelled while pending, skipped,
  unknown) exits 1.
- An array with `--wait` exits 0 only if every task completed, otherwise 1.
- Built 2026-11-17 for SGE and local (`_exit_as_job` in `cli/run.py`;
  `TestExitCode` in `tests/unit/test_cli/test_run.py`;
  `test_run_exits_with_the_jobs_exit_code` in
  `tests/integration/test_cli.py`, passed on `soge`, not run on `ocs`).
  Nothing in it is scheduler-specific.
- The rule for anything not named above (user, 2026-11-17): if the
  scheduler reports, in whatever way, that the job did not finish,
  `hpc run` exits non-zero and says so. It never exits silently.
- Two cases built on that rule, both run on `soge` 2026-11-17:
  - A job that did not complete but has exit code 0 on record exits 1.
    A node killed under a `--batch --wait` job: `failed 19`,
    `exit_status 0`, status FAILED, `hpc run` exits 1 (run
    `sge-soge-2026-11-17-exp9`, case C).
  - A launcher (`qrsh`, `srun`) killed by signal N exits `128 + N`.
    `qrsh` killed with SIGKILL: `hpc run` exits 137 (run
    `sge-soge-2026-11-17-exp10`).
- Measured in the same runs, SGE `soge` only:
  - Ctrl-C typed at the terminal of an interactive `hpc run`: prints
    "Aborted.", exits 1, and the job leaves `qstat` (run
    `sge-soge-2026-11-17-exp11`). Bare `qrsh` exits 0 there.
  - `qrsh` alone sent SIGTERM, SIGINT or SIGHUP (not from a terminal)
    exits 0 itself, and `hpc run` then exits 0 too. Not something a user
    does; nothing is built for it.
  - An interactive job deleted with `qdel`: `qrsh` and `hpc run` exit 137.
  - A command that kills itself with SIGTERM: `hpc run` exits 143.
- A skipped job exits 1 on every scheduler (user, 2026-11-17). The 113
  on record on SGE is internal to hpc-runner and is never the exit code
  of `hpc run`. Run on `soge`: `exit_status 113` in `qacct`, status
  SKIPPED, `hpc run` exits 1 (run `sge-soge-2026-11-17-exp9`, case A).

## Status and exit codes

- Accounting (slurmdbd) is required, as on SGE.
- Job status and exit code come from `sacct` only:
  `sacct -P -n -j <ids> --format=...`. It returns pending and running jobs
  too.
- `squeue` is used only for the live listing (`hpc status`, `hpc monitor`).
  Pipe-delimited text, explicit field list, job name last.
- No JSON output: its shape changes between Slurm releases.
- The exit code is what a shell would have reported for the command, on
  every scheduler. From Slurm's `ExitCode N:S`: `N` if `S` is 0, otherwise
  `128 + S`. `DerivedExitCode` is not used.
- Status comes from the job's own row. The exit code comes from that row
  too, except when it says `0:0` and the status is not COMPLETED: then the
  `.batch` row is used (decided 2026-11-15). A timed-out job reports 143.
- A job in which nothing ever ran (cancelled while pending, skipped) has
  no exit code. The exit code is never 0 unless the status is COMPLETED.
- So `sacct` is read without `-X`: the job row and the `.batch` row.

| Slurm state | hpc-runner status |
|---|---|
| PD, RQ, RH, RF, RD | PENDING |
| `CANCELLED` with no `by <uid>` | SKIPPED |
| R, CF, CG, SI, SO, RS | RUNNING |
| S, ST | SUSPENDED |
| CD | COMPLETED |
| F, NF, BF, SE | FAILED |
| `CANCELLED by <uid>`, PR, RV | CANCELLED |
| TO, DL | TIMEOUT |
| OOM | KILLED (`hpc status` shows `OUT_OF_MEMORY` in the details) |
| anything else | UNKNOWN |

Measured on the testbeds (2026-11-14, 2026-11-15):

- A new job is not in `sacct` at once: within 0.1 s on 24.11, up to 1 s on
  22.05, where the first row can also be a stub (name `allocation`, no
  partition) for 0.2 s. State and exit code in the stub are right.
  A missing job is UNKNOWN; the wait loop already keeps waiting through
  60 s of UNKNOWN on every scheduler (`core/result.py:173-203`), so
  nothing new is needed.
- At job end the controller and `sacct` agree within 50 ms (24.11).
  So the Slurm testbed needs no slow-accounting variant.
- The `DependencyNeverSatisfied` reason is not in `sacct`: Reason is `None`
  on 24.11 and `Dependency` on 22.05, for both the pending and the
  cancelled form. It is in `squeue` and `scontrol show job`.
- After `scontrol requeue`, `sacct` says `PENDING` on 24.11 and `REQUEUED`
  on 22.05.
- A job cancelled by a user is `CANCELLED by <uid>` in `sacct`; one
  cancelled by `--kill-on-invalid-dep` is plain `CANCELLED` (both
  versions).
- Every `scancel` leaves `CANCELLED by <uid>` on the job's own row: by
  the owner or by root, pending or running, single job or array (run
  exp7, 24.11 only). The only plain `CANCELLED` found is the dependent
  killed by `--kill-on-invalid-dep`, whether its parent failed or was
  cancelled. The `.batch` row is always plain `CANCELLED`, so the test is
  made on the job's own row.
- The job's own row has `ExitCode 0:0` for TIMEOUT and for a cancelled
  running job; only the `.batch` row shows the signal (`0:15`).
- A pending array is one `sacct` row, `64_[1-4%2]`; each task gets its own
  row once it starts. The base job id belongs to the last task.

## Job details

- From `scontrol show job <id>` while the controller knows the job, from
  `sacct` after that, without the output file path (decided 2026-11-15).
- `scontrol` gives the output path filled in on both versions. `sacct`
  has no `StdOut` field before 24.11 (man pages) and on 24.11 gives the
  pattern unfilled (`/work/d-%j.out`, testbed run).

## Dependencies

- Slurm's own `--dependency` is used. None of the SGE workaround: no guard,
  no exit directory, no `.rc` files, no exit code 113.
- `emulate_afterok` stays an SGE-only key.
- `aftercorr` and `singleton` go through `-S`.
- `kill_invalid_dependents = true` (default) adds
  `--kill-on-invalid-dep=yes`, so a job whose parent failed is cancelled
  instead of pending forever. `sacct` shows that as `CANCELLED` with no
  `by <uid>`, which is SKIPPED (decided 2026-11-16). With the key false
  the job stays PENDING: `sacct` cannot tell it from any other pending
  job, and no second command is run to find out.
- Interactive jobs refuse dependencies, as on SGE (confirmed by the user
  2026-11-18): an interactive job runs now, at the terminal, and `srun`
  waits for ever if the parent failed.
- Submitting a dependent after its parent has left the controller's memory
  is not supported.

## Working directory

- `use_cwd` is removed on every scheduler (decided 2026-11-15). A job
  always runs in `workdir`: SGE always gets `-cwd`, Slurm always gets
  `--chdir=<workdir>`.

## Shell

- `shell` is removed on every scheduler (decided 2026-11-15). The job
  script is written in bash and always runs under it: SGE always gets
  `-S /bin/bash`, the Slurm script starts `#!/bin/bash`.

## Whitespace in option values

- Refused on Slurm as on SGE, with the same message (decided 2026-11-15).
  Slurm accepts a double-quoted value (testbed run, job name only), but a
  job accepted on one scheduler must be accepted on the other.

## Default output file for arrays

- Changes on every scheduler to `${hpc.job_name}.${hpc.job_id}.${hpc.task_id}.out`
  (`sim.123.4.out`; decided 2026-11-15). It was `sim.123.out.4`.
- A user-set pattern without `${hpc.task_id}` gets it before the last
  extension (`run.log` gives `run.4.log`); a name with no extension gets
  it appended. It was appended always (`run.log.4`).
- Built 2026-11-17 (`_with_task_id` in `schedulers/base.py`). An
  extension is a final `.` followed by letters, digits, `_` or `-`; a
  `${hpc.*}` reference at the end is not one.

## `${hpc.*}` variables

Decided 2026-11-15.

| Variable | In the script | In an output path |
|---|---|---|
| `${hpc.cpus}` | `$SLURM_CPUS_PER_TASK` | none |
| `${hpc.job_id}` | `${SLURM_ARRAY_JOB_ID:-$SLURM_JOB_ID}` | `%A` for an array, `%j` otherwise |
| `${hpc.task_id}` | `$SLURM_ARRAY_TASK_ID` | `%a` |
| `${hpc.job_name}` | `$SLURM_JOB_NAME` | `%x` |
| `${hpc.tmpdir}` | `$TMPDIR` | none |
| `${hpc.host}` | `$HOSTNAME` | `%N` (not run on the testbed) |

- `${hpc.job_id}` is the number submit printed, the same for every task of
  an array, as on SGE. Slurm gives each task its own `SLURM_JOB_ID`
  (testbed run, both versions: `64_1|65`, `64_2|66`, `64_3|67`, `64_4|64`).
- `$SLURM_CPUS_PER_TASK` is set only when `--cpus-per-task` is given, so
  Slurm is always sent it. SGE is unchanged.
- `${hpc.tmpdir}` is whatever `$TMPDIR` holds. hpc-runner makes no
  directory: on Slurm it is shared between jobs and not cleaned unless the
  site arranges it. Scratch space is the user's job. The user docs say so.

## Environment

- `SBATCH_*` variables in a user's profile override what hpc-runner sets.
  Documented as "do not combine"; hpc-runner does not remove them.
- `hpc run` from inside a running job is not supported.
- Shell functions are not carried into a job by SGE `-V` (testbed run), and
  a Slurm batch shell is not a login shell. Neither matters: the job script
  sources the Modules init file itself
  (`templates/_environment.sh.j2:14-23`).
- `DISPLAY` travels with the environment. With `inherit_env = false` a site adds it to
  `env_passthrough`. `srun --x11` is not used.

## Interactive jobs

- `srun <script>` is the launcher. No `salloc`.
- The script is written to `~/.cache/hpc-runner/scripts/` (or
  `$HPC_SCRIPT_DIR`) and deletes itself unless `--keep-script`.
- `--pty` only when stdin and stdout are both terminals.
- `stdout` and `stderr` are ignored on an interactive job, on every
  scheduler (user, 2026-11-17): `--output` and `--error` are never sent to
  `srun`.
- `hpc run` exits with whatever `srun` exits with (decided 2026-11-15).
  A failure of `srun` itself (bad partition: exit 1, testbed run) is not
  told apart from the command failing, as with `qrsh` on SGE; srun's
  message is already on the terminal. `SLURM_EXIT_ERROR` is not used.

- `inherit_env = false` is honoured as asked (decided 2026-11-15):
  `srun --export=NONE`, or `--export=NAME=val,...` from `env_passthrough`.
  Unlike `sbatch`, `srun` builds no login environment: `HOME`, `USER`
  and `LOGNAME` are empty and `PATH` is bash's default (run
  `slurm-24.11-2026-11-15-exp5`, 24.11 only). A site that wants them
  names them in `env_passthrough`. The user docs say so.

## Trace

Built 2026-11-15 (`0e874aa`); `docs/dev/trace/DESIGN.md` is the reference.
Nothing in it is scheduler-specific.

- A trace is a debug record of a handful of commands, read as raw files.
  `hpc trace compare` and `hpc trace bundle` are removed.
- One record per `hpc` command line (argv, config files, the whole
  environment, output, error, exit code) and one per scheduler command,
  with the job script on the command that submitted it.
- The whole environment, not a list of prefixes: `SBATCH_*` variables
  override `#SBATCH` lines (run S18). Nothing is scrubbed; a user edits
  the JSON before passing it on.
- Every scheduler command is written. There is no deduplication.
- `hpc monitor` is never traced; `hpc --trace DIR monitor` is a usage
  error.
- Nothing is ever removed: no `max_days`. `[trace]` keys are `enabled`
  and `dir`.
- A traced command ends with one line on stderr: `trace: N records
  written to <dir>`.
- For Slurm: the scheduler calls `recorder.note_script(path, text)` when
  it writes a job script, as SGE does. Nothing else is needed.

## Config: `[schedulers.slurm]`

- `memory_per = "job" | "cpu"`, default `"job"`.
- `kill_invalid_dependents = true | false`, default `true`.

## `hpc cancel` replaces `hpc kill`

- `hpc cancel JOB_ID...`
- `hpc cancel -N PATTERN | --all [--pending | --running]`
- `--force` on both.
- `hpc kill` is removed: it was the same operation under a second name.
- However the jobs are selected, it prints a table of them and asks
  "Proceed?" once (decided 2026-11-15). `--force` skips the question.
- Exits 1 if any job could not be cancelled. Answering "no" exits 0.
- Built 2026-11-17 (`cli/cancel.py`; `hpc kill` deleted). As built:
  - Job ids and `-N`/`--all` cannot be combined; `--pending` and
    `--running` apply only with `-N` or `--all`.
  - Ids are looked up among the caller's own active jobs (one listing,
    scoped to the user). An id not found there is shown in the table
    with state `UNKNOWN` and nothing is sent to the scheduler for it; it
    does not change the exit code (user, 2026-11-17).
  - A filter that matches nothing prints so and exits 0.

## Testbed

- Built before any code that parses Slurm output. Parser tests read
  captures from it.
- A stack is named by scheduler and flavour:
  `hpc-testbed --scheduler slurm --flavour 22.05 up`. `--scheduler` is
  required; `--flavour` defaults per scheduler (`soge`, `24.11`,
  `openpbs`). The integration suite takes the same two options.
- Two flavours: `testbed/slurm/24.11/` (Debian trixie, 24.11.5) and
  `testbed/slurm/22.05/` (bookworm, 22.05.8). Shared config in
  `testbed/slurm/common/`. Fixtures in
  `tests/unit/fixtures/slurm/<version>/`.
- Containers: `master` (slurmctld, slurmdbd, munged), `db` (MariaDB),
  `exec1`, `exec2` (slurmd), `login` (clients, pytest). Same shape as the
  SGE testbed.
- A container is named `<compose project>-<service>`
  (`hpc-testbed-slurm-24-11-login`) and each stack has its own subnet, so
  any stacks can be up together (2026-11-16).
- No cgroups: `proctrack/linuxproc`, `task/none`. Not `privileged`, but the
  24.11 exec containers have `cap_add: SYS_ADMIN`: without it slurmd exits
  at the first `--export=NONE` job (testbed run). 22.05 does not need it.
- exec1 has a feature (`fast`) and a GPU
  count; both nodes have temp disk; there are two licences (`lic`).
- Accounting always on. Munge key generated per stack.
- A lost node is made with `docker kill`, the same `kill_node` call the
  SGE tests use (`kill_node` in `tests/integration/cluster.py`). The testbed
  `slurm.conf` sets no `SlurmdTimeout` today; set a short one when the
  `node_fail` capture is taken, and check the job ends `NODE_FAIL`.
- Cannot be produced in this testbed: an out-of-memory kill, memory
  enforcement, `--x11`. Their handling rests on the Slurm source and one
  hand-built record, marked as not captured.

Captures to take: ok, exit1, signal, timeout, cancelled_pending,
cancelled_running, held, dependent_skipped, dependent_pending, array,
array_partial, array_throttled, suspended, requeued, node_fail, running,
pending_resources, bad_partition, bad_account, dependent_ok.

- Accounts are enforced (`AccountingStorageEnforce=associations`, decided
  2026-11-16). Every user is in `proj`; `other` has no user. A job with
  no account runs under `proj`; `-A nosuch` and `-A other` are rejected by
  `sbatch` and `srun` with rc 1 and `Invalid account or account/partition
  combination specified` (run exp8, 24.11 only).

## Tests

- Integration tests of hpc-runner's own behaviour (e.g. `hpc run --batch`
  of a failing command gives FAILED, exit code 1) run on every scheduler.
- Tests of one scheduler's output stay per scheduler (`test_sge_*.py`,
  `test_slurm_*.py`).
- Unit: parser tests per flavour from fixtures; one set of golden scripts
  per scheduler.
- CI gains `24.11` and `22.05` cells.

## Order of work

This list is all the remaining work for the Slurm backend. Work is taken
from it one step at a time. A step is deleted when it is done.

Done before this list: the research, run on real schedulers (SGE, Slurm
24.11 and 22.05, OpenPBS; LSF dropped, no download without a registered
company), the three testbeds, and the answers to the first round of
questions. Results are in the two fact files and `runs/`.

1. Make the tests ready for a second scheduler. The inventory is in
   `../FOLLOW_UPS.md`, "Tests and testbed".
   - Integration tests that promise the same thing on every scheduler:
     move the SGE words behind the cluster class.
   - Tests that must differ per scheduler: one per scheduler.
   - A test kept for SGE only needs the user's decision and a written
     reason.
   - Fixture helpers named for SGE (`SGEFixtures`, `make_sge_responder`,
     `sge_fixtures`, `mock_sge_commands`): tests that are not about SGE
     stop depending on them.
2. Slurm testbed support, both flavours.
   - `hpc-runner-testbed.toml`, and `HPC_RUNNER_CONFIG` in the compose
     files.
   - The Slurm cluster class: every method the SGE one has, or a written
     reason (`accounting_truncate`).
   - A Slurm capture module, registered in `CAPTURE_MODULES`.
   - `testbed/slurm/README.md` with the table of test against flavour.
   - Run exp7 (which cancellations are plain `CANCELLED`) and exp8
     (account enforcement) on 22.05.
3. Slurm captures and fixtures: the list in "Testbed". Set a short
   `SlurmdTimeout` for `node_fail`.
4. Rendering.
   - The scheduler class, registered in `schedulers/__init__.py`;
     `[schedulers.slurm]` keys in its `CONFIG_KEYS`.
   - Arguments, batch template, golden scripts.
   - `${hpc.*}` variables, whitespace refusal, `make_output_dirs`,
     `recorder.note_script`.
   - Interactive: `srun`, the `--pty` rule, `--export`.
5. Parsing, against the fixtures.
    - Status and exit code from `sacct`; the SKIPPED rule.
    - Live listing from `squeue`.
    - Job details from `scontrol show job`, then `sacct`.
    - Cancel.
6. Integration suite on both Slurm versions; CI cells for `24.11` and
    `22.05`.
    - Includes: for each kind of finished job with more than one `sacct`
      row (timeout, cancelled while running), hpc-runner reports the
      right status and code.
7. User docs, and a manual checklist on a real cluster.
    - The Slurm cpu and task model.
    - `inherit_env = false` on an interactive job leaves `HOME`, `USER`
      and `LOGNAME` empty.
    - `${hpc.tmpdir}` is plain `$TMPDIR`; scratch space is the user's job.
    - `SBATCH_*` variables are not to be combined with hpc-runner.
    - A table of Slurm job states against readers, as
      `test-review/LIFECYCLE_MATRIX.md` is for SGE.

## Open questions

None.

## Later, not planned

Slurm only. Anything about another scheduler, the testbed or the test
setup is in `../FOLLOW_UPS.md`.

- JSON output from `sacct`/`squeue`, once 23.11 is the oldest version.
- A config key for whether `srun` steps in a user's script start clean.
- A look at Slurm's advanced features (job steps, `salloc`, ...) and
  whether any fits a single-job runner.
- A privileged testbed profile to capture a real out-of-memory kill.
- Review of the `cpu` and `mem` mapping after real use. One known
  point: Slurm multiplies `--mem-per-cpu` by the CPUs it allocates, which
  can be more than `cpu` (`RESOURCES_COMPARISON.md`, R4).
- Allowing dependencies on interactive Slurm jobs.
- A site catalogue giving one resource a different name per scheduler.
- Tests for every stdin/stdout combination of the `--pty` rule, plus a
  manual checklist on a real cluster.

## Build notes

- Compose refuses a `.` in a project name; `hpc-testbed` maps it
  (`hpc-testbed-slurm-24-11`).
- A Slurm `compose.yml` without a `CLUSTERS["slurm"]` entry fails
  `test_the_repository_testbed_has_a_class_and_a_default_for_every_scheduler`.
- Scheduler section keys are declared in the scheduler's `CONFIG_KEYS`,
  not in `schema.py`. Render order comes from the shared `RENDER_ORDER`.
  A scheduler needs `HPC_VARS`. A new Sphinx page needs a toctree entry.
- `scheduler_args` are split tokens; `sbatch` reads several options per
  directive line.
- A repeated `-L` or `--gres` replaces the earlier one: `-L lic:1 -L
  lic:2` asks for `lic:2`, `--gres=gpu:1 --gres=gpu:0` for `gpu:0` (run
  `slurm-24.11-2026-11-15-exp6`; only one licence and one device name
  exist on the testbed, so two different names were not run). Several of
  a kind are joined into one option.
- `__init__.py` exports `Resource`/`ResourceSet`. `job_info.py` and the two
  TUI files do not read `Job.resources`.
- An interactive dry run has no directives, and `--dry-run` does not print
  the command line.
- `has_accounting()` must be True or `--history` exits 1. `result` is in
  the base class. `get_job_details` fills `extra["tasks"]`.
- `sacct -P -n -j <id>` prints the default columns; always pass
  `--format`. `sacct` prints long state names; the table above uses the
  two-letter codes.
- `hpc-testbed pytest -k` needs `--`.
- `nproc` proves nothing under `task/none`.
- A request larger than any node is rejected at submit, so it cannot give a
  pending-for-resources capture.
- An out-of-memory kill without cgroups shows as `FAILED 0:9`.
- `afterok` on an unknown job id is accepted on 22.05 and rejected on
  24.11 (testbed runs). Where between the two it changed was not checked.
