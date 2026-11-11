# Slurm backend: decisions

Decided 2026-11-02 to 2026-11-04 in a grilling session on the Slurm
implementation plan. Facts cited as "DIFF §n" are in
`SGE_SLURM_DIFFERENCES.md`; "src:" is the SchedMD source at the tags
slurm-22-05-11-1 / 24-11-7-1 unless stated. Numbering D-S1... (restructure
D1-D19, testbed D-T1..., trace D-TR1...).

**Where this stands (2026-11-11): decided, nothing implemented.** This file
is the whole Slurm plan: the decisions, then "Amendments" (D-S28 onwards),
"Open questions" (each needs the user's answer before the code it touches is
written), "Parked" and "Implementation notes". There are no tickets; work is
taken from here one item at a time. Fact bases: `SGE_SLURM_DIFFERENCES.md`
and `RESOURCES_COMPARISON.md`.

Two statements made during the session were wrong and are recorded so they
are not repeated: (1) "sacct lags slurmctld by a few seconds" has no basis;
on a working cluster the record is sent at once (D-S6); (2) `hpc kill` never
sent a signal; it was a bulk `cancel` (D-S25).

Standing rule from the session: hpc-runner is for a *working* cluster. No
decision is justified by a scheduler service being down or degraded.

## Scope and approach

### D-S1. Testbed first

The Slurm testbed is built before any code that parses Slurm output. The
captures from it are the fixtures the parser tests read (the repo rule: no
canned scheduler text typed from memory; the 2026-10-31 bug triage found
every parser escape came from hand-typed fixtures). The rendering half
(directives, templates, goldens, `bash -n`) needs no cluster and may run in
parallel.

Rejected: backend first against man-page examples (rewrite once real output
exists); strictly sequential (wastes the rendering overlap).

### D-S2. SGE parity only

The first cut gives what SGE gives today: batch and interactive `hpc run`,
arrays with a throttle, dependencies, status/wait/result/exit codes, cancel,
active and completed listings, job details, trace. `nodes`/`tasks` are
rendered when set. Out: heterogeneous jobs, federation/multi-cluster, job
steps, `sbcast`, changing a throttle after submit, `salloc`, `--x11`.

Amended by D-S29: `nodes` and `tasks` are removed, so nothing is rendered
for them.

The user's view: the tool is a single-shot job runner; the advanced features
need a separate investigation into what exists and whether any of it applies
(Parked).

### D-S3. Versions and flavours

Floor: Slurm 22.05. Testbed flavours `testbed/slurm/24.11/` (Debian trixie,
first) and `testbed/slurm/22.05/` (bookworm, second), named by version
because Slurm has one lineage. Fixtures in `tests/unit/fixtures/slurm/<ver>/`.
A 25.x flavour (source build) only if a user's cluster needs it.

Differences that touch us (DIFF §2, §6): `--export=NIL` only from 23.02;
`srun` inside `sbatch` does not inherit `--cpus-per-task` on 22.05 (reads
`SRUN_CPUS_PER_TASK`), inherits again from 23.11; `afterok` on an unknown id
is accepted on 22.05, rejected from 23.02; JSON output differs entirely.

Rejected: floor 23.02 or 24.11 (drops the version vhpc and EPEL 9 ship, and
we would never see the 22.05 differences).

### D-S4. Testbed shape

`master` (slurmctld + slurmdbd + munged), `db` (MariaDB), `exec1`/`exec2`
(slurmd), `login` (clients + pytest), mirroring the SGE stack so
`TestbedCluster`, `ready.sh`/`clean.sh` and node control keep their shape.
Unprivileged: `proctrack/linuxproc`, `task/none`, cgroups off (as vhpc,
MIT, confirms works). Accounting always on; the plan's optional `accounting`
profile is dropped. Munge key generated per stack at first start.

Prior art evaluated (2026-11-02): exactlab/vhpc (Rocky 9, EPEL 22.05.9 only,
no login node, re-runs network installs at start, dormant since 2025-11),
pytest-slurm (drives vhpc from the host over docker exec or an SSH
connection, pulls images from ghcr only, cannot run `sbatch` inside the
cluster as our `login` model does), giovtorres/slurm-docker-cluster
(25.11/26.05 from source, `privileged: true`). None matches the Debian
22.05/24.11 matrix; we build our own and borrow vhpc's plugin choices and
entrypoint ordering as reference.

