# hpc-runner test cluster: implementation plan

Status: decided 2026-10-29; **phases 0–4 built the same day** on
`refactor/decisions` (`testbed/`, `tests/integration/`, the CI `integration`
job). Where the build diverged from this text, `TESTBED_REVIEW.md` records
the decision and why (D-T1..D-T35) and the bugs the tests found (BUG-T1..T4);
this file is left as the plan that was followed, not rewritten to match.
Inputs: the original question list (since deleted) (the questions),
`TESTBED_DESIGN.md` (the answers), `TESTBED_RESEARCH.md` (prior art and fact
checks, every claim with a URL). This plan supersedes the design's "First
milestone" section and corrects it where the research contradicted it.
Phase 5 (further flavours) and phase 6 (Slurm) are not started.

The plan is written against the refactored code on branch `refactor/decisions`
(D1–D15): one `run_command` subprocess seam, `SubmissionError` with stderr
attached, `JobSpec`/`resolve`/`Job`, `JobResult.stdout_path`, `JobStatus.SKIPPED`,
exit code 113, `emulate_afterok`, `${hpc.*}`.

## 0. What changed since TESTBED_DESIGN.md

| Design said | Research found | Plan does |
|---|---|---|
| SGE has one open lineage | `daimh/sge` (SoGE continuation, tested weekly on trixie) and Open Cluster Scheduler (Univa lineage, monthly releases, ready compose stack) are both live; Debian may re-base on one (#1121046) | Each SGE implementation is its own testbed flavour under `testbed/sge/<flavour>/`, never merged. Debian trixie packages (SoGE lineage, flavour `soge`) are the first; OCS is the next candidate |
| Install packages in the Dockerfile | `gridengine-master` postinst runs `init_cluster` at install time and bakes the build hostname in, unless `shared/gridengineconfig=false` | Preseed `false`; run `init_cluster` in the entrypoint on the shared cell volume (idempotent) |
| Daemons run in the foreground | True via `SGE_ND=1` (man page + source), but no prior-art stack uses it | Spike 1 verifies it; fallback is the init script plus `tail -f messages` |
| `flush_time 00:00:01` | Syntax is `key=value` inside `reporting_params`; `accounting_flush_time=0` disables buffering outright | Default profile sets `flush_time=00:00:01`; a `SLOW_ACCOUNTING=1` variant keeps the 15 s window for the sentinel test |
| `h_rt` kill maps to `TIMEOUT` | `qacct` shows `failed 100`, `exit_status 137` for any signal death; `failed 37` only for qmaster enforcement | The timeout test asserts `FAILED` with exit 137 and `failed 100` today; `TIMEOUT` is a separate decision (SESSION_REVIEW.md) |
| Test user at host UID | Debian default `min_uid 1000` refuses lower UIDs | Testbed global config sets `min_uid 0`, `min_gid 0` |
| `qrsh` just works | Defaults to `-now y` (fails at once if no slot), needs `INTERACTIVE` queues and execd as root; ignores `-o/-e` | `all.q` is `BATCH INTERACTIVE`; execd runs as root; interactive tests never fill both nodes |
| bookworm Slurm lacks `squeue --json` | `--json` exists since 21.08; 22.05 lacks the versioned data_parser schema (23.11+) | Slurm matrix stays bookworm/trixie; the parser must handle both JSON shapes |
| Docker 29 / Compose 5.6 | Runners have Docker 28.0.4 / Compose 2.38.2 | Use only Compose features present in 2.38 (`--wait`, healthchecks, profiles) |

Reusable prior art, by permission: dask-jobqueue `ci/sge` (BSD-3) for the debconf
keys, `init_cluster` call, `qconf -A*` file layout and the `qhost`-count readiness
loop; xenon's `qping -info` healthcheck; Debian's own `init_cluster` and
`default-configuration`. The images themselves are obsolete; nothing is copied
verbatim except the debconf keys.

## 1. Layout

```
testbed/
  README.md                 how to run by hand, how to debug inside the login container, how to add a scheduler
  Makefile                  sge-up / sge-down / sge-shell / sge-logs / sge-test / sge-clean, all take FLAVOUR=soge
  common/
    users.sh                alice 1001, bob 1002, tester 1003 (fixed; no host UID anywhere)
    wait-for.sh             poll helper with timeout and last-output capture
  sge/
    README.md               the SGE flavour support matrix (section 6, "further flavours")
    soge/                   Son of Grid Engine lineage: Debian trixie gridengine-* 8.1.9
      Dockerfile              debian:trixie-slim; ARG ROLE unused (one image, role from env)
      compose.yml             master, exec1, exec2, login; profiles: default, slow-accounting
      entrypoint.sh           case $SGE_ROLE in master|exec|login
      hpc-runner-testbed.toml [schedulers.sge] names matching cell/*.conf; config_version = 1
      cell/
        global.conf           qconf -Mconf input (min_uid 0, reporting_params, gid_range)
        sched.conf            qconf -Msconf input (schedule_interval 0:0:1, flush_*_sec 1)
        pe-smp.conf           qconf -Ap
        hg-allhosts.conf      qconf -Ahgrp @allhosts
        q-all.conf            qconf -Aq all.q  (BATCH INTERACTIVE, @allhosts, slots 2 per host)
        q-short.conf          qconf -Aq short.q (h_rt 00:01:00, @allhosts)
        complexes.conf        qconf -Mc: gpu CONSUMABLE, xcelium_lic CONSUMABLE
        userset-arusers.conf  alice, bob, tester as arusers
      ready.sh                readiness probe (section 4)
      clean.sh                per-test cleanup (section 5)
tests/
  unit/                     the existing suite, moved (tests/conftest.py stays at tests/)
  integration/
    conftest.py             --cluster, --flavour, --require-cluster, Cluster fixture (runs inside login)
    cluster.py              Cluster protocol + SGECluster(flavour) (scheduler commands, waits, node control)
    test_submit.py ... test_failures.py   (section 6)
.github/workflows/
  ci.yml                    integration job (section 7)
```

Two levels: the scheduler family (`sge`, later `slurm`) owns the fixture class
and the test files; each vendor implementation of that family is a *flavour*,
a self-contained directory with its own image, compose file, cell
configuration and testbed config. Flavours of one family are never merged
into one stack: a test run exercises exactly one implementation, and
`testbed/sge/README.md` records per flavour which features, flags and parser
paths are tested and supported. Shared pieces are factored out only when a
second flavour needs them. Adding Slurm is `testbed/slurm/<flavour>/` plus
`SlurmCluster`.

## 2. The SGE stack, `soge` flavour

### 2.1 Image (`testbed/sge/soge/Dockerfile`)

One image for all four roles, selected at run time by `SGE_ROLE`. Build steps:

1. `FROM debian:trixie-slim`. No build arguments that vary per machine: the
   image is identical everywhere. It is built locally every time
   (`docker compose build`, decided 2026-10-29: no registry, no tags); Docker's
   layer cache makes a rebuild free when nothing under `testbed/sge/soge/` changed.
2. Preseed debconf for `gridengine-common`, `gridengine-master`, `gridengine-client`:
   `shared/gridengineconfig boolean false`, `shared/gridenginecell string default`,
   `shared/gridenginemaster string master`.
3. Satisfy the `mailx` dependency without an MTA: install `s-nail` first (provides
   `mailx`, no `default-mta` dependency). Spike 1 confirms; fallback is dask's
   "postfix, No configuration" preseed.
4. `apt-get install --no-install-recommends gridengine-master gridengine-exec
   gridengine-client gridengine-common environment-modules python3 bash procps
   less vim-tiny` (clients on every node so a job may submit jobs; Modules on
   every node so a job may `module load`).
5. `common/users.sh`: `alice` 1001, `bob` 1002, `tester` 1003, all fixed, in
   every image, so the job owner has the same passwd entry on qmaster and
   each exec node (SGE looks the owner up on every host; this is what Galaxy
   and dask-jobqueue do). Home dirs are created under `/home`, which is a
   named volume at run time, so the entrypoint recreates them if missing.
6. `COPY cell/ entrypoint.sh ready.sh clean.sh /opt/testbed/`.
7. `ENV SGE_ROOT=/var/lib/gridengine SGE_CELL=default`.
8. `HEALTHCHECK` per role, set in compose rather than the Dockerfile (section 2.3).

Expected size: under 300 MB. Build time: under two minutes cold.

### 2.2 Entrypoint (`testbed/sge/soge/entrypoint.sh`)

Common prologue: `exec > >(tee -a /var/log/testbed.log) 2>&1`; `set -euo pipefail`;
ensure home dirs; mount check that `/shared` and `/home` exist.

`master`:
1. `init_cluster /var/lib/gridengine default /var/spool/gridengine/spooldb sgeadmin`
   as `sgeadmin` (idempotent: skips if the spool exists).
2. Write `act_qmaster` = `master`; `host_aliases` empty.
3. `SGE_ND=1 sge_qmaster &` then wait for `qping -info master 6444 qmaster 1`.
4. Apply `cell/*.conf` with `qconf -Mconf`, `-Msconf`, `-Mc`, `-Ap`, `-Ahgrp`,
   `-Aq`, `-Au`; add `exec1`, `exec2` with `qconf -ah`/`-as`, and `login` with
   `-as`. All idempotent (`-M*` replaces, `-A*` errors on existing are ignored
   with a check that the object exists).
5. `wait` on the qmaster pid (foreground; container exits when qmaster dies).

`exec`:
1. Wait for `qping -info master 6444 qmaster 1`.
2. Write `act_qmaster`. `SGE_ND=1 exec sge_execd` as root (needed for `qrsh`).
   The host is added to the `@allhosts` group by the master step 4 (which may
   run before execd is up; `qconf -ah` does not need the daemon).

`login`:
1. Wait for the master. Install hpc-runner (`uv pip install -e /src --no-deps`
   into each prebuilt venv, section 4.1). `exec sleep infinity`.

### 2.3 Compose (`testbed/sge/soge/compose.yml`)

- Services `master`, `exec1`, `exec2`, `login`; for each, `hostname:` and
  `container_name:` equal the service name (dask's pattern; spike 2 checks that
  SGE's name resolution is happy with Compose's DNS; fallback is a user-defined
  network with static IPs plus `extra_hosts`).
- Volumes: `home:/home`, `cell:/var/lib/gridengine` (shared so `qacct` on
  `login` reads the accounting file and so `act_qmaster`/`bootstrap` agree),
  `work:/work` (named volume shared by every container; job scripts,
  sentinels and output live here), `../../tests/modulefiles:/opt/modulefiles:ro`,
  `../..:/src:ro` (login only, the source tree),
  `/var/run/docker.sock:/var/run/docker.sock` (login only, for the failure
  tests). No writable directory is bind-mounted from the host, so no host
  UID is involved; results leave the stack as JUnit XML via `docker compose cp`.
- Healthchecks: master `qping -info master 6444 qmaster 1`; exec
  `qping -info $(hostname) 6445 execd 1`; login `qstat -f` exits 0.
  `depends_on: condition: service_healthy` from exec to master and login to
  exec1/exec2, so `docker compose up --wait` returns only when the stack is up.
- `profiles`: `slow-accounting` starts the master with `ACCOUNTING_FLUSH=00:00:15`
  (entrypoint substitutes it into `global.conf`), used by one test only.
- No `privileged`, no `cap_add`. SGE needs neither. `login` has the `docker`
  CLI (the `docker.io` package's client only) and `docker-compose-plugin`.

### 2.4 Cell configuration (`testbed/sge/soge/cell/`)

- `global.conf`: `min_uid 0`, `min_gid 0`, `reporting_params accounting=true
  reporting=false flush_time=00:00:01 joblog=false sharelog=00:00:00`,
  `execd_params ENABLE_ADDGRP_KILL=true`, `gid_range 65400-65500`,
  `max_unheard 0:0:30`, `administrator_mail none`.
- `sched.conf`: `schedule_interval 0:0:1`, `flush_submit_sec 1`,
  `flush_finish_sec 1`, `max_reservation 0`.
- `complexes.conf`: standard set plus `gpu gpu INT <= YES YES 0 0` and
  `xcelium_lic lic INT <= YES YES 0 0`; `h_vmem` requestable; `mem_free`
  requestable.
- `q-all.conf`: `qtype BATCH INTERACTIVE`, `slots 2`, `pe_list smp`,
  `hostlist @allhosts`, `shell /bin/bash`, `shell_start_mode unix_behavior`,
  `complex_values gpu=1` per host (so `-l gpu=2` is rejected: a real
  "rejected resource" case).
- `q-short.conf`: `h_rt 00:01:00`, `s_rt INFINITY`, `qtype BATCH`.
- `hpc-runner-testbed.toml` names these: `parallel_environment = "smp"`,
  `memory_resource = "mem_free"`, `time_resource = "h_rt"`, `memory_per = "slot"`.

## 3. Readiness and cleanup

`ready.sh` (run on `login`, polled by the fixture with a 90 s cap, last output kept
for the failure message):

1. `qhost` lists `exec1` and `exec2` with numeric load (not `-`).
2. `qstat -f` shows `all.q@exec1` and `all.q@exec2` with no `au`, `E`, `d` state.
3. `qconf -sel` lists both; `qconf -ss` lists `login`.
4. `sudo -u alice qsub -sync y -b y -cwd -o /dev/null -j y true` exits 0 within
   20 s, and `qacct -j <that id>` succeeds within 5 s (accounting path works).

`clean.sh` (per test, on `login`): `qdel -u '*'` (ignore "no jobs"), wait until
`qstat -u '*'` is empty (cap 30 s; `qdel -f` after 15 s), remove
`/work/<test>/`. Between modules that assert on history, the fixture
truncates `/var/lib/gridengine/default/common/accounting` and restarts nothing
(qmaster appends).

## 4. One execution mode: tests run inside the login container

Decided 2026-10-29. An earlier draft had a second, host-side mode driving the
cluster through PATH shims so that a debugger could step through hpc-runner
while real scheduler output flowed through it. Dropped: the code under test
is argv building, subprocess handling, text parsing and path computation,
and every failure is diagnosable from what is already captured (`--verbose`
logs each argv, `SubmissionError` carries stderr and the script, `wait_for`
attaches the last `qstat` output). The host-side mode was the only reason a
host UID, PATH shims and a `-V` caveat existed. This also matches every
scheduler-in-Docker project surveyed (dask-jobqueue, Galaxy, giovtorres,
Toil), none of which match host UIDs.

### 4.1 Running the suite

- `login` has `uv` and venvs for 3.10, 3.11, 3.12 at `/opt/venv/<ver>` with
  hpc-runner's test extras preinstalled at image build (from `pyproject.toml`,
  so the image is rebuilt when dependencies change). At start, `uv pip install -e /src --no-deps` into each, so source
  edits on the host are live without a rebuild.
- The one command is `make -C testbed sge-test [PY=3.12] [FLAVOUR=soge]`, which runs
  `docker compose exec -T -u tester login /opt/venv/$PY/bin/pytest
  tests/integration --cluster sge --flavour $FLAVOUR --junitxml=/work/junit-$PY.xml` and copies
  the XML out with `docker compose cp`. CI uploads it as an artefact.
- `make -C testbed sge-shell` opens a shell in `login` as `tester` with the
  venv active, for `pytest --pdb`, `qstat -f`, reading `/work`, and so on.
- Inside the container the fixture talks to the scheduler directly:
  `Cluster.run` is a plain `subprocess.run`, and hpc-runner's own seam runs
  the real binaries. `HPC_SCRIPT_DIR` and `HPC_EXIT_DIR` point under
  `/work/cache`, `HPC_SCHEDULER=sge`, `HPC_RUNNER_CONFIG=/src/testbed/sge/$FLAVOUR/hpc-runner-testbed.toml`.
- Because the test process is the submitting shell, the environment tests
  (`inherit_env`, venv capture, `-V`) need no special marker.
- The compose stack stays independently usable by hand (`make sge-up`, then
  `make sge-shell`).

### 4.2 Fixture design (`tests/integration/conftest.py`, `cluster.py`)

```python
class Cluster(Protocol):
    name: str                      # "sge"
    flavour: str                   # "soge"; tests that depend on one implementation check it
    workdir: Path                  # fresh per test under /work
    users: tuple[str, ...]         # ("alice", "bob", "tester")
    def run(self, argv, *, user="tester", check=True, timeout=60) -> CompletedProcess  # sudo -u for alice/bob
    def wait_for(self, job_id, state: JobStatus | set[JobStatus], timeout=60) -> JobStatus
    def kill_node(self, name) -> None       # docker compose kill, via the mounted socket (see below)
    def stop_master(self) -> None; def start_master(self) -> None
    def accounting_truncate(self) -> None
    def config(self) -> HPCConfig            # load_config("hpc-runner-testbed.toml") with the right scheduler section
    def scheduler(self) -> BaseScheduler     # get_scheduler("sge", config=...)
    def host(self) -> Host                   # Host(env with HPC_* set, cwd=workdir, user="tester")
```

Options: `--cluster sge` (default), `--flavour soge` (default; the stack
that is up), `--require-cluster` (fail instead of skip when no scheduler is
reachable). A test that only holds for some implementations says so with
`@pytest.mark.flavour("soge", ...)`, which skips elsewhere with the reason
recorded, and the skip list is what feeds the support matrix. `addopts = -m "not
integration"` in `pyproject.toml`; `tests/integration/` sets `pytestmark =
pytest.mark.integration`. Stack lifecycle is the Makefile's job (`sge-up`
runs `docker compose build` then `docker compose up -d --wait`, then polls
`ready.sh`; `sge-down` runs `down -v`), not pytest's: the session fixture
only verifies readiness and fails with the `ready.sh` output otherwise.
Function fixture: `workdir` + `clean.sh` after each test. Every wait goes
through `wait_for` with a timeout whose failure message includes the last
`qstat -f` and the job's `qstat -j` output.

