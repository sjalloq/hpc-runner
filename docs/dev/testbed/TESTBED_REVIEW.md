# Testbed build: review points — 2026-10-29 (autonomous)

Record of every place this session diverged from `TESTBED_PLAN.md`, or had to
choose where the plan was silent. Sub-agents made no decisions: each question
came back to the main session and is logged here with the choice made and
why. Each item needs a yes/no or a correction.

## Plan corrections (typos and facts, no judgement involved)

- **Relative mount paths in section 2.3** are one level short:
  `testbed/sge/soge/compose.yml` must mount `../../../tests/modulefiles` and
  `../../..:/src`, not `../../tests/modulefiles` and `../..`.

## Decisions

<!-- one bullet per decision, newest last -->

### Phase 0 spikes (results in `testbed/README.md`; package gridengine 8.1.9+dfsg-13.1 on trixie)

- **D-T1 mailx provider.** Plan: `s-nail` satisfies `mailx`. Spike: on trixie
  `s-nail` no longer `Provides: mailx`, so apt pulls `bsd-mailx` + exim4.
  Chosen: `ssmtp` + `bsd-mailx` (132 kB, no daemon, no extra libs). Rejected:
  `mailutils` (no MTA but ~6 MB and ~15 libs), postfix preseed (4 MB), exim4
  (4.3 MB). Nothing in the testbed sends mail; the provider only has to exist.
  Fallback if `ssmtp` disappears from the suite: `dma`.
- **D-T2 name resolution.** Plan: rely on Compose DNS with
  `hostname == container_name`. Spike: fails outright (reverse lookup returns
  `exec1.<network>`, commlib refuses every connection). Chosen: the plan's own
  fallback, a user-defined network with static IPs plus `extra_hosts` on every
  service, Debian's `bootstrap` untouched (`ignore_fqdn false`). Also verified
  and rejected: `ignore_fqdn true` in `bootstrap` (one sed, works, but leaves
  the ordering race below in place because names resolve only while a
  container runs).
- **D-T3 execd start race.** Spike: execd exits after 5 s if its host is not
  yet an admin host, and does not retry; a dead container's name then stops
  resolving under Compose DNS, so the master's `qconf -ah` retry loop can
  spin forever. Chosen: on first initialisation the master pre-registers
  `exec1`, `exec2` (admin hosts) and `login` (submit host) offline with
  `spooldefaults adminhosts|submithosts` before `sge_qmaster` starts
  (verified in the spike), and the master healthcheck passes only after a
  marker file says the post-start `qconf` configuration step completed, so
  `depends_on: service_healthy` orders exec/login after the fully configured
  master. The post-start step still runs idempotent `qconf -ah/-as` as a
  belt-and-braces. Rejected: a restart loop around execd (hides real
  failures), a marker file alone (still a race on first boot).
- **D-T4 spool persistence.** Plan lists only `cell:/var/lib/gridengine`.
  Spike: the BDB spool and `jobseqnum` live in `/var/spool/gridengine`; with
  the cell volume alone a `down`/`up` re-initialises the cluster and restarts
  job ids at 1 while the old accounting file still holds them (`qacct`
  collisions). Chosen: a `spool` named volume on the master at
  `/var/spool/gridengine` (verified: config, queues, ids and accounting all
  survive `down`/`up`). `make sge-down` keeps running `down -v` as planned.
- **D-T5 master entrypoint shape and stop grace.** Spike: qmaster needs ~9 s
  to shut down; a bash PID 1 doing `sge_qmaster & wait` never forwards
  SIGTERM, so `compose down` hits the 10 s grace and SIGKILLs it. Chosen:
  the master entrypoint traps TERM/INT and forwards to the qmaster pid, then
  `wait`s on it (plan step 5 kept, with the trap added); `stop_grace_period:
  30s` on master. execd and login `exec` their process directly.
- **D-T6 slow-accounting window.** Spike: the 15 s latency comes from
  `schedule_interval`, not `flush_time`; with the plan's fast `sched.conf`
  and `flush_time=00:00:15` the window is a variable 10–13 s. Chosen: keep
  the plan's profile (only `flush_time` changes) and make the sentinel test
  qualitative: the sentinel answers before `qacct` has the record, and
  `qacct` has it within 30 s. Rejected: also restoring `schedule_interval`
  15 s for the profile (every other wait in that run would slow to 15–30 s).
