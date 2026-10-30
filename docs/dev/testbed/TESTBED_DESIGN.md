# hpc-runner test cluster — design (answers to the decision spec)

Seed: a question list (`hpc-runner-test-cluster-spec.md`, since deleted). This answers its questions against what
the code does today (see `../restructure/QA_REVIEW.md`), and ends with a concrete layout and a
first milestone. Section numbers follow the spec.

## 1. What is under test

**Interface: CLI only.** No DRMAA, no REST. SGE is driven by `qsub`, `qrsh`,
`qstat` (plain, `-xml`, `-j <id> -xml`), `qacct -j`, `qdel`. Output parsing is
the main risk, so the cluster must produce *real* output from the *real*
binaries; nothing else will catch a parser regression.

**Operations to exercise end to end**, taken from `BaseScheduler` and what the
CLI/TUI call:

| Operation | Code path | Note |
|---|---|---|
| batch submit | `qsub <script>` + `parse_qsub_output` | the `--` passthrough bug (QA B1) only shows up here |
| interactive submit | `qrsh ... <script>`, exit code captured | `qrsh` running a script needs no tty; fine under `docker exec -T` |
| status while queued / running / done | `qstat -j`, `qstat`, `qacct -j`, exit sentinel | three fallbacks, each needs a test |
| exit code | `qacct exit_status`, sentinel | |
| cancel | `qdel` → `CANCELLED` via `qstat` state `d` | |
| dependencies | `-hold_jid` + afterok sentinel guard | the guard is the only non-scheduler logic in the job script; needs a shared FS |
| arrays | `-t`, `$SGE_TASK_ID`, `base.index` ids | |
| resources | `-pe`, `-l mem_free`, `-l h_rt`, `-l <custom>` | names come from `[schedulers.sge]`, so the cluster's complex names must match a checked-in config |
| queues | `-q` | at least two queues to test selection and `queue@host` parsing |
| env, modules, venv, cwd | the job script itself | Environment Modules must be installed on *exec* nodes |
| stdout/stderr paths | `-o`/`-e`/`-j y` | `read_stdout()` on SGE is currently always empty (QA B7) |
| TUI/`hpc status` listings | `qstat -xml -u '*'`, `qacct -j -o user -b -e`, `qstat -j -xml` | needs ≥2 users and ≥2 nodes |

**Failure modes that must be reproducible**, and whether the container tier can:

- walltime exceeded: yes. SGE enforces `h_rt` with a signal from `execd`, no
  cgroups needed. Today this maps to `FAILED`; a test pins `TIMEOUT` once it is
  implemented.
- memory exceeded: yes for SGE. `h_vmem` is enforced with `setrlimit`, which
  works in a plain container. Slurm memory enforcement needs cgroups: non-goal.
- submission rejected (bad queue, bad PE, bad resource): yes. Today this
  escapes as `CalledProcessError`; the test pins `SubmissionError`.
- job vanished between `qstat` and `qacct`: yes, and it is the important one.
  `qacct` reads the accounting file, which `qmaster` flushes every 15 s by
  default. That window is exactly why the exit sentinel exists. Test it both
  ways: `flush_time 00:00:01` for the fast path, and a run with accounting
  disabled to force the sentinel path.
- scheduler unreachable: yes, `docker compose stop master` mid-test. Today a
  traceback; the test pins `SchedulerError`.
- node down mid-job: yes, `docker compose kill exec1`. Lower priority.

**Accounting: yes, keep it.** `qacct` backs `get_status`, `get_exit_code`,
`list_completed_jobs` and `hpc status --history`. SGE accounting is just a file
in `$SGE_ROOT/default/common`, so it costs nothing. For Slurm the equivalent is
`sacct`, which needs `slurmdbd` + MariaDB. Decision: the Slurm stack includes
them (real sites have them, and the TUI history view depends on it), with an
`ACCOUNTING=0` compose profile so the no-accounting fallback is also tested.

**Nested submission: install clients on exec nodes.** Debian's `gridengine-exec`
pulls `gridengine-client` anyway, and `make` targets that call `hpc run` are a
realistic pattern. Free.

**Topology: 1 master + 2 exec nodes, 2 queues.** Two nodes is the minimum that
exercises `queue@host` parsing, per-node listings, and placement. Three adds
nothing the code reads. Queues: `all.q` on both nodes, `short.q` with
`h_rt` capped, so queue selection and a rejected request are both testable.
Complexes: `mem_free`, `h_vmem`, `h_rt`, plus a consumable `gpu` and a
`license` complex so `ResourceSet` and `-l name=value` passthrough are real.

## 2. Containers

**Docker Compose.** Docker 29 and Compose 5.6 are already on this machine;
Podman is not installed. Nothing in §1 needs systemd, cgroups or PAM: both
`sge_qmaster` and `sge_execd` run as foreground processes, and `slurmctld`/
`slurmd` have `-D`. Do not support both runtimes.

**Separate containers per exec node.** The cost is one extra ~50 MB container;
the payoff is real hostnames in `qstat` output and a kill-able node.