Node and master control (`kill_node`, `stop_master`, `start_master`) run
`docker compose kill|stop|start <service>` from inside `login`, which has
the `docker` CLI installed and the host's `/var/run/docker.sock` bind-mounted
(decided 2026-10-29: this is a test helper on a laptop or a throwaway CI
runner, not something that needs guarding). The mount sits in the default
profile so nothing extra has to be enabled; the two failure tests skip with
a clear reason when the socket is absent, so a stack started without it
still runs the rest of the suite.

## 5. Tests (first set, each pins a gap the QA review found)

All through the public API (`resolve` + `scheduler.submit`) or the CLI
(`CliRunner`), never by hand-rolled `qsub`.

| File | Test | Asserts |
|---|---|---|
| `test_submit.py` | batch echo | `JobResult.job_id` numeric; `wait()` COMPLETED; `read_stdout()` == "hello" (B7, D9) |
| | passthrough | `scheduler_args={"sge": ["-l","gpu=1"]}` lands as one `#$` line and `qstat -j` shows the hard resource (B1) |
| | rejected queue | `queue="nope.q"` raises `SubmissionError` whose message contains qsub's reason (D13) |
| | rejected resource | `-l gpu=2` raises `SubmissionError` (complex_values gpu=1) |
| | interactive | `interactive=True` runs `qrsh`, `exit_code` is the command's, `job_id is None`, stdout streams (captured via capfd) |
| | interactive, no slot | both nodes busy → `qrsh -now y` failure surfaces as `SubmissionError`, not a hang (documents the `-now` question) |
| `test_status.py` | queued → running → done | `wait_for` sees PENDING (hold with `-h` via scheduler_args), then RUNNING, then COMPLETED; `get_statuses` one `qstat -xml` per tick |
| | qacct window | `slow-accounting` profile: a finished job resolves via the sentinel within 2 s, then via `qacct` after 15 s |
| | aged out | accounting truncated, sentinel removed → `wait(unknown_after=2)` returns UNKNOWN, no spin |
| `test_dependencies.py` | afterok cascade | parent `exit 1`; child reports SKIPPED, exit 113; grandchild SKIPPED (B6, D8) |
| | afternotok | parent fails → child runs; parent succeeds → child SKIPPED |
| | afterany | child runs either way |
| | array parent | 3-task array, task 2 fails → dependent SKIPPED; all succeed → runs |
| | CLI `--depend` | `hpc run --batch --depend afterok:<id>` goes through the same path |
| `test_arrays.py` | 1-5 | `ArrayJobResult.wait()` per-task statuses; `task_stdout_path(n)` exists and holds `$SGE_TASK_ID`; task 3 fails → only task 3 FAILED |
| `test_resources.py` | pe, mem, time | `qstat -j` shows `-pe smp 2`, `mem_free`, `h_rt`; `mem="2G"` with `cpu=2`, `memory_per="slot"` shows `1G` |
| | h_rt kill | `short.q`, `sleep 120` → FAILED, `get_exit_code()==137`, `qacct failed` 100 (by decision, no TIMEOUT inference) |
| | h_vmem | `-l h_vmem=50M` + a 200 MB allocation → FAILED (allocation error or kill) |
| | queue selection | `queue="short.q"` appears as `short.q@exec*` in `list_active_jobs` |
| `test_environment.py` | modules on node | `modules=["dummy_tool"]`, `modules_path=["/opt/modulefiles"]`, command `which dummy_tool` → path (D3 contract) |
| | regardless of submitter | same, with the submit shell having `dummy_lib` loaded → job sees only `dummy_tool` |
| | `${hpc.cpus}` | `env_vars={"MAKEFLAGS": "-j${hpc.cpus}"}`, `cpu=2` → job prints `-j2` |
| | env order | prepend/append/set/unset round-trip with quoting-hostile values (B4) |
| | venv | `VIRTUAL_ENV` set on the submit side → job's `python` is the venv's; `venv=False` → system python |
| | inherit_env false | `env_passthrough=["DISPLAY"]` → job sees `DISPLAY`, not an unrelated submit-side variable |
| | cwd | `-cwd` with a workdir under `/work` |
| `test_listing.py` | two users | alice and bob submit; `list_active_jobs(user="alice")` filters; `list_completed_jobs(user="bob")` after flush; `get_job_details` fields |
| `test_failures.py` | master stopped | `stop_master()` → `get_status` raises `SchedulerError` (not a traceback) within the 60 s timeout; `start_master()` → recovers |
| | node killed | `kill_node("exec1")` mid-job → job ends FAILED or is rescheduled (document which, assert no exception) |
| | shared FS missing | compose override without `/work` on exec → job fails and `read_stdout()` returns None; the test documents the symptom |