- **D-T7 config file names and idempotency.** `qconf -Mconf` takes the object
  name from the file's basename, so `cell/global.conf` becomes `cell/global`
  (the one exception to the `.conf` suffix; a comment in the entrypoint says
  why). Objects added with `-A*` use `qconf -A<x> file || qconf -M<x> file`:
  a fresh object is added, an existing one is replaced, so a config edit is
  picked up on the next `up` with a persisted spool.
- **D-T8 small confirmations.** `@allhosts` is created with `-Ahgrp` before
  any queue references it (init_cluster does not create it); `/work` is mode
  1777 with a per-test subdirectory. `python3` is installed on every node
  (the plan already said so; the spike image lacked it).
- **D-T9 node control from `login`.** Plan: install `docker.io` (client only)
  and `docker-compose-plugin` in the image. Neither is a clean apt install
  on trixie (`docker.io` pulls the daemon; the compose plugin is not in
  Debian). Chosen: the Debian `docker-cli` package if it exists, else the
  static client tarball from download.docker.com pinned to one version; no
  compose plugin, because `container_name == service` lets the fixture run
  plain `docker stop|start|kill <name>`. The mounted socket is owned by the
  host's docker GID, which the image cannot know, so `tester` gets
  passwordless sudo (already needed for `sudo -u alice`) and node control
  runs `sudo docker ...`. This is a throwaway test container, as the plan
  says of the socket mount itself.

### Phase 2 fixture (`tests/integration/cluster.py`, `conftest.py`)

- **D-T10 one session `cluster`, per-test `workdir`.** The plan's `Cluster`
  has a per-test `workdir` attribute but is otherwise session-wide. Chosen:
  one session object; the function-scoped `workdir` fixture creates
  `/work/<test>-<hex>` and assigns it to `cluster.workdir` for that test (an
  autouse cleanup depends on it, so every test gets a fresh one). The
  alternative, a function-scoped `cluster` over a session readiness
  fixture, was not taken; it is a small change if mutation offends.
- **D-T11 `wait_for` fails fast on a wrong terminal state.** Plan silent.
  Waiting for COMPLETED on a job that already FAILED raises at once with
  the `qstat -f`/`qstat -j` dump instead of burning the 60 s timeout.
  UNKNOWN is not terminal, so the "aged out" test can still wait through it.
- **D-T12 `--flavour` must agree with the stack.** The container exports
  `HPC_RUNNER_CONFIG` for the flavour it was built from; the fixture fails
  (not skips) when `--flavour` points at a different toml.
- **D-T13 cleanup failure is a teardown error**, not a warning: a `clean.sh`
  that cannot empty the queue means later tests are not isolated.
- `run()` adds `sudo -u` for any user but `tester`; node control and
  accounting truncation call `sudo` directly. A missing `ready.sh` is
  treated as "no cluster" at once; only a present-but-failing one is polled
  for 90 s. The unit suite's autouse `bash -n` check on rendered scripts
  also applies to integration tests (harmless, flagged).

### Phase 1 stack (`testbed/sge/soge/`)

- **D-T14 `login` is an admin host**, not only a submit host as plan 2.2 says:
  every `qconf -s*` read is refused from a non-admin host, and `ready.sh`
  step 3 (`qconf -sel`, `-ss`) runs on `login`. Side effect: root on `login`
  is a manager, which `clean.sh` uses for `qdel -u '*'`.
- **D-T15 slow accounting is an env var, not a Compose profile.** A profile
  can only add or remove services, not change one service's environment,
  and a second `master` would clash on name and IP. `make sge-up
  SLOW_ACCOUNTING=1` sets `ACCOUNTING_FLUSH=00:00:15` on master; the
  entrypoint substitutes it into a temp copy of `cell/global`.
- **D-T16 `login` healthcheck waits for the venv installs** (marker file)
  as well as `qstat -f`, so `up --wait` returns only when tests can run.
- **D-T17 `login` runs under `init: true`** (tini as PID 1) so `sleep
  infinity` receives SIGTERM and `sge-down` does not wait out the 10 s
  grace on it.
- **D-T18 venvs carry a `test` extra, not `dev`.** Plan 4.1 says "test
  extras"; the only extra that existed was `dev` (sphinx, mypy, twine,
  pre-commit ...) and three copies made the image 1.14 GB against the plan's
  300 MB. Added `test = [pytest, pytest-cov, pytest-asyncio]` to
  `pyproject.toml`, with `dev` including it; the image installs `test` plus
  `hatchling`, `hatch-vcs`, `editables` for the offline editable install.