Cannot be exercised in this testbed, documented as such: OUT_OF_MEMORY
(needs `task/cgroup` with memory constrained; without it an OOM kill is
`FAILED 0:9`, src: slurmstepd/mgr.c, task_cgroup_memory.c), memory
enforcement, `--x11` (`PrologFlags=X11` is incompatible with
`proctrack/linuxproc`, slurm.conf(5)). Their mappings rest on the source
and a hand-built unit record, marked as not captured (exception to the
fixture rule, logged in TESTBED_REVIEW).

## Status and records

### D-S5. Accounting is required

slurmdbd + `sacct` are required, as D19 requires SGE accounting. Without
slurmdbd `sacct` exits 1 ("Slurm accounting storage is disabled") and a
finished job is visible only for `MinJobAge` (300 s) via the controller.
The user's view: every real cluster has accounting. No fallback until a
site without it appears.

### D-S6. `sacct` is the only status source

`get_statuses` and `get_exit_code` read `sacct -P -n -j <ids> --format=...`
only. slurmctld sends a record at submit (`job_allocate`, src:
slurmctld/job_mgr.c:4311), start and end; the dbd agent wakes on enqueue
(`slurm_cond_broadcast`, slurmdbd_agent.c:1035) and sleeps only when the
queue is empty or the connection is down. With `-j`, the window is Epoch to
now and the SQL selects `time_end = 0` rows, so PENDING and RUNNING jobs
are returned, held ones included (sacct(1) DEFAULT TIME WINDOW;
as_mysql_jobacct_process.c:1657-1675). Pending array tasks share the base
record (`123_[1-10]`); each task gets its own row when it starts; `sacct -j
<base>` returns all.

`squeue` is used only for `list_active_jobs` (live listing for `hpc status`
and `hpc monitor`). Output is pipe-delimited text with an explicit field
list, job name last (Slurm splits on `|`). JSON output is parked
(Parked): on 22.05 `--json` ignores filters and uses a fixed
v0.0.38 shape; from 23.02 the default data parser changes every release.

Rejected: controller first then `sacct` (argued from slurmdbd outages,
which is not a design input here); JSON.

To verify on the testbed: the `sacct` row exists when `sbatch` returns; the
healthy end-to-`sacct` latency, so the docs state a measured number.

### D-S7. State table

| Slurm | JobStatus |
|---|---|
| PD, RQ, RH, RF, RD | PENDING; PD with reason `DependencyNeverSatisfied` -> SKIPPED |
| R, CF, CG, SI, SO, RS | RUNNING |
| S, ST | SUSPENDED |
| CD | COMPLETED |
| F, NF, BF, SE | FAILED |
| CA | CANCELLED; reason `DependencyNeverSatisfied` -> SKIPPED |
| PR, RV | CANCELLED |
| TO, DL | TIMEOUT |
| OOM | KILLED |
| other | UNKNOWN |

OOM -> KILLED widens D18's wording to "a resource limit other than time";
`hpc status` shows `OUT_OF_MEMORY` in the details. Rejected: a new
`OUT_OF_MEMORY` status (one more enum value, rank and TUI colour for a state
the testbed cannot produce). Sources: squeue(1)/sacct(1) JOB STATE CODES;
`IS_JOB_FINISHED` in slurm_protocol_defs.h; `_job_complete` in job_mgr.c.

### D-S8. Return code from `ExitCode N:S`

`N` when `S == 0`, else `128 + S` (shell convention, `srun`'s own rule, and
what SGE gives today, e.g. 137). From the job record's `ExitCode` only;
`DerivedExitCode` (user-editable) is not used. sacct prints `N:0` or `0:S`,
never both non-zero (src: sacct/print.c).

## Dependencies

### D-S9. Native dependencies, no sentinels

`--dependency=afterok:1:2,afterany:3` is rendered from the same
`(parent, type)` pairs D8 defined. On Slurm there is no guard, no exit dir,
no `.rc` files and no exit code 113. `after` (start-based) works;
`emulate_afterok` stays an `[schedulers.sge]` key. `aftercorr`/`singleton`
go through `-S`. The user's rider: "unless we find a reason we need the
sentinel".

