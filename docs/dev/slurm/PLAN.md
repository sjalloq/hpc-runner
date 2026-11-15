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
  Nothing is silently ignored.
- A job is one process on one node. `nodes` and `tasks` are removed. An MPI
  job uses `-S '-N 2 -n 32'`.
- Adding an option:
  - Its meaning is one sentence that names no scheduler.
  - Every supported scheduler either maps it or rejects it.
  - The mapping is backed by vendor docs or a testbed capture.
  - It is optional, so existing configs keep working.
- Renaming or redefining an option: avoided, but done when the design is
  wrong. The old name then gives an error saying what to use.
- Names a site chooses (SGE parallel environment, memory complex) live in
  `[schedulers.<name>]`. A new scheduler adds its own section and touches no
  other.
- One test table: scheduler against option, each cell "maps" or "rejects".
  A new option or scheduler fails the suite until the table is complete.

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
| `cpu = N` | `--cpus-per-task=N --ntasks=1` |
| `mem` | `--mem=<MB>` (never 0: Slurm reads 0 as "all of the node") |
| `mem` with `memory_per = "cpu"` | `--mem-per-cpu=<mem / cpu, rounded up>` |
| `time` | `--time=D-HH:MM:SS` |
| `queue` | `--partition` |
| `project` | `--account` |
| `priority = P` | `--nice=-P` (Slurm's scale runs the other way) |
| `inherit_env = true` | `--export=ALL` |
| `inherit_env = false` | `--export=NONE` |
| `inherit_env = false` + `env_passthrough` | `--export=NAME=val,...`; a value containing `,` is an error |
| dependencies | `--dependency=afterok:1:2,afterany:3` |
| array | task ids are `<base>_<index>` |
| output file | `%x.%j.out`; arrays `%x.%A_%a.out` (see open questions) |
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
- A config still using `resources` gets an error naming the three maps.
- SGE accepts `-l feat=1` for a boolean and reads it as `TRUE` (run on the
  SGE testbed).
- OpenPBS 23.06 has no built-in `ngpus`; the site creates it (testbed
  run). The PBS device name is therefore a site name, as on SGE.
- What a device count is per: node (Slurm), chunk (PBS), host by default
  (LSF), slot or job depending on site setup (SGE). All the same number for
  one process on one node.

## Status and exit codes

- Accounting (slurmdbd) is required, as on SGE.
- Job status and exit code come from `sacct` only:
  `sacct -P -n -j <ids> --format=...`. It returns pending and running jobs
  too.
- `squeue` is used only for the live listing (`hpc status`, `hpc monitor`).
  Pipe-delimited text, explicit field list, job name last.
- No JSON output: its shape changes between Slurm releases.
- Exit code from `ExitCode N:S`: `N` if `S` is 0, otherwise `128 + S`.
  `DerivedExitCode` is not used.

| Slurm state | hpc-runner status |
|---|---|
| PD, RQ, RH, RF, RD | PENDING |
| PD or CA with reason `DependencyNeverSatisfied` | SKIPPED |
| R, CF, CG, SI, SO, RS | RUNNING |
| S, ST | SUSPENDED |
| CD | COMPLETED |
| F, NF, BF, SE | FAILED |
| CA, PR, RV | CANCELLED |
| TO, DL | TIMEOUT |
| OOM | KILLED (`hpc status` shows `OUT_OF_MEMORY` in the details) |
| anything else | UNKNOWN |

Measured on the testbeds (2026-11-14, 2026-11-15):

- A new job is not in `sacct` at once: within 0.1 s on 24.11, up to 1 s on
  22.05, where the first row can also be a stub (name `allocation`, no
  partition) for 0.2 s. State and exit code in the stub are right.
- At job end the controller and `sacct` agree within 50 ms (24.11).
- The `DependencyNeverSatisfied` reason is not in `sacct`: Reason is `None`
  on 24.11 and `Dependency` on 22.05, for both the pending and the
  cancelled form. It is in `squeue` and `scontrol show job`.
- After `scontrol requeue`, `sacct` says `PENDING` on 24.11 and `REQUEUED`
  on 22.05.
- A job cancelled by a user is `CANCELLED by <uid>` in `sacct`; one
  cancelled by `--kill-on-invalid-dep` is plain `CANCELLED` (both
  versions).
- The job's own row has `ExitCode 0:0` for TIMEOUT and for a cancelled
  running job; only the `.batch` row shows the signal (`0:15`). The rule
  above would report exit code 0 for both. See open questions.
- A pending array is one `sacct` row, `64_[1-4%2]`; each task gets its own
  row once it starts. The base job id belongs to the last task.

## Dependencies

- Slurm's own `--dependency` is used. None of the SGE workaround: no guard,
  no exit directory, no `.rc` files, no exit code 113.
- `emulate_afterok` stays an SGE-only key.
- `aftercorr` and `singleton` go through `-S`.
- `kill_invalid_dependents = true` (default) adds
  `--kill-on-invalid-dep=yes`, so a job whose parent failed is cancelled
  instead of pending forever. Either way it shows as SKIPPED.
- Interactive jobs refuse dependencies, as on SGE.
- Submitting a dependent after its parent has left the controller's memory
  is not supported.

## Environment

- `SBATCH_*` variables in a user's profile override what hpc-runner sets.
  Documented as "do not combine"; hpc-runner does not remove them.
- `hpc run` from inside a running job is not supported.
- Shell functions are not carried into a job by SGE `-V` (testbed run), and
  a Slurm batch shell is not a login shell. Neither matters: the job script
  sources the Modules init file itself
  (`templates/_environment.sh.j2:14-23`).
- `DISPLAY` travels with the environment. In strict mode a site adds it to
  `env_passthrough`. `srun --x11` is not used.

## Interactive jobs

- `srun <script>` is the launcher. No `salloc`.
- The script is written to `~/.cache/hpc-runner/scripts/` (or
  `$HPC_SCRIPT_DIR`) and deletes itself unless `--keep-script`.
- `--pty` only when stdin and stdout are both terminals.
- `SLURM_EXIT_ERROR=112` is set for `srun`, so srun's own failures (bad
  partition; bad account where the site enforces accounts) exit 112 and are reported as submission errors,
  not as the command failing.