## 6. Phases, order, and acceptance

**Phase 0: spikes (half a day).** Throwaway `docker run` experiments before any
file is committed, each with a yes/no result recorded in `testbed/README.md`:

1. `SGE_ND=1 sge_qmaster` and `sge_execd` stay in the foreground on trixie
   packages; `s-nail` satisfies `mailx` without an MTA.
2. Compose DNS: with `hostname == container_name`, `qhost` shows numeric load and
   `qrsh -now y hostname` from `login` returns `exec1`/`exec2`. If not, static
   IPs plus `extra_hosts`.
3. `docker compose exec -T` + `qrsh <script>` returns the script's exit code
   and streams output.
4. `init_cluster` on a persistent volume is idempotent across `down`/`up`.

**Phase 1: SGE stack, `soge` flavour (1–2 days).** `testbed/sge/soge/*`, `Makefile`, `README.md`.
Acceptance: `make -C testbed sge-up` reaches `ready.sh` green in under 60 s
cold after the image is built; `make sge-down && make sge-up` again in under
20 s; manual `qsub -b y hostname` as alice lands on each node across two
submissions; `qacct -j` sees it within 2 s.

**Phase 2: fixture and runner (1 day).** `tests/unit` move, `conftest.py`,
`cluster.py`, `login` venvs, `make sge-test`/`sge-shell`, `pyproject.toml`
marker and addopts. Acceptance: `make -C testbed sge-test` runs an `echo`
test end to end on this laptop with one command and copies the JUnit XML
out; `pytest` alone still runs only the unit suite in under 10 s; a source
edit is picked up by the next `sge-test` without a rebuild.