- **D-T19 `make sge-test` passes `-m integration`** because `pyproject`'s
  `addopts` deselects the marker; the explicit `-m` overrides it. The
  README documents `pytest tests/integration -m integration` as the form.
- **D-T20 timing targets.** Measured: `sge-up` cold volumes 18.5 s (target
  60 s, met); `sge-down && sge-up` 39.6 s (target 20 s, missed; ~10 s is
  D-T17, the rest is health polling and the `qsub -sync y` readiness
  probe). Chosen: keep the probe (ready means a job ran), tighten the
  healthcheck `interval`/`start_interval` to 1 s, accept whatever remains
  and record it in the README. The target was a guess, not a requirement.
- **Recorded, no action:** the image builds deps from a stub project
  (`pyproject.toml` copied, empty package) with `SETUPTOOLS_SCM_PRETEND_VERSION`,
  and `login` installs `/src` editable offline with `HATCH_BUILD_NO_HOOKS=true`
  so hatch-vcs does not try to write into the read-only mount; a
  `Dockerfile.dockerignore` limits the repo-root build context; 8.1.9 ships
  no default PE, so `pe-smp.conf` is written from scratch; the idempotent
  `-A || -M` step prints "already exists" lines on the master log; on this
  laptop the user-defined network has no outbound DNS (tailscale resolver),
  which does not matter because start-up is fully offline. JUnit output
  `testbed/junit-*.xml` is git-ignored.

### Phase 3 tests (`tests/integration/test_*.py`)

- **D-T21 no "fails on `main`" check.** Plan phase 3 says each test must
  fail on `main` where the QA review predicted. The tests use `JobSpec`,
  `resolve` and `JobStatus.SKIPPED`, none of which exist on `main`, so every
  test would fail there at import, which proves nothing. Dropped; the QA
  review's bug ids are cited in each test's docstring instead.
- **D-T22 slow-accounting test detects its stack.** The one test that needs
  `SLOW_ACCOUNTING=1` reads `qconf -sconf` for `flush_time=00:00:15` and
  skips with that reason on the default stack; CI (phase 4) runs it in a
  second `sge-up SLOW_ACCOUNTING=1` cycle with `-k slow_accounting`.
- **D-T23 "shared FS missing" test not automated.** It needs a compose
  override that leaves `/work` off the exec nodes, which cannot be applied
  from inside `login` (no compose plugin, and the volume is shared by every
  container, so it cannot be hidden on one node from inside). The symptom
  (job fails, `read_stdout()` is None) is documented in `testbed/sge/README.md`
  as a manual check instead of a test.
- **D-T24 node-killed test asserts what the spike saw.** `docker kill exec1`
  then `start` within seconds: execd reports the job on re-registration,
  `qacct` records `failed 19`, `exit_status 0`, no rerun (`rerun FALSE`).
  The test asserts a terminal FAILED-family status with no exception and
  documents the `failed 19` code; what hpc-runner maps it to is read from
  `sge/parser.py`, not invented.

### Phase 4 CI (`.github/workflows/ci.yml` `integration` job)

- **D-T25 matrix by event.** Full 3.10–3.12 only on pushes to `main`;
  pull requests, feature-branch pushes and tags run 3.12 only. Alternative
  would be keying on `pull_request` alone, which triples cost for every
  branch push that is also a PR.
- **D-T26 two stack cycles per cell**: the default stack, then
  `SLOW_ACCOUNTING=1` with `-k slow_accounting` (D-T22). The first run's
  JUnit file is renamed `junit-<PY>-default.xml`; the second keeps the
  Makefile's name. If the first run fails the slow cycle is skipped and
  logs plus the XML are still uploaded. `fail-fast: false` so one
  version's failure does not cancel the others. Logs are collected on
  `failure()` only (not on cancellation or timeout). Not run: GitHub
  Actions cannot be executed locally; the YAML parses and the step
  commands are the Makefile targets already exercised by hand.
- **D-T27 "rejected resource" row rewritten.** The plan's `-l gpu=2` against
  `complex_values gpu=1` is *accepted* by qsub and queues forever; SGE
  rejects at submit only unknown resources or queues, and checks
  satisfiability only with `qsub -w e`. The test now uses `-l nosuch=1`
  and documents the gpu case. **Open for you:** should hpc-runner pass
  `-w e` (or `-w w`) so an unsatisfiable request fails at submit instead of
  queueing forever? Policy, not taken here.
  **Decision (2026-10-31): no.** Submit-time verification only works when
  the site's complex configuration matches what `-w e` checks, which it
  rarely does; the same idea on Slurm relied on optional features that
  were not enabled and broke submissions. A request that cannot be met
  queues, which is what the scheduler is for.