## Config: `[schedulers.slurm]`


- `memory_per = "job" | "cpu"`, default `"job"`.
- `kill_invalid_dependents = true | false`, default `true`.

## `hpc cancel` replaces `hpc kill`

- `hpc cancel JOB_ID...`
- `hpc cancel -N PATTERN | --all [--pending | --running]`
- `--force` on both.
- `hpc kill` is removed: it was the same operation under a second name.

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
- No cgroups: `proctrack/linuxproc`, `task/none`. Not `privileged`, but the
  24.11 exec containers have `cap_add: SYS_ADMIN`: without it slurmd exits
  at the first `--export=NONE` job (testbed run). 22.05 does not need it.
  Follow-up: confirm this is acceptable.
- exec1 has a feature (`fast`) and a GPU
  count; both nodes have temp disk; there are two licences (`lic`).
- Accounting always on. Munge key generated per stack.
- Cannot be produced in this testbed: an out-of-memory kill, memory
  enforcement, `--x11`. Their handling rests on the Slurm source and one
  hand-built record, marked as not captured.

Captures to take: ok, exit1, signal, timeout, cancelled_pending,
cancelled_running, held, dependent_skipped, dependent_pending, array,
array_partial, array_throttled, suspended, requeued, node_fail, running,
pending_resources.

## Tests

- Integration tests of hpc-runner's own behaviour (e.g. `hpc run --batch`
  of a failing command gives FAILED, exit code 1) run on every scheduler.
- Tests of one scheduler's output stay per scheduler (`test_sge_*.py`,
  `test_slurm_*.py`).
- Unit: parser tests per flavour from fixtures; one set of golden scripts
  per scheduler.
- CI gains `24.11` and `22.05` cells.

## Order of work

Research is closed on real schedulers before anything is specified or
built on it.