**Budget:** the SGE stack is three Debian containers, well under 1 GB and under
20 s to a schedulable state after the image is built. Slurm with `slurmdbd` and
MariaDB is roughly double. Both fit the laptop; neither needs the Proxmox tier.

**Shared filesystem is mandatory and is itself a test subject.** hpc-runner
writes job scripts to `~/.cache/hpc-runner/scripts` and exit sentinels to
`~/.cache/hpc-runner/exit`, and `-cwd` assumes the working directory exists on
the exec node. So `/home` and a `/shared` work area are named volumes mounted
at identical paths in every container. One deliberate test mounts
`/shared` on the login node only and asserts the error is clear.

## 3. Versions and base images

**Everything from distro packages, nothing from source.** Verified today:

| Suite | `gridengine-*` | `slurm-wlm` |
|---|---|---|
| Debian bookworm | 8.1.9+dfsg-9/10 | 22.05.8 |
| Debian trixie | 8.1.9+dfsg-13.1 | 24.11.5 |
| Ubuntu noble | 8.1.9 | 23.11.4 |

SGE has exactly one open lineage (Son of Grid Engine 8.1.9), so there is no SGE
matrix; Debian trixie is the image. For Slurm the Debian suite *is* the version
matrix: `bookworm` gives 22.05 (the floor, `squeue --json` absent) and `trixie`
gives 24.11. One Dockerfile with `ARG DEBIAN_SUITE=trixie` and the matrix is a
build-arg, as the spec suggested. Image builds take minutes, not tens of
minutes, and Rocky is unnecessary.

**Confirm which SGE the real target clusters run.** If they are Univa/Altair
Grid Engine, output formats differ in places (`qstat -xml` schema, `qacct`
fields) and the parser tests need fixture files captured from a real UGE
cluster in addition to the SoGE container. Capture those once, check them in
under `tests/fixtures/uge/`, and the unit tests cover the delta.

## 4. Where tests run

Two modes, same test files, chosen by the fixture. Both are worth having.

**Mode A: host-side with command shims (default for development).** hpc-runner
only ever runs scheduler binaries via `subprocess`, so a directory of shims
named `qsub`, `qstat`, `qacct`, `qdel`, `qrsh`, `qhost`, `qconf` placed first
on `PATH` that each `exec docker compose exec -T -u $USER -w $PWD login <cmd>
"$@"` lets the *real* Python API run on the host against the real cluster. One
`pytest` command, breakpoints work, no nested test runner. Requirements:

- the work area is a bind mount at the **same absolute path** on host and in
  every container (`/tmp/hpc-testbed` → `/tmp/hpc-testbed`), so `-cwd`, `-o`
  and sentinel paths agree. `HPC_SCRIPT_DIR` and `HPC_EXIT_DIR` already exist
  to redirect the cache dirs there.
- the container test user is created with the host UID (build-arg) so files
  are writable from both sides.
- limitation: `-V` exports the `docker exec` environment, not the host shell's.
  Tests of `inherit_env` run only in mode B.

**Mode B: in-container (CI and release gate).** A `login` service with Python
3.10–3.12, Environment Modules and the scheduler clients; the source tree bind
mounted at `/src` and installed editable at start (wheel for a release run).
The host side is one driver test per matrix cell that runs
`docker compose exec login pytest tests/integration --junitxml=/shared/junit.xml`
and republishes the XML. This is the only mode that tests `inherit_env`, the
submit-host `$VAR` expansion against a module-managed shell, and the venv
auto-capture.

**Invocation:** plain `docker compose` driven from `conftest.py` via
`subprocess`, not testcontainers-python. The stack must stay usable by hand
(`docker compose -f testbed/sge/compose.yml up`, then poke with `qstat`), and
testcontainers adds a dependency for little gain when the compose file already
exists. A `Makefile` target wraps each mode.

## 5. Test structure and lifecycle

- **Tiering:** `tests/unit/` is the existing suite (rename), runs everywhere.
  `tests/integration/` carries `pytestmark = pytest.mark.integration`, is
  deselected by default (`addopts = -m "not integration"`), and skips with a
  reason when Docker is missing unless `--require-cluster` is passed (CI passes
  it so a broken Docker is a failure, not a silent skip).
- **Parametrisation:** one `cluster` fixture parametrised over
  `--cluster=sge,slurm` (default `sge` until Slurm exists). Each test gets a
  `Cluster` object: `scheduler_name`, `run(argv)`, `workdir`, `users`,
  `wait_for(job_id, state, timeout)`, `kill_node(name)`, `stop_master()`.
- **Scope:** session-scoped stack, function-scoped cleanup: `qdel -u alice -u
  bob`, wait for the queue to drain, fresh `workdir` per test, truncate the
  accounting file between modules that assert on history.
- **Readiness:** `qhost` lists all exec hosts with numeric load (not `-`),
  `qstat -f` shows no queue in `au`/`E` state, and `qsub -sync y -b y true`
  returns 0. Poll with a 60 s cap.