- **D-T28 `qrsh -now no` schedules into BATCH-only queues.** Observed: with
  every `all.q` (BATCH INTERACTIVE) slot busy, the interactive job ran at
  once in `short.q` (BATCH only, `h_rt` 1 min). SGE treats a `-now no`
  qrsh like a batch job for queue selection. The "interactive, no slot"
  test therefore fills all eight slots of both queues. **Open for you:** since
  806897f every interactive job can land in a non-INTERACTIVE queue; on a
  site whose batch queues lack X forwarding or have short limits that may
  matter. Options would be a config `queue` for interactive jobs or passing
  `-q` with the INTERACTIVE queues; not taken here.
  **Decision (2026-10-31): nothing to do.** "Interactive" means `qrsh`
  instead of `qsub`, nothing more. Queues and consumables are the user's
  to specify, through config or passthrough, and a job that does not
  dispatch for lack of resources is behaving correctly. hpc-runner does
  not second-guess queue selection.
- **D-T29 accounting truncation must wait for the record.** `wait_for`
  returns via the exit sentinel before qmaster flushes the accounting line
  (1 s), so the aged-out test first polls `qacct -j` until the record
  exists, then truncates. Any future test that truncates must do the same.
- Also: `get_statuses` batching is observed by wrapping the subprocess
  seam (`tests.fakes.SEAM`, `wraps=` the real function), so real commands
  still run; `Cluster.exit_dir` was added so no test imports
  `SGEScheduler`; no test needed the `flavour` marker so far.

### Phase 3 findings: bugs the testbed found (not fixed here, pinned as strict xfails)

Product code was not changed in this session; these are for you to triage.
Each has an integration test marked `xfail(strict=True)` naming the id, so
the suite is green now and the fix flips the test to a hard pass.

- **BUG-T1 workdir with a space breaks submission.** `#$ -o <workdir>/x.out`
  is split on whitespace by SGE's directive parser, so `qsub` fails with
  "Unknown option". `SGEScheduler.render_value` joins the path unquoted.
  SGE directive lines may not support quoting at all; the fix may be to
  pass `-o`/`-e` (and `-wd`) on the qsub argv instead. `test_cwd_with_space`.
- **BUG-T2 `list_completed_jobs(user=...)` returns a phantom record.** *Fixed
  2026-10-31 (trace work, D-TR12): `parse_qacct_records` keeps only blocks
  with a `jobnumber`.* `qacct -o <user> -j` ends with a per-owner summary
  block (header, rule, one row per owner whose wallclock total is non-zero)
  that `parse_qacct_records` parsed as a job (`job_id=''`, `user='unknown'`,
  UNKNOWN). The unfiltered form has no such block. Found live by the first
  `hpc trace compare` run: the capture's bob jobs ran in 0 s and had no
  row, so the fixture never held it; bob now sleeps 2 s.
  `test_completed_listing_by_user`.
- **BUG-T3 `failed 19` with `exit_status 0` is reported COMPLETED.** *Fixed
  2026-10-31 (test QA fix pass): `qacct_status` reads the full `failed`
  table first; `JobInfo` carries `failed_code`/`failed_reason`.* A job
  whose node died is recorded by qacct as `failed 19: before writing
  exit_status`, `exit_status 0`; `qacct_status` special-cases only
  `failed 37` and otherwise trusts `exit_status`, so a lost job looks
  successful. Any non-zero `failed` other than 37 is not a success.
  `test_node_killed`.
- **BUG-T4 `-h` cannot be passed through to qsub.** The CLI group's
  `help_option_names = ["-h", "--help"]` is inherited by `hpc run`, so
  `hpc run -h -- cmd` prints help instead of forwarding qsub's hold flag;
  CLAUDE.md lists only `-t` and `-b` as claimed. No test; the listing test
  uses running sleeps instead of held jobs.

### Phase 3 decisions, second batch

- **D-T30 `h_rt` kill asserts TIMEOUT, exit 137, `failed 37`.** The plan
  row expected FAILED with `failed 100` from the research's claim that
  execd kills are recorded that way. On soge 8.1.9 the *qmaster* enforces
  `h_rt` and records `failed 37`, which is exactly the one code your
  decision maps to TIMEOUT, so hpc-runner is doing what was decided. The
  parser docstring's "execd kill is failed 100" sentence is a research
  claim this stack does not reproduce; left for you to keep or cut.