1. SGE: run the unverified SGE claims on the existing testbed. Done
   2026-11-14, results in `SGE_SLURM_DIFFERENCES.md`, "Testbed runs".
2. Slurm testbeds `24.11` and `22.05`: built, claims run on both (same
   place).
3. PBS testbed: built (`testbed/pbs/openpbs/`, OpenPBS 23.06.06 from
   source on Rocky Linux 8). PBS claims run 2026-11-15; results in
   `RESOURCES_COMPARISON.md`, section 7.
4. LSF: dropped for now. IBM's Community Edition cannot be downloaded
   without a registered company. Its claims stay marked unverified.
5. Fact files updated from the runs. The remaining unverified items were
   accepted by the user on 2026-11-15 as not affecting the build (listed
   at the end of each fact file). Research is closed. Next: review this
   plan against the fact files.
6. Answer the open questions.
7. Options: remove `nodes`/`tasks`, add the rejection error, add the test
   table.
8. Fold `hpc kill` into `hpc cancel`.
9. Replace `resources` with the three maps.
10. Fix a wrong note in `restructure/DECISIONS.md` (D3): Slurm has no
    `--export=NONE,NAME=value`.
11. Slurm captures and fixtures.
12. Rendering: arguments, templates, golden scripts.
13. Parsing, against the fixtures.
14. Integration suite on both Slurm versions, CI cells.
15. User docs; manual checklist on a real cluster.

## Open questions

1. **Cancel prompts.** `hpc cancel 1 2`: one confirmation or one per job?
   On partial failure `kill` exits 1 today and `cancel` exits 0. Which?
2. **`${hpc.*}` variables.** No Slurm mapping is decided. `${hpc.cpus}`:
   `$SLURM_CPUS_PER_TASK` is unset without `-c`. In arrays the job id is
   each task's own id.
3. **Working directory.** Slurm starts in the caller's directory by
   default (testbed run); `--chdir=<dir>` changes it (run). What does
   `use_cwd = false` map to?
4. **`shell`.** Slurm has no option for it; the script's first line
   decides. Drop, reject, or write the first line from it?
5. **Whitespace in argument values.** SGE refuses it. On Slurm an unquoted
   `#SBATCH --job-name=two words` is rejected and a double-quoted one is
   accepted (testbed run). Refuse as on SGE, or quote?
6. **Array output file name.** The plan says `%x.%A_%a.out`; the documented
   `output_pattern` gives `%x.%A.out.%a`. Which?
7. **`SLURM_EXIT_ERROR`.** `run_command` cannot set an environment variable
   for the subprocess. Add the parameter?
8. **srun error text.** Interactive runs do not capture stderr, so a
   submission error has no message to carry. What does it say?
9. **Strict interactive environment.** `srun --export=NONE` gives no login
   environment (unlike `sbatch`), so the job starts without `PATH` or
   `HOME`. What do we do?
10. **Interactive exit code.** `hpc run` interactive prints a failure and
    exits 0 (`cli/run.py:189-194`). Same on SGE. Fix, and where?
11. **Job details.** Where do live job details come from?
    `scontrol show job` was assumed, never decided.
12. **Testbed node control.** `scontrol update NodeName=... State=`, or
    `docker kill` as on SGE (which waits for `SlurmdTimeout`)?
13. **Trace.** `hpc trace` knows only qsub/qstat/qacct, and its scrubbing
    is SGE-only: a Slurm bundle would leave user, host and job names in.
    Not planned yet.
14. **Interactive resources on SGE.** `hpc run -R gpu=1 xterm` drops the
    request today (`_build_qrsh_command` never reads it). Fix along with
    the three maps?
15. **Rules for the three maps.** Proposed, not decided: a count is 1 or
    more; a repeated name on the command line is an error; two more
    captures (`bad_partition`, `dependent_ok`).