**Phase 3: the tests in section 5 (2 days).** Written in the order listed;
each must fail on `main` (the pre-refactor code) where the QA review says it
would, and pass on `refactor/decisions`. Anything that fails on the branch is
a bug report, not a test to weaken.

**Phase 4: CI (half a day).** `ci.yml` integration job: `docker compose
build`, `make sge-test` for 3.12 only on PRs and the full 3.10–3.12 matrix
on `main`. Budget 5 min for the build plus 5 min per cell;
`timeout-minutes: 20`.

**Phase 5: further SGE flavours (one at a time, about a day each).** Each
is `testbed/sge/<flavour>/` with the same four services and the same
`ready.sh`/`clean.sh` contract, so `SGECluster` and the test files are
reused unchanged; `--flavour <name>` selects it. The support matrix in
`testbed/sge/README.md` gets a column per flavour: which tests pass, which
are skipped by marker and why, and which flags or output formats differ.
Candidates, in order: Open Cluster Scheduler (Univa lineage, Apache-2.0,
hpc-gridware's Rocky 9 compose; needs `privileged: true` and a kernel 7.x
`util/arch` patch, both documented upstream), then `daimh/sge` (the
maintained SoGE fork, built from source). Altair Grid Engine is proprietary
and licensed, so it cannot be a container flavour; its differences are
documented from the matrix of the open flavours nearest to it.