### D-S10. `kill_invalid_dependents`

`[schedulers.slurm] kill_invalid_dependents = true` (default) adds
`--kill-on-invalid-dep=yes` to every job with dependencies, so a dependent
whose parent failed is cancelled by Slurm (CA, reason kept) instead of
sitting PD/`DependencyNeverSatisfied` forever. Both forms map to SKIPPED
(D-S7). The user wanted the site switch rather than always-on. The case
"parent already purged (> `MinJobAge`) so afterok is rejected on 23.02+" is
out of scope: hpc-runner does not care about submitting a dependent after
its parent has finished.

To verify on the testbed: `Reason` survives into `sacct` after the cancel
(src: `_kill_dependent` keeps it; UNCONFIRMED live).

## Environment

### D-S11. `SBATCH_*` is documented, not scrubbed

Slurm precedence is `#SBATCH` < `SBATCH_*` environment < command line
(sbatch(1) INPUT ENVIRONMENT VARIABLES), the reverse of SGE's
`sge_request` < `#$` < command line. A user with `SBATCH_PARTITION` in their
profile overrides hpc-runner's `queue`. Decision: document it as a user
control that should not be combined with hpc-runner; no scrubbing, no
command-line rendering. Nested `hpc run` from inside a job (inherited
`SLURM_JOB_ID` makes `srun` a step) is out of scope until a user has the
case. Keep it simple.

### D-S12. `inherit_env` rendering

`true` -> `--export=ALL`. `false` -> `--export=NONE`, or
`--export=NAME=val,...` when `env_passthrough` is set (a value containing
`,` is refused with a clear error). `NONE` gives `SLURM_*` plus the login
environment built by `su - <user>` on the node, which is the Slurm spelling
of SGE strict mode (login shell startup files). `NIL` (23.02+, truly empty,
no `PATH`) is not used, so there is no version detection and no config key.
The batch template exports `SLURM_EXPORT_ENV=ALL` after the environment
block so `srun` steps inside a user's script inherit the script's
environment (modules included) rather than re-applying `NONE`. DIFF §2.2,
§2.3.

Rejected: `NIL` with a per-site key; always `ALL`.

## Interactive

### D-S13. `srun` is the launcher; no `salloc` subcommand

`srun` outside an allocation creates the allocation, runs the command,
returns its exit code: the `qrsh` contract. `salloc` is an advanced feature
hpc-runner does not support; the model is one job, one command, and a
second front end was what the October `submit` removal taught against.

### D-S14. `DISPLAY` travels with the environment

As on SGE: `--export=ALL` carries `DISPLAY`; in strict mode a site adds it
to `env_passthrough`. Target sites have network-visible displays (VNC /
ThinLinc / ETX on a login host). `srun --x11` is not used: it needs
`PrologFlags=X11`, `xauth` both ends and node-to-login connectivity, is
incompatible with the testbed's proctrack plugin, and only helps forwarded
(`localhost:10`) and local-socket displays. DIFF §3.5.

### D-S15. `--pty` only when stdin and stdout are both TTYs

Matches a local shell in every case: `hpc run bash`/`vim` get a pty;
piped input, scripts, cron, `xargs -P`, `| tee log`, `> file` and binary
output do not (a pty rewrites `\n` to `\r\n` and makes tools emit colour).
`srun` with a non-TTY stdin would warn and fall back anyway. Every
permutation (stdin/stdout TTY or not, piped input, `tee`, `xargs`,
`< /dev/null`) gets an integration test, plus a human-in-the-loop checklist
for prompt rendering, Ctrl-C, `vim`, an xterm.

### D-S16. Wrapper script on shared storage

As SGE: rendered to `~/.cache/hpc-runner/scripts/` (or `$HPC_SCRIPT_DIR`),
run as `srun <path>`, self-deleting unless `--keep-script`. slurmstepd
resolves the program on the compute node (src: slurmstepd/task.c). A shared
filesystem is a stated invariant of the tool, not something to work around.
Rejected: `--bcast`; inline `bash -c`.

### D-S17. `SLURM_EXIT_ERROR=112`