16. **Non-numeric resource values.** The maps take whole numbers. In use
    today: `hostname=exec1` and `h_vmem=50M` (tests only), `scratch=20G`
    (a golden script), `tmpfs=8G` (a docs example). The real case is
    scratch disk space; a `scratch` option is proposed, not decided.
    A scratch size reserves space on LSF, reserves it on SGE and PBS only
    if the site set that up, and on Slurm only picks a node whose temp disk
    is big enough. Bare numbers: bytes on SGE, MB on Slurm and LSF. Facts:
    `RESOURCES_COMPARISON.md` R8.
17. **Output directory missing.** Slurm 22.05 fails the job (`FAILED 0:53`,
    run on the testbed); 24.11 creates the directory (run); SGE puts the job in error state (`Eqw`).
    Create it ourselves, or document? The user docs say SGE discards the
    output, which the testbed run does not show.
18. **SKIPPED cannot be told from `sacct` alone.** A dependent whose parent
    failed is `PENDING` or `CANCELLED` in `sacct` with Reason `None`
    (testbed run). The reason is in `squeue` (pending form) and
    `scontrol show job` (cancelled form, while the controller remembers
    the job). This conflicts with "status comes from `sacct` only".
    Related: a job just submitted is missing from `sacct` for up to 1 s on
    22.05, so "not found" right after submit cannot mean "unknown job".
    One fact that may help: with `kill_invalid_dependents` on, the
    dependent ends as plain `CANCELLED`, where a user's cancel is
    `CANCELLED by <uid>`.
19. **Wrong account.** The table says `project` maps to `--account`; a
    wrong account is only rejected where the site enforces associations.
    On the testbed `-A nosuch` is accepted. Follow-up: turn enforcement on
    in the testbed?
20. **Priority.** The table maps `priority = P` to `--nice=-P`. An
    ordinary user cannot raise priority on Slurm: `--nice=-10` is rejected
    at submit, "Invalid --nice value" (run on 24.11); `--nice=10` is
    accepted. SGE is the same in kind: users may only lower (`submit(1)`,
    not run). So a positive `priority` fails for ordinary users on Slurm.
    Keep the mapping and let it fail, or something else?
21. **Exit code of a timed-out or cancelled job.** `sacct` gives the job
    row `0:0` for both, so "N if S is 0" reports 0. What should `hpc`
    report: nothing, or the `.batch` row's signal (143)?

## Later, not planned

- LSF. No way to get a cluster to test on.

- JSON output from `sacct`/`squeue`, once 23.11 is the oldest version.
- A config key for whether `srun` steps in a user's script start clean.
- A look at Slurm's advanced features (job steps, `salloc`, ...) and
  whether any fits a single-job runner.
- A privileged testbed profile to capture a real out-of-memory kill.
- Review of the `cpu` and `mem` mapping after real use.
- Allowing dependencies on interactive Slurm jobs.
- A site catalogue giving one resource a different name per scheduler.
- Tests for every stdin/stdout combination of the `--pty` rule, plus a
  manual checklist on a real cluster.
- PBS items not run: host exclusion, soft requests, `place=scatter`, why
  `qsub -V` did not carry a variable, OpenPBS against PBS Professional.
  For when a PBS backend is planned.

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
- Repeated `-L` and repeated `--gres` overwrite each other; several of a
  kind are joined into one option.
- `__init__.py` exports `Resource`/`ResourceSet`. `job_info.py` and the two
  TUI files do not read `Job.resources`.
- An interactive dry run has no directives, and `--dry-run` does not print
  the command line.
- `has_accounting()` must be True or `--history` exits 1. `result` is in
  the base class. `get_job_details` fills `extra["tasks"]`.
- `sacct -P -n -j <id>` prints the default columns; always pass
  `--format`. `sacct` prints long state names; the table above uses the
  two-letter codes.
- `hpc-testbed pytest -k` needs `--`; `trace compare` needs a directory.
- `nproc` proves nothing under `task/none`.
- A request larger than any node is rejected at submit, so it cannot give a
  pending-for-resources capture.
- An out-of-memory kill without cgroups shows as `FAILED 0:9`.
- `afterok` on an unknown job id is accepted on 22.05 and rejected on
  24.11 (testbed runs). Where between the two it changed was not checked.
