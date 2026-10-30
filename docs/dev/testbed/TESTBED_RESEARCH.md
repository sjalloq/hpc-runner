# Testbed research: prior art and fact checks

Research for `TESTBED_DESIGN.md`. Collected
2026-10-28/29. Every finding carries a URL. "Debian source" links point at the
trixie package `gridengine 8.1.9+dfsg-13.1` on sources.debian.org, which is the
SoGE 8.1.9 code the design proposes to run. Statements marked **(inference)**
are my reading, not something a source says.

---

## 1. Existing containerised SGE / OGS / Debian `gridengine-*` setups

### Summary table

| Project | Last activity | Base / SGE source | Separate master/exec containers | Cell bootstrap | Modules | Licence | Verdict |
|---|---|---|---|---|---|---|---|
| dask-jobqueue `ci/sge` | ci/sge last changed 2024-08-21; repo pushed 2025-12-19 | `ubuntu:14.04`, Ubuntu `gridengine-*` pkgs (6.2u5 era) | Yes: `master` + `slave-one` + `slave-two` | debconf preseed (`gridengineconfig false`), then Debian `init_cluster`, then `qconf -A*/-M*` files | No | BSD-3 | **Copy ideas** (closest prior art; image itself is obsolete) |
| hpc-gridware/quickinstall `containers/Rocky/9` (Open Cluster Scheduler) | 2026-10-20 | Rocky 9, OCS 9.1.7 binary tarballs | Yes: master + 2 workers, static IPs | `inst_sge` auto-install template via `ocs.sh` | No | Apache-2.0 | **Copy ideas**; possible UGE-lineage second target (see 1.2) |
| xenon-middleware/xenon-docker-images `gridengine` | 2019-07-31 | phusion baseimage 16.04, Ubuntu `gridengine-*` | No (single container) | Configured at **build** time with `qconf`, hostname rewritten at start | No | Apache-2.0 | Copy one idea (qping healthcheck); ignore image |
| bgruening/docker-galaxy `test/gridengine` | 2026-01-01 | `ubuntu:22.04` + Debian `spooldefaults.bin` transplant | Single SGE container + Galaxy client container | Package postinst `init_cluster` + runtime `qconf` | No | MIT | Ignore image; documents Ubuntu breakage |
| calypr/funnel `tests/gridengine` | gridengine dir last changed 2020-11-23 | `centos:6`, EPEL gridengine 6.2u5 | No | `inst_sge -x -m -auto` | No | MIT | Ignore |
| michaelmayer2/ge-docker-cluster | 2022-09-01 | `centos:7`, loveshack COPR SoGE RPMs | Yes | `inst_sge` template | No | none | Ignore |
| wtakase/docker-sge, gawbul/docker-sge, dgruber/gridengine-docker, kgutwin/simple-sge, stevekm/docker-centos6-sge, manabuishii/docker-sge-* | 2016-2020 | CentOS 6 / phusion; SoGE 8.1.8 from source RPM, or izpack | mixed | `inst_sge` | No | mixed | Ignore (dead) |
| pygridtools/gridmap and drmaa-python `travis/install_sge.sh` | 2023 / 2019 | Travis Ubuntu host, no container | n/a (single host) | debconf preseed `gridengineconfig true`, `qconf -A*` templates | No | — | Copy the template files idea only |