- **D-T31 other users submit through the CLI.** "alice and bob submit"
  runs `sudo -u <user> env HPC_*=... <venv>/bin/hpc run --batch ...` from
  the test (the venv of the running interpreter, so each Python cell tests
  its own CLI); the Python API cannot change the submitting user.
- **D-T32 `queue selection`** asserts `JobInfo.queue == "short.q"` and
  `node` starts with `exec`, since the parser splits `queue@host` by design.
- **D-T33 `env order`** writes `$$HOME` to get a literal `$HOME` into the
  job: `$VAR` in env values is D5 expansion on the submit side, so B4's
  hostile values are about shell quoting after expansion.
- **D-T34 "regardless of submitter"** replaces the test process's
  `os.environ` with a module-loaded shell's environment (via monkeypatch),
  because `qsub -V` reads the process environment, not `Host.env`.
- **D-T35 fixture additions**: `docker_available` on the protocol,
  `start_node(name)`, `wait_ready()` (polls `ready.sh`), `hpc_cli` path.
- **D-T36 the Makefile is replaced by `hpc-testbed`** (test QA review,
  2026-10-31, §5 "Testbed driver"). `src/hpc_runner/_testbed.py` is a
  stdlib-only entry point with `up`, `down`, `pytest`, `shell`, `logs`,
  `clean`, `--flavour`, `--py`, `--slow-accounting` and `pytest --up
  [--keep]`; it drives `docker compose` by subprocess (testcontainers was
  not worth a dependency for six commands) and prints every command.
  `pytest` always passes `--require-cluster`. CI installs the package for
  the driver and no longer renames the first JUnit report (`--junit`).
  The inside-out design is unchanged: tests still run in `login`.
- **D-T37 no conditional skips** (test QA review §5 item 4). The Docker
  check in `test_failures.py` and the `flush_time` check in
  `test_slow_accounting_window` now fail with the `hpc-testbed` command
  that fixes the stack; the slow variant is a `slow_accounting` marker
  selected by `--slow-accounting` (pytest option, passed by
  `hpc-testbed pytest --slow-accounting`) and deselected otherwise, so the
  default cycle never sees it. `--flavour` and `@pytest.mark.flavour`
  names are validated against `testbed/*/*/compose.yml` at collection;
  a flavour mismatch deselects rather than skips. `-r sxX` is in addopts.
  This supersedes the skip wording of D-T22 and D-T9.
- **D-T38 unit fixtures are captured from the testbed** (test QA review §5
  item 5b). `hpc-testbed capture` runs `tests/integration/capture.py` in
  `login`: it submits one job per scenario (ok, exit 1, finished array,
  another user's job, a `qdel`'d running job, a job on a killed node, then a
  live snapshot with a running job, a held job, an `Eqw` job and an array
  with running and queued tasks, then `qstat` against a stopped master) and
  copies the raw CLI output to `tests/unit/fixtures/sge/` with a
  `manifest.json` of scenario ids. `mock_sge_commands` and the parser tests
  read those files; ids are never hard-coded. Found while capturing: a job
  whose `-o` directory it cannot write goes `Eqw` with `failed 26`; `qstat
  -j <unknown>` exits 0 with `<unknown_jobs>`; `qdel` says "has registered
  the job N for deletion"; against a stopped master qstat fails after ~45 s
  with a commlib "No route to host" line before "unable to contact qmaster".
