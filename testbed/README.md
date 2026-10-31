# Scheduler testbed

Real schedulers in Docker Compose, used by `tests/integration`. Design:
`docs/dev/testbed/TESTBED_PLAN.md`; decisions and deviations: `docs/dev/testbed/TESTBED_REVIEW.md`. Only Docker
(29 locally, 28 on CI) and Compose 2.38 features are used; no registry, the
image is built locally each time (Docker's layer cache makes a rebuild free).

The stack is driven by `hpc-testbed` (`src/hpc_runner/_testbed.py`, installed
with the package; stdlib only, wraps `docker compose` and prints every command
it runs). It needs a checkout: it finds `testbed/<family>/<flavour>/compose.yml`
above the current directory and uses `-p hpc-testbed-<flavour>`.

```
testbed/
  common/             users.sh (alice 1001, bob 1002, tester 1003), wait-for.sh (poll with timeout)
  sge/README.md       SGE flavours and the per-flavour support matrix
  sge/soge/           Debian trixie gridengine 8.1.9 (Son of Grid Engine lineage)
    Dockerfile        one image for every role; role from SGE_ROLE at run time
    compose.yml       master, exec1, exec2, login on a static-IP network
    entrypoint.sh     master: init_cluster + qconf config; exec: sge_execd; login: venvs + sleep
    cell/             qconf inputs: global, sched.conf, complexes.conf, pe-smp.conf,
                      hg-allhosts.conf, q-all.conf, q-short.conf, userset-arusers.conf
    hpc-runner-testbed.toml   HPC_RUNNER_CONFIG for the suite ([schedulers.sge] names match cell/)
    ready.sh / clean.sh       readiness probe / per-test cleanup, both run on login
```

## Running by hand

```bash
hpc-testbed up                    # build, start, wait for ready.sh (--ready-timeout 90, --no-build)
hpc-testbed shell                 # bash in login as tester, /opt/venv/3.12/bin on PATH (--py 3.11 to change)
hpc-testbed pytest                # tests/integration inside login; JUnit XML copied to testbed/junit-<py>.xml
hpc-testbed pytest -- -k submit -x   # everything after -- goes to pytest
hpc-testbed pytest --up [--keep]  # up, pytest, down (--keep leaves the stack running)
hpc-testbed logs [master ...]     # container logs, all or named services
hpc-testbed clean                 # delete every job, wait for an empty queue
hpc-testbed down                  # containers, network and volumes removed
hpc-testbed up --slow-accounting  # master with a 15 s accounting flush (one test only)
hpc-testbed --flavour <name> ...  # another flavour under testbed/<family>/ (default soge)
```

`hpc-testbed pytest` always passes `-m integration --require-cluster`: inside
the testbed an unreachable scheduler is a broken testbed and fails the run.

Inside `login` the suite's environment is already set: `HPC_SCHEDULER=sge`,
`HPC_RUNNER_CONFIG=/src/testbed/sge/soge/hpc-runner-testbed.toml`,
`HPC_SCRIPT_DIR=/work/cache/scripts`, `HPC_EXIT_DIR=/work/cache/exit`. The
repo is mounted read-only at `/src`; each venv has an editable install of it
(done at container start, no rebuild needed for source edits; a dependency
change in `pyproject.toml` needs `sge-up` again, which rebuilds; the venvs
carry the `test` extra only). Users:
`alice`, `bob` (job owners), `tester` (runs the suite, passwordless sudo).
Shared volumes: `/work` (mode 1777: job scripts, output, sentinels),
`/home`, `/var/lib/gridengine` (the cell, so `qacct` works on login);
`/opt/modulefiles` is `tests/modulefiles` read-only on every node.

## Debugging inside login

- `hpc-testbed shell`, then `qstat -f`, `qhost`, `qconf -sq all.q`,
  `qacct -j <id>`, `sudo -u alice qsub ...`. The in-container form of the
  suite is `pytest tests/integration -m integration` (the `-m` overrides
  pyproject's `-m 'not integration'` addopts; add `--pdb`, `-k`, ... as usual).
- `login` is an admin host as well as a submit host (every `qconf -s*` read
  needs one), so root on `login` is an SGE manager: `sudo qdel -u '*'`,
  `sudo qconf ...` work from there.
- Daemon logs: `hpc-testbed logs master` (qmaster prints to
  stdout under `SGE_ND=1`), or inside the containers
  `/var/spool/gridengine/qmaster/messages` and
  `/var/spool/gridengine/execd/<host>/messages`; the entrypoints append to
  `/var/log/testbed.log`.
- A job's script as execd saw it: `/var/spool/gridengine/execd/<host>/job_scripts/<id>`.
- Readiness by hand: `docker exec login /opt/testbed/ready.sh` prints which
  of the four checks failed and the evidence.
- Node control from login (failure tests): `sudo docker stop|start|kill exec1`
  (the host's Docker socket is mounted; the client is Debian's `docker-cli`).
- A bare `qsub` script does not have `module`: a non-interactive bash does
  not source `/etc/profile.d/modules.sh`. hpc-runner's generated scripts
  source it themselves; in a hand-written script add `. /etc/profile.d/modules.sh`.

## CI

The `integration` job in `.github/workflows/ci.yml` runs this stack on
`ubuntu-latest` after `lint` and `test`, with `timeout-minutes: 20` and no
registry (the image is built in the job). Pull requests run Python 3.12 only;
pushes to `main` run 3.10, 3.11 and 3.12, one matrix cell each. A cell is:

1. `pip install -e .` for the driver, `hpc-testbed up`, then `hpc-testbed
   pytest --py <ver> --junit testbed/junit-<ver>-default.xml` (a missing
   cluster is a failure, not a skip).
2. `hpc-testbed down`, `hpc-testbed up --slow-accounting`, then `hpc-testbed
   pytest --py <ver> -- -k slow_accounting` for the one test that needs the
   15 s flush (docs/dev/testbed/TESTBED_REVIEW.md D-T22); its report is
   `testbed/junit-<ver>.xml`.

Artefacts: `junit-<ver>` (both XML reports, always uploaded) and, on failure,
`testbed-logs-<ver>` with `compose.log` (`hpc-testbed logs`) and each
container's `/var/log/testbed.log` as `<name>-testbed.log`. `hpc-testbed down`
runs last whatever happened.

## Adding a scheduler or a flavour

A *flavour* is one vendor implementation of a scheduler family:
`testbed/<family>/<flavour>/` with the same four services (`master`, `exec1`,
`exec2`, `login`), the same `ready.sh`/`clean.sh` contract (exit 0 when ready;
delete all jobs and wait), the same users and volumes, and its own cell
configuration and `hpc-runner-testbed.toml`. The fixture classes
(`tests/integration/cluster.py`) and the tests are shared across flavours of
one family; `--flavour <name>` selects the stack that is up and
`@pytest.mark.flavour(...)` skips what a flavour cannot do, which is what fills
`testbed/sge/README.md`. A new family (Slurm) is `testbed/slurm/<flavour>/`
plus a `SlurmCluster` with the same interface. The driver needs nothing new:
`hpc-testbed --flavour <name>` finds the compose file under any family and
passes the family as `--cluster`.

## Phase 0 spike results (2026-10-29, gridengine 8.1.9+dfsg-13.1 on trixie)

| # | Spike | Result |
|---|---|---|
| 1a | `s-nail` satisfies `mailx` without an MTA | **No**: on trixie `s-nail` no longer `Provides: mailx`; apt picks `bsd-mailx` + exim4 (4.3 MB). Chosen `ssmtp` + `bsd-mailx` (D-T1) |
| 1b | `SGE_ND=1` keeps `sge_qmaster` / `sge_execd` in the foreground | **Yes**: both run as PID 1, exit 0 on SIGTERM (qmaster ~9 s controlled shutdown, execd ~1 s) |
| 1 | `init_cluster` idempotent | **Yes**: skips when `<spooldir>/sge` exists. It writes `settings.sh/csh`, `sge.module`; not `bootstrap` (package postinst) nor `act_qmaster` (entrypoint). `act_qmaster` must be sgeadmin-writable or qmaster exits at once |
| 2 | Compose DNS with `hostname == container_name` | **No**: Docker's embedded DNS reverse-resolves a peer to `exec1.<network>`; commlib refuses every connection (`access denied (client IP resolved to host name "login.<net>". This is not identical to clients host name "login")`). Static IPs + `extra_hosts` work (chosen, D-T2); `ignore_fqdn true` in `bootstrap` also works |
| 3 | `docker compose exec -T login qrsh <script>` | **Yes**: output streams line by line, exit code 7 propagated; `qrsh -now no` queues behind full slots and runs, `-now y` fails after 4 s |
| 4 | `init_cluster` across `down`/`up` on a volume | **Yes** when `/var/spool/gridengine` is a volume too (config, job ids, accounting all survive). Cell-only persistence re-inits and restarts job ids at 1 against the old accounting file (D-T4) |
| 5a | `qacct -j` on login via the shared cell volume | **Yes**; latency table below |
| 5b | default `min_uid 1000` | alice (1001) accepted; uid 500 and root refused (`job rejected: your user id N is lower than minimum user id 1000`) |
| 5c | `qconf -Aq` on an existing queue | errors (`already exists`); `qconf -Mq` replaces; hence `-A* \|\| -M*` (D-T7) |
| 5d | `qstat -xml` root element | `job_info` (namespace `arc.liv.ac.uk/.../qstat.xsd`) |

Accounting visibility (`qsub -b y hostname`, polled every 0.1 s, three runs each):

| scheduler config | `flush_time` | submit → gone from `qstat` | gone → in `qacct` |
|---|---|---|---|
| default (`schedule_interval 0:0:15`) | 00:00:15 | 29–30 s | 0.03 s (written at the same tick, 15 s after `end_time`) |
| default | 00:00:01 | 27–30 s | 0.03 s (unchanged: the delay is the scheduler tick) |
| `cell/sched.conf` (1 s tick, flush on submit/finish) | 00:00:01 | 1.1 s | 0.4–0.9 s |
| `cell/sched.conf` | 00:00:15 | 1.1 s | 10.6–12.9 s |

Other facts the stack relies on: `qconf -Mconf` names the object after the
file's basename (hence `cell/global`); every `qconf -s*` read is refused from a
host that is not an admin host (login is one); `qconf -ah` resolves the name
at add time (hence the offline `spooldefaults adminhosts|submithosts`
pre-registration before qmaster starts, D-T3); execd exits after 5 s and does
not retry if its host is unknown to qmaster; a bash PID 1 doing `qmaster &
wait` never forwards SIGTERM (the entrypoint traps and forwards; master
`stop_grace_period` 30 s).

## Phase 1 acceptance (measured 2026-10-29 on the development laptop)

- Image: 637 MB; full `--no-cache` build 44.5 s; cached rebuild check 1.2 s.
- `hpc-testbed up` after the image is built, fresh volumes: ready.sh
  green in **21.0 s** (master configured 1 s after its start, login's three
  editable installs done at +7 s; the rest is health polling and the
  `qsub -sync y` readiness probe).
- `hpc-testbed down`: **8.4 s** (login 0.3 s under tini, execd 0.9 s, qmaster
  6.9 s controlled shutdown); `hpc-testbed up` again: **19.6 s**. The plan's
  20 s combined target was a guess (docs/dev/testbed/TESTBED_REVIEW.md D-T20); 28 s is the
  recorded number.
- `qsub -b y hostname` as alice twice: exec2 then exec1; `qacct -j` had the
  record 0.98 s and 0.70 s after the job left `qstat`.
- `qrsh -now no hostname` as tester: `exec2` in 1.2 s.
- `module load dummy_tool` in a batch job through hpc-runner
  (`JobSpec(modules=["dummy_tool"], modules_path=["/opt/modulefiles"])`):
  COMPLETED, `DUMMY_TOOL_VERSION=1.0`.
- The suite with `--py` 3.12 / 3.11 / 3.10: the smoke test passes once
  per Python (4.2 s / 3.2 s / 4.1 s); `testbed/junit-<PY>.xml` copied out.
- Node control from login as tester: `sudo docker kill exec1` then
  `sudo docker start exec1`: exec1 back in `qhost` with numeric load 5 s after
  the kill. A `sleep 120` job running on exec1 stays `r` in `qstat` while the
  node is down; when execd restarts it reports the job failed
  (`qacct`: `failed 19`, "shepherd exited with exit status 19: before writing
  exit_status", `exit_status 0`), and the job leaves `qstat`.