Set on the `srun` subprocess so `srun`'s own failures (bad partition,
account, option) exit 112 and raise `SubmissionError` with stderr, instead
of looking like the command exiting 1. 112 sits beside 113 (D8). SGE has no
qrsh equivalent and stays as is.

### D-S18. Interactive jobs refuse dependencies

As SGE (`refuse_interactive_dependencies`), for parity, although `srun
--dependency` would accept them. Relaxing it on Slurm is an open thread.

## Resources

### D-S19. `cpu`, `nodes`, `tasks`

`cpu` -> `--cpus-per-task=N` with `--ntasks=1` unless `tasks` is set
(one multi-threaded process, the EDA case); `tasks` -> `--ntasks`; `nodes`
-> `--nodes`. The script exports `SRUN_CPUS_PER_TASK=$SLURM_CPUS_PER_TASK`
for 22.05. First pass; the user flags the cpu/task model as a known Slurm
pain point that the docs must explain clearly, to be revisited on feedback.

Amended by D-S29: `nodes` and `tasks` are removed; `cpu` always renders
`--cpus-per-task=N --ntasks=1`.

### D-S20. `mem`

`mem` (per job, D10) -> `--mem=<MB>` via `Memory.to_slurm_mb()` (guard
against 0, which means "all of the node"). `--mem` is per node, so `mem`
with `nodes > 1` is a submit error pointing at `-S '--mem=...'`.
`[schedulers.slurm] memory_per = "job" | "cpu"` (default `"job"`); `"cpu"`
divides by `cpu`, rounds up, renders `--mem-per-cpu`. First pass; Slurm is
more complex than SGE here and commonly used functionality must not be
blocked.

Amended by D-S29: with `nodes` removed the `nodes > 1` error has nothing to
check and is dropped.

### D-S21. Resource kinds in config

Three of four schedulers require the request to say what kind of thing it
asks for (research 2026-11-04, IBM LSF 10.1 docs, PBS Pro 2021 RG/AG; the
full report with sources is Appendix A of `RESOURCES_COMPARISON.md`):

| | licence (cluster-wide counted) | device (per-node counted) | feature (per-node filter) |
|---|---|---|---|
| SGE | `-l lic=1` | `-l gpu=2` | `-l feat=1` |
| Slurm | `-L lic:1` | `--gres=gpu:2` | `-C feat` |
| LSF | `-R "rusage[lic=1]"` | `-gpu "num=2"` | `-R "select[feat]"` |
| PBS | `-l lic=1` (job-wide) | `-l select=1:ngpus=2` | `-l select=1:feat=True` |

The kind is a property of the resource, not the site. Config shape: one
map per kind, idiomatic TOML that merges by key like `env_vars`, cannot be
forgotten, is schema-validated per kind, and reads plainly after weeks away:

```toml
[tools.xcelium]
licenses = { xcelium = 1 }
devices  = { gpu = 2 }
features = ["bigmem"]
```

CLI: `--license NAME=N`, `--device NAME=N`, `--feature NAME`, mirroring the
keys. `resources = [...]` and `--resource` are removed in the same change
(the unknown-key error names the three maps). SGE renders all three as
`-l`; devices are per node (Slurm), per chunk (PBS), per job (LSF):
identical for single-node jobs, documented beyond.

Rejected: tagged list entries (`kind = ...`, clumsy TOML, default needed);
per-scheduler site lists (restates an intrinsic fact per scheduler section);
prefixed strings (grammar inside strings); three separate `JobSpec` lists;
`scheduler_args` only (no merge-by-name). A site catalogue with
per-scheduler aliases (`[resources.license.xcelium] sge = "xcelium_lic"`)
is a possible later layer (Parked).

### D-S22. Other mappings

`project` -> `--account` (sites enforce via associations; a wrong account is
rejected at submit). `priority` -> `--nice=-priority` (SGE: higher is more
important, users may only lower; Slurm nice: higher is less important,
users may only raise). `queue` -> `--partition`. `time` ->
`Duration.to_slurm()` (`D-HH:MM:SS`). Outputs: `%x.%j.out`, arrays
`%x.%A_%a.out`; Slurm merges stderr by default so `--error` is rendered
only when `stderr` is set (D9 for free). Task ids `<base>_<index>`.
`cancel` -> `scancel <id>`.