**Phase 6: Slurm (when the Slurm backend starts).** `testbed/slurm/debian/` with
Debian packages (`ARG DEBIAN_SUITE=bookworm|trixie` → 22.05 / 24.11),
unprivileged config (`proctrack/linuxproc`, `task/none`, cgroup constraints
off; `CgroupPlugin=disabled` only on 24.11), `slurmdbd` + MariaDB behind an
`accounting` profile, giovtorres-style healthchecks and
`depends_on: service_healthy`. The fixture interface does not change.

## 7. CI details

- No image registry and no separate image workflow (decided 2026-10-29,
  keep it simple). The image is built in the job; the apt step is the slow
  part, a few minutes on a runner. If that ever hurts, the first step up is
  `docker/build-push-action` with `cache-from/to: type=gha`, still no
  registry.
- `ci.yml` `integration` job: `needs: [lint, test]`, `ubuntu-latest`,
  `docker compose build`, `up -d --wait`,
  `pytest tests/integration --require-cluster --cluster sge --flavour soge`, on failure
  `docker compose logs` and `/var/log/testbed.log` uploaded as artefacts,
  `down -v` always.
- `ci.yml` runs on every push and pull request; the `integration` job is
  live (phase 4, done 2026-10-29) and drives the stack through the `testbed/`
  Makefile targets, with a second `sge-up SLOW_ACCOUNTING=1` cycle for the
  slow-accounting test. See `testbed/README.md`, section "CI".