- **D-T39 testbed additions** (test QA review §5 items 10, 11, 13).
  `h_vmem` is now a consumable (`complexes.conf`) with `complex_values
  h_vmem=8G` on every all.q host, and `hpc-runner-testbed-consumable.toml`
  is a second config layer selecting it with `memory_per = "slot"`;
  `test_memory_per_consumable` reads `qstat -F h_vmem` back: 2 slots x 1G
  leaves 6G ("slot"), 2 slots x 2G leaves 4G ("job" over-books), which is
  the sge.rst claim verified. `[tools.sort] commands = ["sort", "uniq"]`
  with `queue = "short.q"` proves the alias through `hpc run` and `hpc
  config resolve`. `test_cli.py` runs the real `hpc status`, `--history`
  and `<id>` (`--json`); for that, `get_job_details` of a job qstat no
  longer knows now answers from qacct, so `hpc status <id>` of a finished
  job shows exit code, failed code and reason. `test_goldens.py` runs
  `qsub -w v` on every SGE batch golden: a directive the parser rejects
  says "Unable to read script file" (what `-t 1-10:2%3` produced) and
  fails any golden; two goldens verify, two get the site rejection they
  must (4-slot PE on 2-slot hosts; unknown resource `scratch`). Seen on
  the way: a consumable with no `complex_values` anywhere (`xcelium_lic`)
  is "unknown resource" to every queue instance, so `-l xcelium_lic=1`
  is accepted by qsub but can never be scheduled here. Two bugs the new
  tests found: (1) with `h_vmem` consumable and default 0, every job that
  did not request memory ran under a 10 MB address-space limit (`ulimit
  -v` 10240) and bash aborted with exit 134 in the dependency guard; with
  a 2G default, as sites set, such jobs book 2G per slot and run under the
  queue's own limit (`ulimit -v` unlimited here); (2) `hpc status`, `hpc
  kill`, the local scheduler and the TUI read `$USER` and fell back to the
  string "unknown", which under `docker exec` (no `USER`) matched nobody's
  jobs; they now share `core.host.current_user()` with a `getpass`
  fallback, as `Host.capture` already did.
- Suite time: about 4 min per Python on the default stack; the `h_rt`
  (61 s) and node-kill tests are inherent (the master-stop test, 95 s, was
  removed by D-T40).
- **D-T40 no live stopped-qmaster test** (user, 2026-10-31 evening).
  `test_master_stopped` was removed. What it protected (BUG-T6: an
  unreachable qmaster raises `SchedulerUnavailableError`, never an empty
  listing or UNKNOWN) is pinned by the `master_down` capture scenario and
  the unit test over `qstat_master_down.json`, and a flavour whose qstat
  words the error differently changes the fixture. What only the live test
  added was commlib's give-up time against our 60 s command timeout
  (observed 43.5 s, 45 s and twice over 60 s on the same stack: ARP and
  retry schedule, not our code) and "status works again after restart",
  which exercises Docker and SGE, not hpc-runner (each qstat is fresh).
  Jobs keep running on the execds while the master is down; what stops is
  answers, and the message for that is covered. `test_node_killed` stays:
  a lost node is a real mid-job event and `failed 19` is our path.
- **BUG-T5 signal deaths report `exit_code None`.** *Fixed 2026-10-31:
  `get_exit_code` uses `_leading_int`.* qacct writes
  `exit_status 137 (Killed)`; `SGEScheduler.get_exit_code` does
  `int(record["exit_status"])`, which raises, falls through to the exit
  sentinel, and a SIGKILLed job never wrote one. `qacct_status` already
  uses `_leading_int` on the same field; `get_exit_code` does not. Affects
  every `h_rt`/`h_vmem` kill and `qdel` of a running job, in the API and
  `hpc status`. Pinned by `test_signal_exit_code` (a qdel'd sleep, 3 s,
  rather than a second 60 s `h_rt` job); `test_h_rt_kill` keeps TIMEOUT
  and `failed 37`.
- **BUG-T6 an unreachable qmaster is reported as UNKNOWN, not an error.**
  *Fixed 2026-10-31: `_qstat_xml` raises `SchedulerUnavailableError` when
  qstat's stderr names qmaster/commlib/a connection failure. Pinned by the
  unit test over the captured `qstat_master_down.json`; the live test was
  removed the same day (D-T40).*
  `SGEScheduler._active_states` runs `qstat -xml -u '*'` with `check=False`
  and treats any non-zero exit as "nothing active" (to tolerate sites whose
  qstat exits 1 on an empty queue). With the master stopped, qstat fails at
  once with `error: unable to contact qmaster using port 6444`, the lookup
  falls through to `qacct` (which reads the accounting file and needs no
  master) and the sentinel, and `get_status` returns UNKNOWN. A down
  scheduler is thus indistinguishable from an aged-out job, and `wait()`
  keeps polling it quietly until `unknown_after`. The plan expected
  `SchedulerError`. A fix would inspect qstat's stderr (contact/commlib
  errors raise; "no jobs" stays empty). Pinned by `test_master_stopped`
  with a **non-strict** xfail: against the stopped master qstat sometimes
  fails at once (UNKNOWN, the bug) and sometimes hangs into the 60 s
  command timeout (`SchedulerTimeoutError`, which passes); seen both ways
  on this laptop. Make the marker strict, or remove it, when BUG-T6 is fixed.