## Tooling and process

### D-S23. `[schedulers.slurm]` has two keys

`memory_per` (D-S20) and `kill_invalid_dependents` (D-S10). Nothing else
survived the session.

Confirmed by D-S28: `module_init_script` is a top-level key and
`module_system` no longer exists, so the two keys stand.

### D-S24. Tests: one per contract behaviour, N per scheduler output

Integration tests written against hpc-runner's contract (`hpc run --batch
exit 1` -> FAILED, rc 1) lose `pytestmark = family("sge")` and run on every
flavour through the `cluster` fixture. Tests asserting on a scheduler's own
output stay per family (`test_sge_*.py`, `test_slurm_*.py`). Unit side
unchanged in principle: core tests once, parser tests per flavour via
`slurm_fixtures`, goldens per scheduler. Tooling: `SlurmCluster` in
`cluster.py` (probe `sinfo`, dump `scontrol show job`, node control
`scontrol update NodeName=... State=`), `CAPTURE_MODULES["slurm"]`,
`SLOW_ACCOUNTING_FAMILIES` stays `{"sge"}` (no `flush_time` on Slurm), CI
cells `24.11` and `22.05` without the slow-accounting step.

Capture scenarios: ok, exit1, signal, timeout, cancelled_pending,
cancelled_running, held, dependent_skipped (kill on), dependent_pending
(kill off), array, array_partial, array_throttled, suspended, requeued,
node_fail, running, pending_resources; measurements: sacct row at submit,
healthy end-to-sacct latency.

### D-S25. `hpc kill` folded into `hpc cancel`

`kill` (added 2026-04-16, commit message "Add kill subcommand", no decision
recorded) is a filter loop around the same `scheduler.cancel()` as `cancel`.
Two verbs for one operation, and on Slurm a false echo of `scancel
--signal`. New form: `hpc cancel JOB_ID...` or `hpc cancel -N PATTERN |
--all [--pending|--running]`, `--force` on both. Timing free.

### D-S26. Paper trail

`docs/dev/slurm/`: this file, `SGE_SLURM_DIFFERENCES.md` (fact base),
`RESOURCES_COMPARISON.md` (resources on four schedulers; its Appendix A is
the LSF and PBS research behind D-S21). Testbed decisions as D-T items in
`docs/dev/testbed/TESTBED_REVIEW.md`, cross-referenced. D8's note
`--export=NONE,NAME=value` (restructure DECISIONS.md:169) is wrong (Slurm
takes `ALL`, `NONE`, `NIL` or an explicit list) and is corrected with the
first Slurm commit.

### D-S27. Order of work

0. Housekeeping: D-S25; D-S21 config/CLI change (SGE renders all as `-l`);
   D-S26 correction; this log.
1. `testbed/slurm/24.11/`, `common/`, `SlurmCluster`, smoke test.
2. `capture_slurm.py`, fixtures `slurm/24.11/`, `slurm_fixtures`.
3. Backend rendering half (args, submit/preview, templates, goldens); may
   overlap 1-2.
4. Backend parsing half against the fixtures.
5. Integration on 24.11: strip markers (D-S24), fix parity gaps, Slurm
   tests, CI cell.
6. `22.05` flavour, re-capture, CI cell.
7. User docs (cpu/tasks model, `--mem`, `SBATCH_*`, `DISPLAY`,
   licences/devices/features, pty rule); manual checklist on a real cluster.

## Amendments

Decided after the review of 2026-11-04, which read the decisions against
the code and found the places where the two disagreed (its open questions
are listed below). Each item names the decision it amends; the original keeps a
one-line pointer.

### D-S28. `module_init_script` is a top-level key; `module_system` removed (amends D-S23)

Open question Q4, now closed. `BaseScheduler.__init__` read `module_init_script` and
`module_system` from every `[schedulers.<name>]` section
(`schedulers/base.py:85-94` before this change) and the schema rejects a key
the backend's `CONFIG_KEYS` does not declare (`core/schema.py`,
`_validate_typed`), so D-S23's two keys would have left a Slurm site unable to
set either. Both keys arrived with the shared environment block (commit
7721244) and were put under the scheduler section because that block is
rendered by the scheduler; neither describes a scheduler.

Decision (user, 2026-11-05), in two steps the same day:

1. `module_init_script` describes the compute nodes and moves to the top
   level of the config file beside `scheduler` (last file wins). A copy left
   under a scheduler section is an error naming the move.
2. `module_system` is redundant: hpc-runner's module system is Environment
   Modules (the project brief), so a key saying so carries no information.
   Its one effect, `export MODULES_VERBOSITY=concise` before `module purge`
   (`_environment.sh.j2`), is now unconditional; Environment Modules 4.3+
   honours it and older versions ignore it. Its `"lmod"` value was accepted
   and had no effect anywhere (restructure `SESSION_REVIEW.md:124`). A
   `module_system` key left under a scheduler section is an error naming the
   removal (`RETIRED_SCHEDULER_KEYS` in `core/schema.py`).

So `[schedulers.slurm]` has exactly D-S23's two keys, `[schedulers.local]`
has none, and no module setting is per scheduler. Implemented on 2026-11-05
ahead of D-S27 step 0: schema, `HPCConfig.module_init_script`, the base
class, SGE and local `CONFIG_KEYS`, the template (every golden gained the
export's comment line), `hpc config` output and template,
`defaults/config.toml`, docs, tests.

### D-S29. The portable vocabulary grows by rule, not by survey (amends D-S2, D-S19, D-S20)

Adding a scheduler should not force changes in the other backends or break
existing configs. A complete map of common ground across SGE, Slurm, LSF and
PBS cannot be finished and verified up front (`RESOURCES_COMPARISON.md` §7
lists 15 unconfirmed items), so the protection is a rule for what enters the
vocabulary and how.

Decision (user, 2026-11-11; clause 3 softened, clause 4 changed and clause 5
confirmed by the user in the same session):

1. **Two tiers.** *Portable keys* are the `JobSpec` fields and their config
   keys; each is defined by what it means for the job, in hpc-runner's
   words, never by a scheduler's option. *Raw arguments* are
   `scheduler_args`, keyed by scheduler (`core/spec.py:73-75`); anything a
   portable key does not cover goes there, and only the active scheduler's
   list is used.
2. **Admission rule.** A request becomes a portable key only when its
   meaning can be written in one sentence that names no scheduler, and every
   supported backend either renders it from that meaning plus its own
   `[schedulers.<name>]` keys or refuses it (clause 5). The evidence for
   both is a primary source or a testbed capture (`docs/agents/evidence.md`).
3. **Growth is additive by default.** A new key defaults to unspecified
   (`None`). Renaming or redefining a portable key is avoided, but is done
   when the model is found to be wrong; it is then a recorded decision with
   a pointed error for the old key. A request can move from raw arguments to
   a portable key.
4. **Job shape.** Portable keys describe one process on one node. Under that
   shape a per-node, per-host, per-chunk and per-job amount are the same
   number (`RESOURCES_COMPARISON.md` R4, R9). `nodes` and `tasks` are
   removed: the `JobSpec`/`Job` fields and config keys, `-n/--nodes` and
   `--ntasks` (`cli/run.py:30-31`), the rows in `configuration.rst` and
   `cli.rst`, and the `[types.mpi]` example in `defaults/config.toml`. A
   config that sets either gets an error naming `scheduler_args`. They were
   Slurm's MPI options (`--nodes`, `--ntasks`); SGE and LSF have no
   node-count option (`RESOURCES_COMPARISON.md` R2), no backend ever
   rendered them (`sge/scheduler.py:243-244` ignores them silently) and the
   tool does not target MPI flows. An MPI job passes `-S '-N 2 -n 32'`.
5. **No silent drops.** A backend that cannot honour a portable key set on a
   job fails the submission with a message naming the key and the
   `scheduler_args` alternative. The tool handles this, not the user.
6. **Site names stay per backend.** A name the site chooses (a parallel
   environment, a memory complex) is a `[schedulers.<name>]` key declared in
   that backend's `CONFIG_KEYS`, as `memory_resource` is
   (`sge/scheduler.py:180-187`). A new backend adds its own section and
   touches no other.
7. **Contract test.** One table of backend against portable key, each cell
   "renders" or "refuses", checked in the unit suite. A new key or backend
   fails the suite until every cell is filled.

Consequences. `resources` fails clause 2 (it is SGE's `-l`, untyped) and is
removed, as D-S21 decided. D-S21's three maps and any further candidate
(`scratch`, exclusive use of a node) are each put through clause 2 as
separate items; none is decided here. Not implemented: the removal in
clause 4, the refusal in clause 5 and the test in clause 7 are code changes
still to make.

## Open questions

Raised by the review of 2026-11-04 and by the resources research; none is
decided. Numbers are the review's (Q4 was closed by D-S28). Order to take
them in: Q1, Q5-Q8, Q9, Q10-Q12 with Q17, Q13, Q3, Q14, Q15, Q16, then Q2
with Q18.

| Q | Hangs off | Gap |
|---|---|---|
| Q1 | D-S25 | D-S25 gives the form only. `hpc cancel 1 2`: one prompt or one each? Partial failure: `kill` exits 1, `cancel` exits 0. |
| Q2 | D-S21, D-S29 | D-S21's maps take whole numbers. In use today: `hostname=exec1` (`test_failures.py:52`, `test_resources.py:122`), `h_vmem=50M` (`test_resources.py:155`), `scratch=20G` (golden), `tmpfs=8G` (`configuration.rst:860`). Each goes through D-S29 clause 2. The user recalls scratch requests on LSF for EDA flows as real use; `RESOURCES_COMPARISON.md` R8 and §8 hold the facts and 13 further questions. |
| Q3 | D-S24 | D-S24 says node control is `scontrol update NodeName=... State=`; the inherited `docker kill` waits `SlurmdTimeout`. Which? |
| Q5 | no decision | No decision maps `${hpc.*}` for Slurm; `HPC_VARS` is required (the `full_environment` golden uses `${hpc.cpus}`). `cpus` is the real one: `$SLURM_CPUS_PER_TASK` is unset without `-c` (DIFF §2.5). In arrays `job_id` is each task's own id (DIFF §6). |
| Q6 | no decision | No decision has `--chdir`. Slurm defaults to the caller's cwd (DIFF §6), and `use_cwd = false` has no mapping. |
| Q7 | no decision | `shell` has no mapping: no `-S` on Slurm, the shebang decides (DIFF §6). |
| Q8 | D-T41 | SGE refuses whitespace in directive values. Same on Slurm, or quote? |
| Q9 | D-S22, D9 | D-S22 says `%x.%A_%a.out`; `output_pattern` (D9, documented in `configuration.rst`) gives `%x.%A.out.%a`. |
| Q10 | D-S17 | `run_command` has no `env` parameter, needed for `SLURM_EXIT_ERROR=112`. |
| Q11 | D-S17 | D-S17 says `SubmissionError` "with stderr"; interactive runs with `capture=False`, so there is none to carry. |
| Q12 | D-S12 | D-S12 describes sbatch. srun's `--export=NONE` fetches no login environment (DIFF §2.6) and srun has no `--get-user-env` (§2.3), so strict interactive jobs start with no `PATH`/`HOME`. |
| Q13 | D-S6 | D-S6 names `squeue` for listings and `sacct` for status; neither says where live job details come from (`scontrol show job` was assumed). |
| Q14 | D-S2 | D-S2 has trace in scope and nothing plans it. `trace/compare.py` `CHECKS` has only qsub/qstat/qacct; `trace/bundle.py` scrub rules and `scheduler_banner` are SGE-only, so a Slurm bundle would leave user, host and job names in place. |
| Q15 | D-S21 | `_build_qrsh_command` never reads `job.resources`, so `hpc run -R gpu=1 xterm` drops the request on SGE today. D-S21 says SGE renders all three as `-l`. Fix with the D-S21 change? |
| Q16 | D-S21, D-S24 | Rules proposed beyond the decisions: a count is `N >= 1`; a repeated CLI name is an error; capture scenarios `bad_partition`, `dependent_ok`. |
| Q17 | D-S13, D-S17 | `hpc run` interactive prints the failure and returns, so the shell sees 0 (`cli/run.py:189-194`); only the API has `exit_code`. D-S13's "returns its exit code" and D-S17 assume otherwise. Same on SGE. Fix, and where? |
| Q18 | D-S21 | Corrections the resources research asks for: D-S21 says LSF devices are per job, `bsub -gpu` says per host by default; D-S21 renders an SGE feature as `-l feat=1`, complex(5) documents `TRUE`/`FALSE` (`RESOURCES_COMPARISON.md` §8 items 1 and 5). |

## Parked

Each names the decision it hangs off.

- **JSON output** (D-S6). Text parsing chosen; revisit `sacct --json` /
  `squeue --json` once the floor is >= 23.11 and a data_parser version can
  be pinned.
- **`SLURM_EXPORT_ENV` knob** (D-S12). If a user has a script whose `srun`
  steps should start clean, add a `[schedulers.slurm]` key, not a CLI flag.
- **Advanced features investigation** (D-S2). Heterogeneous jobs,
  federation, job steps, `sbcast`, `salloc`, throttle changes after submit:
  a separate look at what exists and whether any applies to a single-shot
  job runner.
- **OOM capture** (D-S4, D-S7). A privileged cgroup testbed profile
  (CI-optional) to capture a real OUT_OF_MEMORY record.
- **`cpu` and `mem` model** (D-S19, D-S20). First passes; review against
  real use cases and feedback. Document the model clearly.
- **Interactive dependencies on Slurm** (D-S18). `srun --dependency` would
  allow what SGE refuses.
- **Site resource catalogue** (D-S21). `[resources.license.xcelium] sge =
  "xcelium_lic"` style declarations with per-scheduler aliases, layered on
  the three maps if a site needs it.
- **pty test matrix** (D-S15). Integration tests for every stdin/stdout
  permutation plus a human-in-the-loop checklist; run once on a real
  cluster.
- **Latency figure** (D-S6). Measure end-to-`sacct` on the testbed and put
  the number in the docs.
- **Comparison doc UNCONFIRMED items** (`SGE_SLURM_DIFFERENCES.md`, last
  section). Several `qrsh` questions can be settled on the SGE testbed; the
  Slurm ones (bash functions through `--get-user-env`, stale `SLURM_*`
  effects) on the Slurm testbed.

## Implementation notes

Facts the review of 2026-11-04 established by reading the code or running
the command, kept because each one would otherwise be met again. They are
as recorded then; re-check a line number before relying on it.

- Compose refuses `hpc-testbed-24.11` as a project name (run, Compose
  5.6.1); a `.` in the flavour name has to be mapped.
- A Slurm `compose.yml` without a `CLUSTERS["slurm"]` entry fails
  `test_the_repository_testbed_has_a_class_for_every_family`.
- Scheduler section keys are declared in the backend's `CONFIG_KEYS`, not
  in `schema.py`; render order comes from the shared `RENDER_ORDER`; a
  backend needs `HPC_VARS`; a new Sphinx page needs a toctree entry (`-W`).
- `scheduler_args` are split tokens; sbatch reads several options per
  directive line (`opt.c:_opt_batch_script`).
- Repeated `-L` and repeated `--gres` overwrite each other (`slurm_opt.c`,
  both tags); several of a kind are joined into one option.
- `__init__.py` exports `Resource`/`ResourceSet`. `job_info.py` and the two
  TUI files do not read `Job.resources`.
- An interactive dry run has no directives, and `--dry-run` does not print
  the argv.
- `has_accounting()` must be True or `--history` exits 1; `result` is in
  the base class; `get_job_details` fills `extra["tasks"]`.
- `sacct -P -n -j <id>` prints the default columns, not `COMPLETED|0:0`.
  `sacct` prints long state names; D-S7's table uses the two-letter codes.
- `hpc-testbed pytest -k` needs `--`; `trace compare` needs a directory.
- `nproc` proves nothing under `task/none`.
- A request larger than any node is rejected at submit, so it cannot
  produce a pending-for-resources capture.
- `PD` with `DependencyNeverSatisfied` is SKIPPED at once (D-S7), not
  PENDING.
- D-S26 misnames its target: the `--export=NONE,NAME=value` note is in D3,
  not D8.
- `key_for` is `merge.py:74`; the history window is `--since`, default
  `30m`.