## 8. Open questions for you

1. ~~Which flavour do the real clusters run?~~ **Answered 2026-10-29: the
   Son of Grid Engine lineage, the same code as the Debian trixie packages.**
   That is flavour `soge`. Checked locally against real output
   (never committed, see the policy below): the parsers handle ISO
   `JAT_start_time`, per-task array states and a `QRSH_COMMAND` joined with
   raw `0xff` bytes; `qrsh` returns the job's exit code even through a site
   ssh wrapper; the accounting flush window exists in production; memory is
   requested per slot, confirming `memory_per = "slot"`.

   **Policy: nothing copied, pasted or captured from the real environment
   goes into this repo, in any form.** Site facts stay in the session; the
   container stack is the only source of checked-in fixtures.
2. ~~Host UID~~ **Resolved 2026-10-29 by dropping the host-side mode**
   (section 4). Users are fixed in the image; nothing writable is
   bind-mounted; no UID matching anywhere. `min_uid 0`/`min_gid 0` stay in
   the testbed config so a low GID can never matter.
3. ~~Registry~~ **Resolved 2026-10-29: none. Build the image each time**,
   locally and in CI; Docker's layer cache covers repeat runs.
4. ~~`qrsh -now`~~ **Resolved 2026-10-29: interactive submits always pass
   `-now no`** (there is no use case for dropping the request); no config
   key; `-- -now y` restores fail-fast. Done in code on `refactor/decisions`.
   The testbed test fills both nodes and checks the interactive job queues
   and then completes.
5. ~~TIMEOUT~~ **Resolved 2026-10-29: no inference.** `TIMEOUT` stays
   mapped from `failed 37` only; an execd `h_rt` kill is reported as what
   `qacct` says, `FAILED` with exit 137. Working out why a job failed is the
   user's job, from the job history and logs. Already documented in
   `docs/source/sge.rst`; the test asserts `FAILED`/137 permanently.
6. ~~Scope of phase 5 and Podman~~ **Resolved 2026-10-29.** Docker only;
   Podman is not mentioned again. Only SGE matters now, and the testbed is
   structured as one directory per vendor implementation of a lineage
   (`testbed/sge/<flavour>/`), each tested on its own and never grouped
   with the others, with a support matrix per flavour. `soge` (Debian
   packages) first; more flavours are phase 5, added one at a time.

## 9. Effort

Roughly six working days for phases 0–4, with phase 0 the only one with real
uncertainty (foreground daemons and name resolution). Each further flavour
(phase 5) and Slurm (phase 6) are separate pieces of work.