- **Determinism:** set `schedule_interval 0:0:1`, `flush_submit_sec 1`,
  `flush_finish_sec 1` in `sched_conf`, `reporting_params accounting=true
  flush_time 00:00:01` in the global config. hpc-runner's own tests use
  `poll_interval=0.5`. Tests wait on observed state through `wait_for`, never
  bare `sleep`, and every wait has a timeout that fails the test with the last
  `qstat` output attached.
- **Users:** `alice` (1001) and `bob` (1002) baked into every image, both SGE
  managers' `userset` members. Needed for `list_active_jobs(user=)`,
  `list_completed_jobs(user=)`, `hpc status --all`, and the `-u` filter.
- **Modules:** mount `tests/modulefiles` (which already has `dummy_tool`) at
  `/opt/modulefiles` on exec nodes, so a job can assert `which dummy_tool`.

## 6. CI

- **Current `ci.yml` runs only on tag pushes.** Fix that first: lint and unit
  tests on every push and PR; integration on PR and `main`.
- **GitHub Actions** is sufficient; `ubuntu-latest` has Docker and Compose.
- **Prebuilt images on GHCR**, built by a separate workflow that triggers on
  `testbed/**` changes, tagged with the short hash of the `testbed/<name>` tree
  so a stale image can never match a changed Dockerfile. Compose pulls by that
  tag; local runs fall back to `docker compose build`.
- **Budget:** 5 minutes per matrix cell including pull. Cells: `sge`, then
  `slurm-22.05`, `slurm-24.11`. Run cells in parallel jobs.

## 7. Layout

In-repo. The stacks are small and specific to what hpc-runner parses; keeping
them next to the parsers keeps them honest. Split out later if a second tool
needs them.

```
tests/
  unit/                     existing tests, moved
  integration/
    conftest.py             --cluster, --require-cluster, Cluster fixture, shims
    test_submit.py          batch, interactive, passthrough, rejected submission
    test_status.py          queued/running/done, qacct window, sentinel fallback
    test_dependencies.py    afterok/afterany, pipeline, guard aborts on failure
    test_arrays.py
    test_resources.py       pe, mem, h_rt kill, custom complex, queues
    test_environment.py     modules on exec node, env_*, venv, cwd, -V (mode B)
    test_listing.py         list_active_jobs, list_completed_jobs, get_job_details
    test_failures.py        master stopped, node killed
testbed/
  common/
    users.sh                alice/bob with fixed UIDs; test user at host UID
    modulefiles/            symlink to tests/modulefiles
  sge/
    Dockerfile              debian:trixie, gridengine-master|exec|client, modules
    compose.yml             master, exec1, exec2, login; volumes home, shared, sge_cell
    entrypoint.sh           role from $SGE_ROLE; master bootstraps cell, queues, complexes
    cluster.conf            queues, PE, complexes, sched_conf as `qconf` input
    ready.sh                readiness probe from §5
    clean.sh                per-test cleanup
  slurm/                    same shape; slurm.conf.j2 templated on node count
  bin/                      PATH shims for mode A
  Makefile                  up / down / shell / test-a / test-b per scheduler
hpc-runner-testbed.toml     [schedulers.sge] names that match cluster.conf
```

Adding PBS later is a new `testbed/pbs/` directory and a value in the
`--cluster` option. The `Cluster` fixture interface is scheduler-neutral by
construction: everything scheduler-specific lives in `ready.sh`/`clean.sh`.

## 8. Non-goals, confirmed with one exception

Out of scope: cgroup/memory enforcement on Slurm, controller HA, GRES/GPU
scheduling beyond a consumable complex, performance testing, LSF. **In scope**,
against the spec's default: `h_rt` and `h_vmem` enforcement on SGE, because
they are rlimit/signal based and work in a plain container, and the resulting
`TIMEOUT`/`FAILED` mapping is user-visible.

## First milestone

1. `testbed/sge/` builds and `make -C testbed sge-up` reaches ready in under
   60 s cold. Manual `qsub -b y hostname` from `login` lands on `exec1` or
   `exec2` and `qacct` sees it within 2 s.
2. `tests/integration/conftest.py` with the `Cluster` fixture in mode A and
   the shims.
3. Six tests that each pin a known gap from the QA review, so the suite earns
   its keep on day one: batch `--` passthrough (B1), `read_stdout` on SGE (B7),
   CLI `--depend afterok:` aborts on parent failure (B6), rejected queue raises
   `SubmissionError`, job finished inside the `qacct` flush window resolves via
   sentinel, `h_rt` kill maps to `TIMEOUT`.
4. Mode B driver and the GHCR image workflow.
5. Slurm stack, reusing `common/` and the fixture, when the Slurm backend starts.

Sources checked for package versions: [Debian trixie gridengine-master](https://packages.debian.org/trixie/utils/gridengine-master),
[Debian src:slurm-wlm](https://packages.debian.org/src:slurm-wlm),
[Debian tracker slurm-wlm](https://tracker.debian.org/pkg/src:slurm-wlm),
[giovtorres/slurm-docker-cluster](https://github.com/giovtorres/slurm-docker-cluster) (reference stack shape for Slurm).