No candidate handles Environment Modules. Searches for the named authors
(`ilveroluca`, `Nielsmitchell`, `aspremont`, `pablo-arantes`, `jpgorman`,
`BioContainers`) found no SGE container repos (GitHub user repo listings checked
2026-10-28; `Nielsmitchell` does not exist). Snakemake, Nextflow and Toil have no
SGE container; Toil only has a Slurm compose
([toil contrib/slurm-test](https://github.com/DataBiosphere/toil/blob/master/contrib/slurm-test/docker-compose.yml)).

### 1.1 dask-jobqueue `ci/sge` (closest prior art), details

Source: <https://github.com/dask/dask-jobqueue/tree/main/ci/sge>

- **Images:** one multi-stage [Dockerfile](https://github.com/dask/dask-jobqueue/blob/main/ci/sge/Dockerfile)
  `FROM ubuntu:14.04`, targets `master` and `slave`, conda env baked in. Published
  as `daskdev/dask-jobqueue:sge` and `:sge-slave`; Docker Hub tags were last
  updated 2025-07-15 ([tags](https://hub.docker.com/r/daskdev/dask-jobqueue/tags)),
  although [build-docker-images.yaml](https://github.com/dask/dask-jobqueue/blob/main/.github/workflows/build-docker-images.yaml)
  builds daily.
- **Install:** [setup-master.sh](https://github.com/dask/dask-jobqueue/blob/main/ci/sge/setup-master.sh)
  preseeds debconf for `gridengine-master`, `gridengine-common`, `gridengine-client`
  with `shared/gridenginemaster string $HOSTNAME`, `shared/gridenginecell string default`,
  `shared/gridengineconfig boolean false`, plus postfix `No configuration`, then
  runs `sudo -u sgeadmin /usr/share/gridengine/scripts/init_cluster /var/lib/gridengine default /var/spool/gridengine/spooldb sgeadmin`.
  [setup-slave.sh](https://github.com/dask/dask-jobqueue/blob/main/ci/sge/setup-slave.sh)
  preseeds the same keys with `MASTER_HOSTNAME=sge_master` and writes
  `act_qmaster` by hand.
- **Compose:** [docker-compose.yml](https://github.com/dask/dask-jobqueue/blob/main/ci/sge/docker-compose.yml)
  sets `container_name` equal to `hostname` (`sge_master`, `slave_one`, `slave_two`),
  shares `userhome:/root` and `shared_space:/shared_space` volumes, bind mounts the repo.
- **Daemon start:** via the Debian init scripts (`service gridengine-master restart`,
  `service gridengine-exec restart`), followed by `sleep infinity`; not foreground
  ([run-master.sh](https://github.com/dask/dask-jobqueue/blob/main/ci/sge/run-master.sh),
  [run-slave.sh](https://github.com/dask/dask-jobqueue/blob/main/ci/sge/run-slave.sh)).
- **Configuration at run time:** `qconf -Msconf scheduler.txt`, `-Ahgrp hosts.txt`,
  `-Aq queue.txt`, `-ah` each exec host, `-as $HOSTNAME`, and
  [add_worker.sh](https://github.com/dask/dask-jobqueue/blob/main/ci/sge/add_worker.sh)
  (`qconf -Ae`, `-aattr hostgroup`, `qmod -e`, `complex_values h_vmem=100G`,
  per-host slots). Then the master is restarted.
- **Scheduler tuning** ([scheduler.txt](https://github.com/dask/dask-jobqueue/blob/main/ci/sge/scheduler.txt)):
  `schedule_interval 0:0:1`, `flush_submit_sec 0`, `flush_finish_sec 0`.
- **Readiness** ([start-sge.sh](https://github.com/dask/dask-jobqueue/blob/main/ci/sge/start-sge.sh)):
  poll `docker exec sge_master qhost | grep lx26-amd64 | wc -l` until it equals 2, cap 300 s.
  Note the master side also spins on `qconf -sconf` because "Sometimes conf is inaccessible at first".
- **Tests run in-container**: `docker exec sge_master conda run ... pytest ... -E sge`
  ([sge.sh](https://github.com/dask/dask-jobqueue/blob/main/ci/sge.sh)); daemon logs
  are dumped from `/tmp/sge*` and `/tmp/exec*` after the run.
- **CI:** [ci.yaml](https://github.com/dask/dask-jobqueue/blob/main/.github/workflows/ci.yaml)
  runs `ubuntu-latest`, matrix `htcondor, pbs, sge, slurm, none`, `timeout-minutes: 30`,
  builds images in the job (`docker compose build`) rather than pulling.

### 1.2 Open Cluster Scheduler (OCS) and its compose stack

- OCS is an actively developed open-source continuation of the **Univa** Grid
  Engine open core by HPC-Gridware ([README](https://github.com/hpc-gridware/clusterscheduler/blob/master/README.md));
  latest release tags V914 (2026-07-27) etc. ([releases](https://github.com/hpc-gridware/clusterscheduler/releases)),
  quickinstall default `OCS_VERSION=9.1.7` ([ocs.sh](https://github.com/hpc-gridware/quickinstall/blob/main/ocs.sh)).
  Repo carries both SISSL v1.2 and Apache 2.0 licence files.
  Free binaries: "no login required", hosted at open.clusterscheduler.io from 9.1.6
  ([download page](https://www.hpc-gridware.com/download-main/)).
- The [quickinstall Rocky 9 compose](https://github.com/hpc-gridware/quickinstall/tree/main/containers/Rocky/9)
  is a working 1 master + 2 worker stack: static IPs on `10.100.0.0/16`, every node
  appends all three to `/etc/hosts`, shared install volume `/opt/ocs`, shared
  `/home/gridware`, MUNGE key volume, `privileged: true`. Master installs with
  `inst_sge` from a template; workers wait for `settings.sh` to appear then run
  `sgeexecd start`. README advises checking `qstat -f`, not just `qhost`, because
  "A node without a queue instance still shows up there while being unable to run anything".
- **Kernel gotcha found there:** the startup scripts patch `/opt/ocs/util/arch` because
  "Upstream only whitelists kernels up to 6.*; newer kernels (e.g. 7.x ...) are reported
  as UNSUPPORTED-* and daemon startup fails"
  ([startup-master.sh](https://github.com/hpc-gridware/quickinstall/blob/main/containers/Rocky/9/startup-master.sh)).
  SoGE 8.1.9's `util/arch` accepts `2.4.*|2.6.*|[3-9].*` so it is **not** affected
  ([Debian source util/arch](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/source/dist/util/arch/)).
  Relevant: this host runs Linux 7.2 and GitHub's `ubuntu-24.04` image runs kernel 7.0
  ([runner image readme](https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2404-Readme.md)).
- Related: [go-clusterscheduler](https://github.com/hpc-gridware/go-clusterscheduler)
  ships a single-node OCS dev container and a "simulator" that dumps a real cluster's
  config and replays it in a container; [slurm-shim](https://github.com/hpc-gridware/slurm-shim)
  provides `sbatch`/`squeue` etc. on top of OCS.
- **Assessment (inference):** OCS output (`qstat -xml`, `qacct`) descends from Univa,
  so an OCS stack is the only freely available stand-in for UGE/AGE-style output, which
  the design otherwise plans to cover with captured fixtures.

### 1.3 Other SoGE lineage: `daimh/sge` ("Some Grid Engine")

- <https://github.com/daimh/sge>: fork of SoGE 8.1.9, actively maintained (commits
  2026-10-24 "fix: address const warnings with glibc 2.43"), CMake build, musl support,
  systemd job submission, OpenSSL updates, weekly test runs on Debian 12/13, Ubuntu
  22.04/24.04/26.04, Rocky/Alma 8-10, Arch, Alpine, Void
  ([README](https://github.com/daimh/sge/blob/master/README.md)). Only tags are `8.1.9`
  and `qmon`; "Version numbers now correspond to the commit hash". Tests are VM/ISO
  based, not containers ([tests/](https://github.com/daimh/sge/tree/master/tests)).
  Quick-start uses `yes "" | ./install_qmaster` then `./install_execd`.
- Upstream SoGE site arc.liv.ac.uk refused HTTPS and timed out on HTTP from here on
  2026-10-28. Wikipedia lists SoGE as an "inactive free fork"
  ([Oracle Grid Engine](https://en.wikipedia.org/wiki/Oracle_Grid_Engine)); SoGE NEWS
  ends at 8.1.9 ([Debian source NEWS](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/NEWS/)).
- Debian is considering moving its package to a live fork: bug
  [#1121046 "have gridengine track the next active fork"](https://bugs.debian.org/1121046)
  (2025-11-19, tagged help) names daimh/sge as the direct successor and OCS as an
  alternative; no decision.

### 1.4 Small reusable ideas from the rest

- **xenon:** `HEALTHCHECK` with `qping -info $(hostname) 6444 qmaster 1` and
  `qping -info $(hostname) 6445 execd 1`
  ([Dockerfile](https://github.com/xenon-middleware/xenon-docker-images/blob/master/gridengine/Dockerfile));
  and the counter-example of configuring at build time, which forces a hostname rewrite
  plus `qconf -ds/-dh/-de` of the build hostname on every start
  ([gridengine.sh](https://github.com/xenon-middleware/xenon-docker-images/blob/master/gridengine/etc/my_init.d/gridengine.sh)).
  It also flips `ignore_fqdn` to `true` in `bootstrap`.
- **Galaxy:** Ubuntu's `gridengine-master` postinst segfaults in `spooldefaults.bin`
  (Launchpad [#1774302](https://bugs.launchpad.net/ubuntu/+source/gridengine/+bug/1774302),
  jemalloc/TLS; Debian's -9+ is fixed); Galaxy's 2026 image works around it by copying
  `spooldefaults.bin` and `libspool*.so` from the Debian `.deb`
  ([Dockerfile](https://github.com/bgruening/docker-galaxy/blob/main/test/gridengine/Dockerfile)).
  It also shows the "add the client container to the master's `/etc/hosts`, restart
  master, `qconf -as client`" dance needed when a submit host joins late
  ([test.sh](https://github.com/bgruening/docker-galaxy/blob/main/test/gridengine/test.sh)).
- **gridmap/drmaa-python:** host/queue/user templates fed to `qconf -A*`
  ([install_sge.sh](https://github.com/pygridtools/gridmap/blob/master/travis/install_sge.sh)).
- **wtakase:** carried a `skip_cl_com_compare_hosts.patch` to work around hostname
  comparison failures under Kubernetes DNS
  ([base/Dockerfile](https://github.com/wtakase/docker-sge/blob/master/base/Dockerfile)).

---

## 2. Debian `gridengine-*` packages

### 2.1 Versions and availability

From the Debian archive (madison, 2026-10-28,
[api.ftp-master.debian.org/madison?package=gridengine-master](https://api.ftp-master.debian.org/madison?package=gridengine-master&text=on)):

| Suite | `gridengine-master/exec/client/qmon` | `gridengine-common` |
|---|---|---|
| bookworm (oldstable) | 8.1.9+dfsg-10+b1 | 8.1.9+dfsg-10 |
| trixie (stable) | **8.1.9+dfsg-13.1** | 8.1.9+dfsg-13.1 |
| forky (testing) | **absent** | absent |
| sid | 8.1.9+dfsg-14 | 8.1.9+dfsg-14 |

All five packages the design names exist in trixie. Binary package list:
gridengine-common, -client, -qmon, -master, -exec, -dev, -drmaa1.0, libdrmaa1.0-java(-doc),
gridengine-drmaa-dev, libdrmaa1.0-ruby ([debian/control](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/debian/control/)).

- **gridengine is not in testing.** It was removed from testing on 2025-11-02 and
  8.1.9+dfsg-14 is blocked by RC bug
  [#1039213](https://bugs.debian.org/1039213) ("ships sysv-init script without systemd
  unit", raised to serious 2025-10-15; patch submitted 2026-09-11, not applied)
  ([tracker](https://tracker.debian.org/pkg/gridengine)). Trixie is fine for now;
  the next Debian release may not carry it.
- Ubuntu: noble 8.1.9+dfsg-11build3, resolute (26.04) 8.1.9+dfsg-13.2
  ([Launchpad API](https://api.launchpad.net/1.0/ubuntu/+archive/primary?ws.op=getPublishedSources&source_name=gridengine&exact_match=true&status=Published)).
  Ubuntu's master postinst segfault (#1774302 above) is the reason to stay on Debian.

### 2.2 What the packaging does at install time

- **debconf questions** (templates are owned by both `gridengine-common` and
  `gridengine-master`; all three are `shared/`):
  `shared/gridengineconfig` (boolean, default **true**, "Configure SGE automatically?"),
  `shared/gridenginecell` (string, default `default`),
  `shared/gridenginemaster` (string, no default)
  ([common.templates](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/debian/gridengine-common.templates/),
  [master.config](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/debian/gridengine-master.config/)).
- **gridengine-common postinst** creates system user/group `sgeadmin` (home
  `/var/lib/gridengine`, only if absent), creates `/var/lib/gridengine/<cell>/common`,
  installs `/usr/share/gridengine/default-bootstrap` as `.../common/bootstrap`, and
  writes `/etc/default/gridengine` via ucf
  ([common.postinst](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/debian/gridengine-common.postinst/)).
- **gridengine-master postinst** creates `/var/spool/gridengine/{qmaster,qmaster/job_scripts,spooldb}`
  and, **only if `gridengineconfig` is true**, runs
  `init_cluster /var/lib/gridengine <cell> /var/spool/gridengine/spooldb sgeadmin` as sgeadmin
  ([master.postinst](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/debian/gridengine-master.postinst/)).
  At `docker build` time that captures the build container's hostname, which is why
  dask preseeds `false` and runs `init_cluster` itself.
- **gridengine-exec postinst** creates `/var/spool/gridengine/execd` and, if config is
  true, writes `act_qmaster` from `shared/gridenginemaster` **only if the file does not
  already exist** ([exec.postinst](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/debian/gridengine-exec.postinst/)).
- **`init_cluster`** ([script](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/debian/scripts/init_cluster/)):
  skips if `<spooldir>/sge` already exists (idempotent on a persistent volume); runs
  `spoolinit berkeleydb libspoolb <spooldir> init`, `spooldefaults configuration`
  from `/usr/share/gridengine/default-configuration`, complexes, usersets, managers;
  writes `settings.sh`, `settings.csh` and an `sge.module` modulefile into `.../common/`.
- **Defaults that matter** ([default-bootstrap](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/debian/default-bootstrap/),
  [default-configuration](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/debian/default-configuration/)):
  `admin_user sgeadmin`, `ignore_fqdn false`, `default_domain none`,
  `spooling_method berkeleydb`, `qmaster_spool_dir /var/spool/gridengine/qmaster`,
  `execd_spool_dir /var/spool/gridengine/execd`, `min_uid 1000`, `min_gid 1000`,
  `reporting_params accounting=true reporting=false flush_time=00:00:15 joblog=false sharelog=00:00:00`,
  `gid_range 65400-65500`, `qlogin/rsh/rlogin_* builtin`, `finished_jobs 100`,
  `login_shells bash,sh,ksh,csh,tcsh`.
- **Dependencies pulled in:** gridengine-common depends on `bsd-mailx | mailx`, `cpp`,
  `ucf`, `tcsh | c-shell`; exec depends on client, `tcsh | c-shell`, `procps`,
  `lsb-base`; master on client, `procps`, `lsb-base`
  ([control](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/debian/control/)).
  `bsd-mailx` depends on `default-mta` ([packages.debian.org](https://packages.debian.org/trixie/bsd-mailx)),
  so an MTA is pulled unless another `mailx` provider is installed (dask preseeds postfix
  and then disables it). `cpp` is needed because `util/arch` runs `/usr/bin/cpp -dM`
  to detect x32/armhf ([util/arch](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/source/dist/util/arch/)).

### 2.3 Non-interactive preseed (pattern from prior art)

dask's approach, verbatim keys ([setup-master.sh](https://github.com/dask/dask-jobqueue/blob/main/ci/sge/setup-master.sh)):

```
echo "gridengine-common shared/gridengineconfig boolean false" | debconf-set-selections
echo "gridengine-common shared/gridenginecell   string  default" | debconf-set-selections
echo "gridengine-common shared/gridenginemaster string  <master>" | debconf-set-selections
# repeated with owner gridengine-master and gridengine-client
DEBIAN_FRONTEND=noninteractive apt-get install -y gridengine-master gridengine-client
```

### 2.4 Layout: where SGE_ROOT ends up

- `SGE_ROOT=/var/lib/gridengine`, `SGE_CELL=default`, `SGE_ARCH` from `util/arch`
  ([gridengine.default](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/debian/gridengine.default/)).
- Real binaries live in `/usr/lib/gridengine/`; `/usr/bin/q*` and `/usr/sbin/sge_*` are
  symlinks to `/usr/share/gridengine/gridengine-wrapper`, which sets SGE_ROOT/CELL/ARCH
  from env, then `/etc/default/gridengine`, then defaults, and `exec`s
  `/usr/lib/gridengine/<prog>` ([wrapper](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/debian/gridengine-wrapper/),
  [client.links](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/debian/gridengine-client.links/),
  [master.links](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/debian/gridengine-master.links/),
  [exec.links](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/debian/gridengine-exec.links/)).
- Accounting file: `$SGE_ROOT/$SGE_CELL/common/accounting`, i.e.
  `/var/lib/gridengine/default/common/accounting`, written by qmaster
  ([qacct.1 FILES](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/doc/man/man1/qacct.1/)).
  `qacct` on a login/exec container only sees it if `.../common` is shared.

### 2.5 Running daemons in the foreground

- Setting **`SGE_ND`** in the environment stops daemonisation: sge_qmaster(8) "If set,
  don't daemonize the program (for debugging)"
  ([sge_qmaster.8](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/doc/man/man8/sge_qmaster.8/)).
  In code: `sge_daemonize_qmaster` returns early on `getenv("SGE_ND")`
  ([sge_qmaster_threads.c L181](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/source/daemons/qmaster/sge_qmaster_threads.c/#L181));
  execd checks it too ([execd.c L217, L288](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/source/daemons/execd/execd.c/#L217)).
  So `SGE_ND=1 exec /usr/sbin/sge_qmaster` / `sge_execd` is the foreground form.
- Every prior-art stack instead used the init scripts or `sgeexecd start` plus
  `sleep infinity`/`tail -f messages` (dask, xenon "GE can not start in the foreground
  so use the rc scripts", Galaxy, OCS). **(inference)** The xenon comment is wrong for
  8.1.9; nobody in the prior art exercised `SGE_ND`, so verify it early.
- The Debian init script exits unless `SGE_START_MASTERD=yes`, sources
  `/lib/init/vars.sh` and `/lib/lsb/init-functions`, and uses
  `start-stop-daemon` with PID file `/var/run/gridengine/qmaster.pid`
  ([master.init](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/debian/gridengine-master.init/)).
- Logs: `/var/spool/gridengine/qmaster/messages` (Galaxy tails it,
  [master_script.sh](https://github.com/bgruening/docker-galaxy/blob/main/test/gridengine/master_script.sh));
  execd `messages` under `/var/spool/gridengine/execd/<host>/`.

### 2.6 Known gotchas

- **Hostname resolution.** SGE requires consistent name resolution across hosts and
  warns against mapping the hostname to 127.0.0.1
  ([Altair troubleshooting PDF](https://help.altair.com/AltairGridEngine/TroubleShootingQuickReferenceGE.pdf)).
  Wikimedia hit qmaster writing `localhost` into `act_qmaster`, then
  `HOST_NOT_RESOLVABLE` for clients ([T100554](https://phabricator.wikimedia.org/T100554)).
  `host_aliases` (`$SGE_ROOT/$SGE_CELL/common/host_aliases`, first alias is the unique
  name) is the escape hatch ([host_aliases.5](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/doc/man/man5/host_aliases.5/)).
  Prior art handles Compose by making `container_name == hostname` (dask) or static
  IPs plus explicit `/etc/hosts` lines (OCS). **(inference, unverified)** Docker's
  embedded DNS answers reverse lookups for other containers with a name that need not
  equal their `hostname:`; that is the likely reason both patterns exist. Test it.
- **`act_qmaster`**: exec postinst writes it only if absent; xenon and dask overwrite
  it at start. `bootstrap` must agree on `admin_user` / spool paths between master and
  exec when `.../common` is not shared.
- **Root vs sgeadmin**: daemons start as root and drop to `admin_user` (sgeadmin);
  `qrsh` jobs "can also only be run if the sge_execd is running under the root account"
  ([submit.1](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/doc/man/man1/submit.1/)).
- **`min_uid`/`min_gid 1000`** in Debian's default configuration: jobs from UIDs below
  1000 (root, system users) are refused ([sge_conf.5 min_uid](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/doc/man/man5/sge_conf.5/)).
- **`gid_range`** 65400-65500 by default; with `ENABLE_ADDGRP_KILL` (default on) the
  supplementary gid is used to find and kill job processes (sge_conf.5).
- **`ignore_fqdn false`** by default; xenon flips it to true.
- **Debian `testing` removal** and the sysv-only init (above).

---

## 3. Slurm in Docker

### 3.1 Candidates

| Project | Last activity | Slurm | Base | Containers | slurmdbd | Privileged | Licence | Verdict |
|---|---|---|---|---|---|---|---|---|
| [giovtorres/slurm-docker-cluster](https://github.com/giovtorres/slurm-docker-cluster) | release 3.0.2 2026-10-19 | **26.05.5, 25.11.8 only** (built from source RPMs) | Rocky 9 | mysql, slurmdbd, slurmctld, slurmrestd, c1, c2 (+GPU, OOD, ES/Kibana optional) | Yes (MySQL) | `privileged: true` | MIT | Copy ideas; not usable for 22.05/24.11 |
| dask-jobqueue [ci/slurm](https://github.com/dask/dask-jobqueue/tree/main/ci/slurm) | 2024-08 | 21.08.6 from source | Rocky 8 | mysql 5.7, slurmdbd, slurmctld, c1, c2 | Yes | No (`cap_add: NET_ADMIN`) | BSD-3 (vendored from giovtorres) | Copy readiness pattern |
| [nathan-hess/docker-slurm](https://github.com/nathan-hess/docker-slurm) | 2026-04-21 | Ubuntu `slurm-wlm` (22.04 → 21.08) | Ubuntu | **single** container | No | No | MIT | Copy unprivileged slurm.conf |
| [SciDAS/slurm-in-docker](https://github.com/SciDAS/slurm-in-docker) | 2019-10 | 19.05.1 | CentOS 7 | multi + Lmod | optional | — | MIT | Ignore |
| [rancavil/slurm-cluster](https://github.com/rancavil/slurm-cluster) | 2024-04 | — | — | multi + Jupyter | — | — | none | Ignore |
| [SlinkyProject/containers](https://github.com/SlinkyProject/containers) (SchedMD official) | 2026-10-20 | 25.11, 26.05 | Rocky 9, Ubuntu 24.04/26.04 | per-daemon OCI images for Kubernetes (Slinky); no compose | — | — | Apache-2.0 | Reference only |
| [koesterlab/setup-slurm-action](https://github.com/koesterlab/setup-slurm-action) (used by [snakemake-executor-plugin-slurm CI](https://github.com/snakemake/snakemake-executor-plugin-slurm/blob/main/.github/workflows/ci.yml)) | 2025-07 | runner's apt Slurm via galaxyproject/ansible-slurm | GHA host, no container | all-in-one + slurmdbd | Yes | n/a | — | Alternative CI approach, not a testbed |

giovtorres details: compose healthchecks (`scontrol ping` on slurmctld, `pidof slurmdbd`,
`pidof slurmd`), `depends_on: condition: service_healthy`, `init: true`, dynamic node
registration `slurmd -Z`, Lmod 9.1.2 and Spack baked in, slurm.conf uses
`ProctrackType=proctrack/linuxproc`, `TaskPlugin=task/affinity`, cgroup.conf with all
`Constrain*=no` and `IgnoreSystemd=yes`
([.env.example](https://github.com/giovtorres/slurm-docker-cluster/blob/main/.env.example),
[docker-compose.yml](https://github.com/giovtorres/slurm-docker-cluster/blob/main/docker-compose.yml),
[config/26.05/slurm.conf](https://github.com/giovtorres/slurm-docker-cluster/blob/main/config/26.05/slurm.conf),
[cgroup.conf](https://github.com/giovtorres/slurm-docker-cluster/blob/main/config/common/cgroup.conf)).
Its CI ([test.yml](https://github.com/giovtorres/slurm-docker-cluster/blob/main/.github/workflows/test.yml))
builds Slurm from source and runs the cluster tests in about 8-11 minutes per matrix cell
(run of 2026-10-19 on `ubuntu-latest` and `ubuntu-24.04-arm`, from the Actions API).

dask Slurm readiness ([start-slurm.sh](https://github.com/dask/dask-jobqueue/blob/main/ci/slurm/start-slurm.sh),
[register_cluster.sh](https://github.com/dask/dask-jobqueue/blob/main/ci/slurm/register_cluster.sh),
[docker-entrypoint.sh](https://github.com/dask/dask-jobqueue/blob/main/ci/slurm/docker-entrypoint.sh)):
entrypoint waits for MySQL (`SELECT 1`), slurmctld waits on `/dev/tcp/slurmdbd/6819`,
slurmd on `/dev/tcp/slurmctld/6817`; daemons run `-Dvvv` under `gosu`; host loop
retries `sacctmgr --immediate add cluster name=linux` then restarts slurmdbd/slurmctld.

nathan-hess unprivileged config ([slurm.conf](https://github.com/nathan-hess/docker-slurm/blob/main/dockerfile_base/slurm.conf)):
`ProctrackType=proctrack/linuxproc`, `TaskPlugin=task/none`,
`JobAcctGatherType=jobacct_gather/none`, `AccountingStorageType=accounting_storage/none`,
started via `service munge/slurmd/slurmctld start`.

### 3.2 Debian / Ubuntu `slurm-wlm` versions

From madison ([slurm-wlm](https://api.ftp-master.debian.org/madison?package=slurm-wlm&text=on)):
bookworm **22.05.8-4+deb12u3**, trixie **24.11.5-4** (24.11.5-4+deb13u1 in
proposed-updates), forky/sid 26.05.5-1. Binary packages include `slurmctld`, `slurmd`,
`slurmdbd`, `slurm-client`, `slurmrestd`, `slurm-wlm-basic-plugins`,
`slurm-wlm-mysql-plugin`, `sackd` (trixie)
([control 24.11.5-4](https://sources.debian.org/src/slurm-wlm/24.11.5-4/debian/control/)).
Ubuntu noble 23.11.4-1.2ubuntu5, resolute 25.11.2-1ubuntu2
([Launchpad API](https://api.launchpad.net/1.0/ubuntu/+archive/primary?ws.op=getPublishedSources&source_name=slurm-wlm&exact_match=true&status=Published)).

`slurm-smd`: **not verified** in this pass (SchedMD's own in-tree Debian packaging);
no source checked, so treat as open.

### 3.3 cgroups in unprivileged containers

- `CgroupPlugin` accepts `disabled|cgroup/v1|cgroup/v2|autodetect` in 24.11
  ("disabled, to completely disable any interaction with the cgroups"), but in
  **22.05 only `cgroup/v1|cgroup/v2|autodetect`** exist
  ([cgroup.conf.5 @ 24.11.5](https://github.com/SchedMD/slurm/blob/slurm-24-11-5-1/doc/man/man5/cgroup.conf.5),
  [@ 22.05.8](https://github.com/SchedMD/slurm/blob/slurm-22-05-8-1/doc/man/man5/cgroup.conf.5),
  [current docs](https://slurm.schedmd.com/cgroup.conf.html)).
- `IgnoreSystemd` is "Only for cgroup/v2 and for development and testing" (same page).
- Unprivileged prior art uses `proctrack/linuxproc` + `task/none` (nathan-hess,
  dask). Privileged prior art (giovtorres) keeps `task/affinity` and cgroup.conf with
  constraints off. I found no SchedMD page dedicated to running Slurm daemons inside
  Docker.

### 3.4 `--json` history

- `sacct`, `squeue`, `sinfo`: "Add --json and --yaml arguments" in **21.08**
  ([NEWS @ slurm-21-08-0-1](https://github.com/SchedMD/slurm/blob/slurm-21-08-0-1/NEWS)).
- 23.02: "New data_parser interface with v0.0.39 plugin"; partial `--json` for
  sacctmgr, sdiag, sinfo, squeue, scontrol
  ([RELEASE_NOTES @ 23.02](https://github.com/SchedMD/slurm/blob/slurm-23-02-0-1/RELEASE_NOTES)).
- 23.11: "All commands supporting '--yaml' and '--json' arguments will now use the
  data_parser/v0.0.40 plugin ... by default"
  ([RELEASE_NOTES @ 23.11](https://github.com/SchedMD/slurm/blob/slurm-23-11-0-1/RELEASE_NOTES)).
  **(inference)** 23.11 is the point the CLI JSON schema became versioned and stable.
- 24.05.0/24.05.1: `squeue --json` aborts; fixed in 24.05.2
  ([slurm-users thread](https://groups.google.com/g/slurm-users/c/E_i1Zcn565A)).
- 24.11: data_parser v0.0.42 with `prefer_refs` default
  ([RELEASE_NOTES @ 24.11](https://github.com/SchedMD/slurm/blob/slurm-24-11-0-1/RELEASE_NOTES)).
- Debian bookworm 22.05 is built with `libjson-c-dev`
  ([control](https://sources.debian.org/src/slurm-wlm/22.05.8-4+deb12u3/debian/control/))
  and `slurm-wlm-basic-plugins` ships `serializer_json.so`
  ([bookworm filelist](https://packages.debian.org/bookworm/amd64/slurm-wlm-basic-plugins/filelist));
  trixie also ships `data_parser_v0_0_40/41/42.so`
  ([trixie filelist](https://packages.debian.org/trixie/amd64/slurm-wlm-basic-plugins/filelist)).
  So `squeue --json` **exists** on bookworm, in the pre-data_parser format.

---

## 4. Environment Modules (brief)

- Debian package `environment-modules`: bookworm 5.2.0-1, **trixie 5.5.0-1**, forky 5.7.0-1
  ([madison](https://api.ftp-master.debian.org/madison?package=environment-modules&text=on)).
- Trixie file list includes `/etc/profile.d/modules.sh`, `/usr/share/modules/init/bash`
  (lowercase `modules`), `/usr/lib/x86_64-linux-gnu/modulecmd.tcl`,
  `/etc/environment-modules/modulespath`, `/etc/environment-modules/initrc`
  ([filelist](https://packages.debian.org/trixie/amd64/environment-modules/filelist)).
  hpc-runner's templates try `/etc/profile.d/modules.sh` then
  `/usr/share/Modules/init/bash` (RHEL capitalisation)
  (`src/hpc_runner/schedulers/sge/templates/batch.sh.j2` L32-35); on Debian the first
  branch is the one that matches.
- `MODULES_VERBOSITY`: "Added in version 4.3"; levels silent, concise, normal, verbose,
  verbose2, trace, debug, debug2 ([module(1)](https://modules.readthedocs.io/en/latest/module.html)).
- Lmod: Debian bookworm 8.6.19-1, trixie 8.7.60-1
  ([madison](https://api.ftp-master.debian.org/madison?package=lmod&text=on)); Environment
  Modules documents Lmod Tcl modulefile compatibility (same page), and giovtorres ships
  Lmod 9.1.2. Reasonable alternative, but the target is Environment Modules.
- SGE's `init_cluster` itself writes an `sge.module` modulefile into
  `$SGE_ROOT/$SGE_CELL/common/` ([init_cluster](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/debian/scripts/init_cluster/)).

---

## 5. pytest + docker compose patterns

- **pytest-docker** 3.2.6 (2026-10-16, MIT, [avast/pytest-docker](https://github.com/avast/pytest-docker)):
  session fixtures `docker_compose_file` (list allowed), `docker_compose_project_name`,
  `docker_setup` (default includes `up --build --wait`, which honours compose
  `healthcheck`), `docker_cleanup`, `docker_services.wait_until_responsive(check=..., timeout=..., pause=...)`.
  README shows pinning project name and `["down -v", "up --build --wait"]` to avoid
  stray stacks when debugging ([README](https://github.com/avast/pytest-docker/blob/master/README.md)).
- **testcontainers-python** 4.15.0 (2026-07-24): `testcontainers.compose.DockerCompose`
  dataclass with `context`, `compose_file_name` (str or list), `pull`, `build`,
  `wait=True`, `keep_volumes`, `env_file`, `services`, `profiles`; methods `start`,
  `stop(down=True)`, `get_logs`, `get_container`, `exec_in_container`,
  `waiting_for(strategies)`
  ([compose.py](https://github.com/testcontainers/testcontainers-python/blob/main/src/testcontainers/compose/compose.py)).
- Others: pytest-docker-tools 3.1.10, pytest-docker-compose-v2 0.3.0 (PyPI).
- **Scheduler-integration prior art all uses plain shell + `docker exec`**, with tests
  running *inside* the cluster container: dask-jobqueue (SGE, Slurm, PBS, HTCondor;
  section 1.1 and 3.1), Galaxy (SGE), Toil (Slurm compose with a munge-key
  healthcheck, [compose](https://github.com/DataBiosphere/toil/blob/master/contrib/slurm-test/docker-compose.yml)).
  Snakemake's Slurm plugin installs Slurm directly on the runner instead
  (setup-slurm-action). None of them use pytest-docker or testcontainers.
- Readiness probes seen: count `qhost` lines (dask SGE), `qping -info` (xenon),
  `qstat -f` queue instances (OCS README), `scontrol ping` healthcheck (giovtorres),
  `sacctmgr add cluster` retry (dask Slurm), `/dev/tcp/host/port` waits (dask Slurm).

---

## 6. GitHub Actions

- `ubuntu-latest` = Ubuntu 24.04, image 20261026: kernel 7.0.0-1017-azure, Docker
  client/server 28.0.4, Docker Compose 2.38.2, Buildx 0.38.1, Podman 4.9.3
  ([Ubuntu2404-Readme](https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2404-Readme.md),
  [label table](https://github.com/actions/runner-images/blob/main/README.md)).
- Standard Linux runners: public repos 4 CPU / 16 GB / 14 GB SSD; private 2 CPU / 8 GB /
  14 GB; passwordless sudo; `ubuntu-slim` is container-based, unprivileged, no
  Docker-in-Docker ([GitHub-hosted runners](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)).
  Arm64 `ubuntu-24.04-arm` has the same specs; giovtorres runs its matrix there too.
- `privileged: true` containers work on standard runners (giovtorres CI runs its
  privileged compose on `ubuntu-latest`, [test.yml](https://github.com/giovtorres/slurm-docker-cluster/blob/main/.github/workflows/test.yml));
  OCS compose also uses it. SGE prior art (dask) needs no privileges.
- Caching: the GHA BuildKit cache backend (`cache-from/to: type=gha`) needs a
  non-default builder (`docker/setup-buildx-action`) and a distinct `scope` per image
  or builds overwrite each other's cache; registry cache `type=registry,ref=...,mode=max`
  is the alternative when pushing to GHCR anyway
  ([Docker docs: GHA cache](https://docs.docker.com/build/ci/github-actions/cache/),
  [gha backend](https://docs.docker.com/build/cache/backends/gha/)).
- Time budgets observed: giovtorres 8-11 min per cell **including** building Slurm from
  source; dask-jobqueue and giovtorres both set `timeout-minutes: 30`.
- Hostname: every prior-art compose sets `hostname:` explicitly; dask also sets
  `container_name` to the same value and OCS uses fixed IPs (section 2.6).

---

## 7. CLI fact checks (SoGE 8.1.9)

| Claim / question | Finding | Source |
|---|---|---|
| `-o`/`-e` path variables | `$HOME`, `$USER`, `$JOB_ID`, `$JOB_NAME`, `$HOSTNAME`, `$TASK_ID`; `~` also accepted; "note the lack of an SGE_ prefix" | [submit.1 (-e section)](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/doc/man/man1/submit.1/) |
| `$TASK_ID` in `-o` | Works; expanded by execd `expand_path`. For non-array jobs execd passes task id `0`, so **(inference from code)** it expands to `0`, not "undefined" (that string is the `SGE_TASK_ID` env value) | [get_path.c L226](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/source/daemons/execd/get_path.c/#L226), [exec_job.c L1038-1051](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/source/daemons/execd/exec_job.c/#L1038) |
| `-o` with `qrsh` | "currently silently ignored by qsh, qrsh, and qlogin" | submit.1 `-o` |
| Exit 100 | Job/prolog/epilog exit 100 puts the job in **error state** (not requeue) unless `FORBID_APPERROR` is set; exit **99** requeues unless `FORBID_RESCHEDULE`. Accounting `failed` = 30 "application error returned" | [sge_shepherd.8](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/doc/man/man8/sge_shepherd.8/), [sge_conf.5 FORBID_*](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/doc/man/man5/sge_conf.5/), [sge_status.5](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/doc/man/man5/sge_status.5/) |
| `-hold_jid` semantics | Dependent not eligible "unless all jobs referenced ... have completed"; completion, not success, so afterany. "If any of the referenced jobs exits with exit code 100, the submitted job will remain ineligible". Name references only to the same user's jobs, resolved at submit time | submit.1 `-hold_jid` |
| `-hold_jid_ad` | Task-to-task array dependency; both must be array jobs with the same task range (else rejected); stride mismatches give many-to-one; exit 100 in a referenced task keeps dependents held | submit.1 `-hold_jid_ad` |
| `qacct -j` before flush | Prints `error: job id N not found` and exits **1** | [qacct.c L901-930, L1169](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/source/clients/qacct/qacct.c/#L901), [msg_history.h](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/source/common/msg_history.h/) |
| Flush timing | `reporting_params` changes "take immediate effect"; `flush_time` default 15 s, format HH:MM:SS **or seconds**; separate **`accounting_flush_time`**, "Setting it to 0 disables buffering"; defaults to `flush_time` if unset. Syntax is `key=value` inside `reporting_params` | [sge_conf.5 reporting_params](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/doc/man/man5/sge_conf.5/), Debian [default-configuration](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/debian/default-configuration/) |
| `schedule_interval`, `flush_submit_sec`, `flush_finish_sec` | `schedule_interval` is the event-to-scheduler interval, 0 disables scheduling; `flush_*_sec` ≥1 triggers a run that many seconds after submit/finish, 0 disables | [sched_conf.5](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/doc/man/man5/sched_conf.5/) |
| `qrsh` running a script without a tty | `-pty` default: qrsh **with a command** starts without a pty. qrsh defaults to `-now y`: "tries to start the job immediately or not at all", returns 1 if not scheduled; only INTERACTIVE queues unless `-now no` | submit.1 `-pty`, `-now`, qrsh description |
| `qrsh` exit code | With builtin IJS (Debian default `qlogin_*/rsh_* builtin`), qrsh exits "with real exit_status" of the job | [qsh.c L2275-2286](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/source/clients/qsh/qsh.c/#L2275) |
| `-V` / `-v` | `-V` exports all qsub env vars; `-v` adds listed vars; both valid for qsub and **qrsh with a command**. The man text "options -cwd, -v, -V, and -display only apply to batch jobs" is narrower than the code: qsh.c strips `-cwd/-wd/-v/-V/-display` only for qlogin and **qrsh without a command** | submit.1 `-V`, `-v`; [qsh.c L2410-2420](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/source/clients/qsh/qsh.c/#L2410) |
| `h_rt` enforcement | Implemented by SGE (execd), **SIGKILL** on `h_rt`; `s_rt` sends SIGUSR1 first then kills after `notify` | [queue_conf.5 RESOURCE LIMITS](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/doc/man/man5/queue_conf.5/), [execd_ck_to_do.c L477-483](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/source/daemons/execd/execd_ck_to_do.c/#L477) |
| `h_vmem` enforcement | **Both**: shepherd sets `RLIMIT_AS` (no `RLIMIT_VMEM` on Linux) to h_vmem, and execd polls usage and sends SIGKILL if exceeded. Under the rlimit, allocations fail first, so the job may exit non-zero on its own rather than be killed **(inference)** | [setrlimits.c L405-412](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/source/daemons/shepherd/setrlimits.c/#L405), [execd_ck_to_do.c L282-290](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/source/daemons/execd/execd_ck_to_do.c/#L282), queue_conf.5 |
| What accounting shows for a limit kill | `exit_status` = 128 + signal; `failed` = 100 "assumedly after job ... killed by a signal (perhaps due to exceeding resources)"; `failed` = 37 only when **qmaster** enforced h_rt/h_cpu/h_vmem | [accounting.5](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/doc/man/man5/accounting.5/), sge_status.5 |
| `qstat -xml`, `qstat -j -xml` | `-xml` "can be used with all other options"; schemas under `$SGE_ROOT/util/resources/schemas/qstat` | [qstat.1](https://sources.debian.org/src/gridengine/8.1.9+dfsg-13.1/doc/man/man1/qstat.1/) |
| `qsub -sync y` (readiness probe) | Waits for completion, exits with the job's exit code, 1 on failure | submit.1 `-sync` |

---

## Implications for TESTBED_DESIGN.md

**Confirmed**

- Debian trixie `gridengine-*` = 8.1.9+dfsg-13.1, all five named packages exist (§2.1).
  Debian over Ubuntu is right: Ubuntu's master postinst segfault is still open (§1.4).
- trixie `slurm-wlm` 24.11.5, bookworm 22.05.8, noble 23.11.4 (§3.2).
- `h_rt` is enforced by execd with SIGKILL, no cgroups needed (§7).
- qmaster buffers accounting with a 15 s default flush; the `qacct` window is real, and
  `qacct -j` exits 1 with "job id N not found" inside it (§7).
- `-hold_jid` is afterany, with the exit-100 exception (§7).
- `qrsh <script>` needs no tty (§7).
- Separate master/exec containers, shared home/work volumes, plain `docker compose` +
  `docker exec`: this is exactly what the closest prior art (dask-jobqueue) does (§1.1, §5).

**Contradicted or needs correction**

- "SGE has exactly one open lineage (SoGE 8.1.9)": two live forks exist, daimh/sge
  (SoGE continuation, weekly-tested on trixie) and Open Cluster Scheduler (Univa
  lineage, monthly releases) (§1.2, §1.3). Debian may follow one of them (#1121046).
- "bookworm gives 22.05 ... `squeue --json` absent": `--json` arrived in 21.08 and
  bookworm ships the JSON serializer; what 22.05 lacks is the data_parser schema
  (23.02+, default 23.11) (§3.4). Also 22.05 has no `CgroupPlugin=disabled` (§3.3).
- "`h_vmem` is enforced with setrlimit": it is setrlimit (`RLIMIT_AS`) **and** an execd
  SIGKILL; the observed failure may be an allocation error, not a kill (§7).
- Exit code 100 does not requeue; it sets the job to error state (99 requeues) (§7).
- `reporting_params ... flush_time 00:00:01` should be `key=value` syntax, and
  `accounting_flush_time=0` exists to disable accounting buffering entirely (§7).
- "TIMEOUT once implemented": accounting does not mark an execd `h_rt` kill as a
  timeout. It shows `failed 100` + `exit_status 137`, same as any signal death;
  `failed 37` appears only for qmaster enforcement (§7). Distinguishing TIMEOUT needs
  another signal (e.g. `ru_wallclock` vs requested `h_rt`).
- `read_stdout`/`-o` on the interactive path: `qrsh` ignores `-o`/`-e` (§7).
- `qrsh` defaults to `-now y`: with two busy exec nodes it fails immediately rather than
  queueing; queues need `qtype ... INTERACTIVE` and execd must run as root (§7, §2.6).
- Ports/versions: runner Docker is 28.0.4 / Compose 2.38.2, not the local 29 / 5.6 (§6).
- Debian's default `min_uid 1000`/`min_gid 1000` rejects jobs from UIDs < 1000, which
  affects the "test user at host UID" plan if the host UID is below 1000 (§2.2).
- `gridengine` is not in Debian testing (forky); trixie is the last suite that has it
  today (§2.1).

**Gaps the design does not mention**

- Debconf preseed must set `shared/gridengineconfig false` (or the postinst runs
  `init_cluster` at build time against the build hostname); run `init_cluster` at
  container start; it is idempotent (§2.2).
- `SGE_ND=1` gives foreground daemons; no prior-art stack uses it, so verify (§2.5).
- `qacct` on the login node needs `/var/lib/gridengine/default/common` shared (it holds
  `accounting`, `act_qmaster`, `bootstrap`) (§2.4). The design's `sge_cell` volume covers it.
- Hostname/reverse-DNS consistency is the most-cited failure in prior art; copy dask's
  `container_name == hostname` or OCS's static IPs + `/etc/hosts` (§2.6).
- `cpp` and an MTA come in as dependencies (§2.2).
- `$TASK_ID` in `-o` for a non-array job expands to `0` (code reading, verify) (§7).

**Reusable artefacts**

- dask-jobqueue `ci/sge`: debconf keys, `init_cluster` call, `qconf -A*` file set
  (scheduler, hostgroup, queue), `add_worker.sh`, `qhost`-count readiness. BSD-3.
- Debian's own `init_cluster` and `default-configuration` as the cell template.
- xenon `qping -info` healthcheck lines.
- OCS quickinstall Rocky 9 compose as a ready UGE-lineage second target if fixtures
  from a real UGE/AGE site are not obtainable (§1.2).
- giovtorres compose healthcheck / `depends_on: service_healthy` pattern for the
  later Slurm stack; nathan-hess slurm.conf for an unprivileged Debian-package build.
- pytest-docker `docker_setup = ["down -v", "up --build --wait"]` if a library is ever
  wanted; prior art shows it is not needed.
