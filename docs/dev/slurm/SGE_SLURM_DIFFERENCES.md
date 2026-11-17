# SGE and Slurm: how they actually behave

A factual comparison for designing a Slurm backend. It records behaviour and
where it comes from; where hpc-runner faces a choice, the section ends with a
neutral "consequence for a front end" and stops. No decisions are made here.

## Sources and conventions

**SGE** means Son of Grid Engine 8.1.9 as packaged by Debian trixie (the
testbed's `soge` flavour). Man pages were taken from manpages.debian.org
(`trixie/gridengine-client/sge_submit.1`, `gridengine-common/sge_conf.5`,
`sge_queue_conf.5`, `sge_complex.5`, `sge_pe.5`, `sge_accounting.5`,
`sge_request.5`). They are cited by their upstream names: **submit(1)** (the
single page covering qsub, qsh, qlogin, qrsh, qalter, qresub),
**sge_conf(5)**,
**queue_conf(5)**, **complex(5)**, **sge_pe(5)**, **accounting(5)**,
**sge_request(5)**. Open Cluster Scheduler (OCS) pages were not compared line
by line; OCS differences are only those already recorded in the repo (D18).

**SGE source** citations are `file:line` in Debian's gridengine source
8.1.9+dfsg-13.1 (sources.debian.org, the trixie package), unless "+dfsg-17"
is named (salsa.debian.org/hpc-team/gridengine, HEAD d96f1c6).

**Slurm** man pages are cited as e.g. **sbatch(1) 25.11** and were rendered
from the `slurm-<ver>` tags of SchedMD/slurm (22.05.11, 23.11.11, 24.11.7,
25.05.9, 25.11.8). Source citations are `path:function` at tag
`slurm-25-11-8-1` unless another tag is named. "CL 23.02" means the 23.02
changelog (NEWS), "RN 23.11" the RELEASE_NOTES of that version.

**hpc-runner today** means `src/hpc_runner/schedulers/sge/` and
`templates/_environment.sh.j2`: every option as a `#$` directive in the
script, `-V` by default (D3), `-cwd`, `-S /bin/bash`, `-j y`, `-pe <pe> <cpu>`,
`-l <mem_resource>=<mem>`; interactive runs `qrsh -now no ... <script>` and
the script does `module purge` then `module load`.

Anything not confirmed from a primary source is marked **UNVERIFIED**, with
a note of what was searched. "(read from source, not run)" marks a fact
settled by reading code only. Output blocks marked "sketch" are
illustrations of documented behaviour, not captured output.

- Every claim was re-checked against primary sources on 2026-11-13 by
  independent checkers; what could not be confirmed is marked **UNVERIFIED**
  in place.
- Environment Modules: the saved copy of `doc/source/module.rst` was a
  0-byte file. The text read was `doc/source/module.rst` of
  envmodules/modules at v5.6.1 (raw.githubusercontent.com), cited below as
  module(1) 5.6.1.

---

## 1. Mental model

### SGE

A job is one script (or binary with `-b y`) bound to a set of slots. With no
`-pe` it gets one slot in one queue instance (testbed run E1: `NSLOTS=1`, qacct
`slots 1`; no man page states it); with `-pe <pe> N` it gets N
slots spread over hosts according to the PE's `allocation_rule`
(`$pe_slots` = all on one host, an integer = that many per host,
`$fill_up`, `$round_robin`; sge_pe(5) `allocation_rule`). The script is
started in the master queue ("The master queue is defined as the queue where
the parallel job is started", submit(1); that it runs once is not stated
there); anything on the other hosts is started by the
script (via `qrsh -inherit` under tight integration, `control_slaves TRUE`,
sge_pe(5)). qsub, qrsh, qsh and qlogin are four front ends to the same
thing: a job. `qrsh` is an interactive job, not a sub-unit of another one.

### Slurm

Slurm separates three things:

| Object | What it is | Created by |
|---|---|---|
| **Job (allocation)** | A set of nodes/CPUs/memory/GRES held for a time | `sbatch`, `salloc`, or `srun` when not already inside one |
| **Job step** | One launch of processes inside an allocation, with its own id (`123.0`, `123.1`), resources, accounting and exit code | `srun` (and implicitly: the `batch` step; the `extern` step only when `PrologFlags` contains `Contain`, `src/slurmctld/node_scheduler.c:3375-3376`) |
| **Task** | One process of a step (an MPI rank); `-n` counts tasks | `srun` launches N tasks of a step |

What each command creates:

- **sbatch**: an allocation, then "a single copy of the batch script on the
  first node in the set of allocated nodes" (sbatch(1) 25.11 DESCRIPTION).
  The script runs as the special `batch` step. It is not "the job" in the SGE
  sense; it is one step that happens to run first.
- **srun** outside any allocation: creates an allocation *and* one step in
  it (srun(1) DESCRIPTION: "If necessary, srun will first create a resource
  allocation"), waits, and releases the allocation when the step ends
  (**UNVERIFIED**: srun(1) 25.11 DESCRIPTION does not say the last two;
  srun source not traced).
- **srun** inside an allocation: creates only a step. It knows it is inside
  one because `SLURM_JOB_ID` is set ("SLURM_JOB_ID Same as --jobid", srun(1)
  INPUT ENVIRONMENT VARIABLES; `--jobid`: "Initiate a job step under an
  already allocated job").
- **salloc**: an allocation, then runs a command *on the host where salloc
  was typed* (default: the user's shell) and releases the allocation when it
  exits (salloc(1) DESCRIPTION). Only with `LaunchParameters=use_interactive_step`
  does a bare `salloc` start the shell on a compute node, via
  `srun --interactive --preserve-env --pty $SHELL`, and a `salloc <command>`
  still runs on the calling host (slurm.conf(5) 25.11 `LaunchParameters`,
  `InteractiveStepOptions`).

`srun` inside a batch script is therefore not "run this command": it is
"start a new step". By default a step gets all non-GRES resources of the
allocation on each node it uses (`--exact` restricts it), and steps do not
share resources unless `--overlap` is given (srun(1) 25.11 `--exact`,
`--overlap`). `--cpus-per-task` on srun implies `--exact`; RN 23.11:
"Starting in Slurm 22.05, --cpus-per-task implies --exact which is why we
needed to make srun not read SLURM_CPUS_PER_TASK." (the sources differ on
the version, see §2.8).

### Worked example

Same request, 4 CPUs, a script that runs a tool:

```bash
# SGE: one job, one script, 4 slots on one host
qsub -pe smp 4 -l h_rt=1:00:00 -cwd -V run.sh

# Slurm: one allocation with one task of 4 CPUs; the script is step "batch"
sbatch -n 1 -c 4 -t 1:00:00 run.sh
```

"4 slots on one host" holds only if the site's PE named `smp` has
`allocation_rule $pe_slots`; `smp` is a site-defined name (testbed run E2:
with that rule, 2 slots on one host).

If `run.sh` calls `srun tool` twice, Slurm records five rows, or four
without `PrologFlags=Contain` (no `123.extern` row,
`src/slurmctld/node_scheduler.c:3375-3376`). Sketch of
`sacct -j 123 -o JobID,JobName,State,ExitCode`:

```
JobID         JobName   State      ExitCode
123           run.sh    COMPLETED  0:0
123.batch     batch     COMPLETED  0:0
123.extern    extern    COMPLETED  0:0
123.0         tool      COMPLETED  0:0
123.1         tool      FAILED     1:0
```

The job's own exit code is the batch script's; each srun's is on its step
(job_exit_code.shtml "Job Step Exit Codes"; the derived exit code "is
initially set to the value of the highest exit code returned by all of the
job's steps", ibid. "Derived Exit Code and Comment String"). SGE has no
equivalent rows: what the script runs is invisible to qacct unless it is a
`qrsh -inherit` task of a tightly integrated PE, and then depending on the
PE's `accounting_summary`. The two pages state that condition in opposite
senses (both read, not resolved): accounting(5) "Such records are not
produced if the PE's accounting_summary parameter is false"; sge_pe(5) "a
value of FALSE indicates an individual accounting(5) record is written for
every slave task".

**Consequence for a front end.** An SGE "job" maps to a Slurm allocation plus
its batch step. Whether a command runs as the batch script itself or under
`srun` inside it decides whether it gets its own accounting row and exit
code, and whether it is subject to step-level resource binding.

---

## 2. Environment propagation

### 2.1 SGE

**`-V`**: "all environment variables active within the qsub utility be
exported to the context of the job" (submit(1) `-V`). Available for qsub,
qsh, qalter and **qrsh with a command** only.

**`-v var[=value],...`**: adds named variables (submit(1) `-v`; qsub, qrsh
with command, qalter). With no value the submitter's value is used: the man
page says only "optionally, values"; the behaviour is in
`common/parse_qsub.c:1705` and `libs/sgeobj/sge_var.c:895-898` (read from
source, not run).

**No `-V`**: the job does not start from an empty environment. It gets:

1. What SGE sets (submit(1) ENVIRONMENT VARIABLES): `HOME`, `USER`,
   `LOGNAME`, `SHELL` (from passwd; "not necessarily the shell in use"),
   `PATH=/usr/local/bin:/usr/ucb:/bin:/usr/bin` (man page; the source
   differs, see the end of this item), `HOSTNAME`, `TMPDIR`, `TMP`,
   `TZ` (from execd), `ENVIRONMENT=BATCH`, `JOB_ID`, `JOB_NAME`, `JOB_SCRIPT`,
   `REQUEST` (batch jobs only), `QUEUE`, `NSLOTS`, `NHOSTS`, `NQUEUES`, `PE`
   and `PE_HOSTFILE` (parallel jobs only),
   `RESTARTED`, `ARC`/`SGE_ARCH`, `SGE_BINARY_PATH`, `SGE_CWD_PATH`,
   `SGE_STDOUT_PATH`/`SGE_STDERR_PATH`/`SGE_STDIN_PATH`,
   `SGE_JOB_SPOOL_DIR`, `SGE_BINDING`, `SGE_TASK_ID` (`undefined` for a
   non-array job) and `SGE_TASK_FIRST/LAST/STEPSIZE`, and `TERM` for
   interactive jobs only.
   `PATH`, man page versus source: the man page gives the default above;
   `daemons/execd/exec_job.c:580-585` (read, not run) writes
   `<tmpdir>:<that default>`, or `<tmpdir>:<user PATH>` when `PATH` came in
   with `-v`/`-V`.
2. The submit-side copies `SGE_O_HOME`, `SGE_O_HOST`, `SGE_O_LOGNAME`,
   `SGE_O_MAIL`, `SGE_O_PATH`, `SGE_O_SHELL`, `SGE_O_TERM`, `SGE_O_TZ`,
   `SGE_O_WORKDIR` (ibid.: "qsub, qsh, and qlogin add several variables of
   the form SGE_O_NAME").
3. **The execution daemon's own environment**, if `execd_params INHERIT_ENV`
   is true, which is the default: "any environment variable that is set in
   the shell which starts the execution daemon ... will be set in the
   environment of any jobs ... unless ... explicitly overridden, such as PATH
   or LOGNAME" (sge_conf(5) `INHERIT_ENV`).
4. Whatever the job shell's startup files add. Under the default
   `shell_start_mode posix_compliant`, a shell named in `login_shells`
   (default `sh,bash,csh,tcsh,ksh`) is started as a login shell: "all system
   and user default resource files like .login or .profile will be executed"
   (sge_conf(5) `shell_start_mode`, `login_shells`). For bash that is
   `/etc/profile`, then the first of `~/.bash_profile`, `~/.bash_login`,
   `~/.profile` (bash(1) INVOCATION). queue_conf(5) `shell_start_mode` adds
   that it "can only be used for batch jobs submitted by qsub(1) and can't
   be used for interactive jobs submitted by qrsh(1), qsh(1), qlogin(1)".

Overriding SGE-set variables with `-v`/`-V`: the man page and the source
disagree (both read; nothing run). submit(1) ENVIRONMENT VARIABLES says of
`SGE_TASK_ID`, `SGE_TASK_FIRST`, `SGE_TASK_LAST` and `SGE_TASK_STEPSIZE`
"It is possible to change the predefined value of this variable with -v or
-V", while `JOB_SCRIPT` "can not be overwritten". The 8.1.9 source
(`daemons/execd/exec_job.c:568-569, 766-783, 922-930`;
`libs/sgeobj/sge_var.c:727-731`) copies the job's environment list first,
then sets the `SGE_TASK_*` values, then copies that group over the list
("set final of variables whose value shall be replaced"), so execd's values
replace submitted ones.
A `qsub -V` run from inside an array task therefore does not hand the
parent's `SGE_TASK_ID` to a non-array child: execd sets it to `undefined`
(`exec_job.c:777-782, 922-924`; read from source, not run; same code in
+dfsg-17; whether Debian patches touch this file was not checked).

**Default request files**: `$SGE_ROOT/$SGE_CELL/common/sge_request`,
`~/.sge_request`, `./.sge_request` may contain any option, including `-V`.
Processing order is: default files, then `#$` lines, then the command line,
so "the command line can be used to override the embedded flags and the
default request settings. The embedded flags, however, will override the
default settings" (submit(1) DESCRIPTION; sge_request(5)). `-clear` discards
everything before it. Repeatable options accumulate across sources
(sge_request(5)).

**qrsh**: per-option text in submit(1): `-V` "Available for qsub, qsh, qrsh
with command and qalter"; `-v` "Available for qsub, qrsh (with command
argument) and qalter"; `-display` "Available for qsh and qrsh with a
command"; `-cwd` "Available for qsub, qsh, qrsh and qalter only". The
`QRSH_WRAPPER` paragraph of submit(1) DESCRIPTION also says "The options
-cwd, -v, -V, and -display only apply to batch jobs"; no text read
reconciles the two. In the client source (+dfsg-17 `qsh.c:1627-1636,
2411-2428`; read from source, not run) qrsh without a command becomes a
login-type job, and for qlogin and qrsh without a command `-display`,
`-cwd`, `-wd`, `-v` and `-V` on the command line are rejected as unknown
options with `EXIT_FAILURE`; qrsh with a command keeps them. (hpc-runner's
`tests/integration/test_environment.py` checks the environment of batch
jobs only.)

### 2.2 Slurm `--export` (sbatch)

sbatch(1) 25.11 `--export={[ALL,]<environment_variables>|ALL|NIL|NONE}`;
"SLURM_* variables are always propagated".

| Mode | What sbatch sends | Login env loaded on the node? |
|---|---|---|
| (unset) / `ALL` | All of sbatch's environment | No (unless `--get-user-env`) |
| `NONE` | Only `SLURM_*` and SPANK option variables (22.05: `SLURM_*` only; SPANK from 23.11, §2.8) | **Yes**, implicitly, "as if --get-user-env was specified" |
| `NIL` (23.02.3+) | Only `SLURM_*` and SPANK option variables | No |
| `VAR1,VAR2=x` | `SLURM_*`, SPANK, and the named variables | **Yes**, implicitly |
| `ALL,VAR2=x` | All, plus VAR2. Which value wins when the caller already has VAR2: man page and source disagree, see below | **Yes**, implicitly (sbatch(1) 25.11: "NOTE: NONE and [ALL,]<environment_variables> implicitly work as if --get-user-env was defined.") |

Source, `src/sbatch/sbatch.c:_fill_job_desc_from_opts`: `ALL` merges
`environ`; `NIL` merges only SLURM/SPANK; `NONE` merges only SLURM/SPANK and
sets `get_user_env = true`; any other value goes through `env_merge_filter`
and sets `get_user_env = true`. Only the exact strings `ALL`, `NIL` and
`NONE` are special-cased, so `ALL,VAR2=x` takes that last branch
(`sbatch.c:456-473`; same branch at 22.05,
`slurm-22-05-11-1:src/sbatch/sbatch.c:444-447`). When true, sbatch adds
`SLURM_GET_USER_ENV=1` to the job's environment.

Precedence in `--export=ALL,VAR2=x`: man page and source disagree (both
read; not run). sbatch(1) 25.11: "If "ALL" is specified, then all user
environment variables will be loaded and will take precedence over any
explicitly given environment variables." `src/common/env.c:2596-2610`
processes the tokens left to right: `ALL` merges `environ`, then `VAR2=x` is
merged with overwrite. So with `ALL` first the explicit value replaces the
caller's; with `VAR2=x,ALL` the caller's replaces the explicit one.

**22.05 wording versus 22.05 behaviour.** sbatch(1) 22.05 says of `NONE`
"--get-user-env will be ignored". The 22.05 code
(`slurm-22-05-11-1:src/sbatch/sbatch.c:_fill_job_desc_from_opts`) sets
`opt.get_user_env_time = 0` for `NONE` and for explicit lists and then
`SLURM_GET_USER_ENV=1` whenever that is `>= 0`. So the login environment is
loaded in 22.05 too (slurmd tests for it at
`slurm-22-05-11-1:src/slurmd/slurmd/req.c:1953`). **UNVERIFIED**: that
"ignored" refers to the user's own `--get-user-env` timeout/mode (the code
overwrites `get_user_env_time` with 0; no source states what the man
sentence meant). sbatch(1) 23.02.7 and later say "as if --get-user-env was
specified". `NIL` (no login env) appeared in 23.02.3, not 23.02.0 (CL 23.02
under "Changes in Slurm 23.02.3": "sbatch - Added new --export=NIL option";
`"NIL"` is absent from `src/sbatch/sbatch.c` at `slurm-23-02-2-1` and
present at `slurm-23-02-3-1`).

### 2.3 What "the user's login environment" means mechanically

On the first node, before the batch step starts, slurmd
(`src/slurmd/slurmd/req.c:_get_user_env`) sees `SLURM_GET_USER_ENV=1` and
calls `env_array_user_default()` (`src/common/env.c`), which, as root:

```
su - <user> -c '/bin/echo; ...; /bin/echo XXXXSLURMSTARTPARSINGHEREXXXX;
                <slurmstepd getenv | /bin/env>; /bin/echo XXXXSLURMSTOPPARSINGHEREXXXXX'
```

(`_child_fn`: `execle(SUCMD, "su", "-", username, "-c", cmdstr, ...)`, or
without `-` if Slurm was built with `LOAD_ENV_NO_LOGIN`; the command run is
`slurmstepd getenv`, else `/bin/env`, else `/usr/bin/env`). It runs in new
PID and mount namespaces when the kernel allows it, falling back to plain
fork. The private PID namespace dates from 23.11.0 (CL 23.11: "Load the user
environment into a private pid namespace to avoid user scripts leaving
background processes on a node."). The mount namespace was added in 23.11.8
and 24.05.1 (`CLONE_NEWNS` in `src/common/env.c`: absent at
`slurm-23-11-7-1` and `slurm-24-05-0-1`, present at `slurm-23-11-8-1` and
`slurm-24-05-1-1`). Only the fallback is a 24.11.0 change (CL 24.11: "check
if PID or mnt namespaces are disabled and fall back to old logic that does
not rely on them when they are not available"). The text between the markers
is parsed into
variables; `DISPLAY`, `ENVIRONMENT` and `HOSTNAME` are discarded
(`env.c:_discard_env`). Then the submitted environment is merged **on top**:
`env_array_merge(&new_env, req->environment)` overwrites duplicates
(`env.c:env_array_merge`: "Any variables already found in dest_array will be
overwritten").

So:

- `--export=ALL --get-user-env`: login env as a base, every submit-host
  variable wins. The job looks like the submitting shell plus any variables
  that exist only in a login shell.
- `--export=NONE`: login env on the compute node plus `SLURM_*`. This is the
  closest Slurm analogue of "SGE without -V, login shell", but it is
  produced by `su -` running the user's profile on the node at job start.
- `--export=NIL`: `SLURM_*` and SPANK only; no `HOME`, `PATH` or `USER`
  unless the site's TaskProlog or the script sets them. Slurm itself does
  not set `HOME`, `USER` or `PATH` (no such assignment in `src/slurmd` or
  `env.c`); it does set `TMPDIR=/tmp` if unset (`src/slurmd/slurmstepd/task.c`),
  `ENVIRONMENT=BATCH` and `HOSTNAME` for a batch job
  (`env.c:env_array_for_batch_job`).

Timeout and failure: in 22.05 the retrieval had a default 8 s timeout and an
`S`/`L` mode (sbatch(1) 22.05 `--get-user-env[=timeout][mode]`; man page
only, not cross-checked in the 22.05 code). 25.05 deprecated both and set
the default timeout to 120 s (CL 25.05: "Deprecate "GetEnvTimeout" from
slurm.conf and "timeout" and "mode" optional parameters from sbatch's
--get-user-env option. The default timeout to get the user environment will
be 120 seconds."); sbatch(1) 25.05+ states "If the user environment
retrieval fails or times out, the job will be aborted, requeued and held".
How long the retrieval takes depends on the login scripts (`env.c` comment:
"Depending upon the user's login scripts, this may take a very long time to
complete or possibly never return"). **UNVERIFIED**: that a slow or
interactive `~/.bash_profile` matters on Slurm in a way it does not for an
SGE `-V` job (inference; sge_conf(5) `login_shells` indicates the profile
also runs for SGE batch jobs under the defaults, with or without `-V`).

Exported shell functions survive the parsing of this output; other
multi-line values do not (`src/common/env.c:2415-2441`, `:95`; read from
source, not run). A value that starts with `(` is treated as a bash function
and joined across lines until its braces balance, within `ENV_BUFSIZE`
(256 KiB). Any other value is cut at its first newline, and a later line
that contains `=` is parsed as a variable of its own.

`--get-user-env` exists only on sbatch; it was removed from salloc in 24.11
(RN 24.11 "Remove salloc --get-user-env"), and srun(1) does not list it at
22.05.11 or later. srun once had the option; by 17.02 it was rejected
(`slurm-17-02-0-1:src/srun/libsrun/opt.c:1689-1691`: "--get-user-env is no
longer supported in srun, use sbatch").

### 2.4 Input environment variables and precedence

Each command reads its options from variables: `SBATCH_*` for sbatch,
`SALLOC_*` for salloc, `SLURM_*` (plus a few `SRUN_*`) for srun.

| | Lowest | | Highest |
|---|---|---|---|
| SGE | sge_request files | `#$` lines | command line |
| sbatch | `#SBATCH` lines | `SBATCH_*` environment | command line |
| srun | `SLURM_*` / `SRUN_*` environment | | command line |

sbatch(1) INPUT ENVIRONMENT VARIABLES: "Environment variables will override
any options set in a batch script, and command line options will override
any environment variables." srun(1): "Command line options always override
environment variable settings." So on Slurm a user's
`export SBATCH_PARTITION=debug` beats a `#SBATCH -p batch` line in a
generated script, whereas on SGE `~/.sge_request` loses to `#$` lines.

`#SBATCH` lines are read until "the first non-comment, non-whitespace line"
and are not shell-expanded (sbatch(1) DESCRIPTION). SGE reads `#$` lines
through the whole script (submit(1) DESCRIPTION, "from top to bottom of the
script file"; the loop in `common/parse_job_cull.c:823, 889-899` runs to the
end of the file with no exit at the first non-directive line).

### 2.5 What Slurm adds to the job

Batch script (sbatch(1) OUTPUT ENVIRONMENT VARIABLES): `SLURM_JOB_ID`
(and legacy `SLURM_JOBID`), `SLURM_JOB_NAME`, `SLURM_JOB_NODELIST`,
`SLURM_JOB_NUM_NODES`, `SLURM_JOB_PARTITION`, `SLURM_JOB_ACCOUNT`,
`SLURM_JOB_QOS`, `SLURM_CPUS_ON_NODE`, `SLURM_JOB_CPUS_PER_NODE`,
`SLURM_NTASKS` (when requested), `SLURM_CPUS_PER_TASK` (only when `-c` or
`--tres-per-task=cpu=#` is given), `SLURM_MEM_PER_NODE` or
`SLURM_MEM_PER_CPU` (one of them, or neither when `pn_min_memory` is 0:
`src/common/env.c:1379-1387`; the man page only names them, and
`SLURM_MEM_PER_GPU` is a third documented variable),
`SLURM_TASKS_PER_NODE`, `SLURM_SUBMIT_DIR`, `SLURM_SUBMIT_HOST`,
`SLURM_CLUSTER_NAME`, `SLURM_ARRAY_*` for arrays, `SLURM_EXPORT_ENV`
("Same as --export."), and more. The SGE `SGE_O_*` family has no Slurm
equivalent beyond `SLURM_SUBMIT_DIR`/`SLURM_SUBMIT_HOST`.

Several of these **output** variables are also srun **input** variables
(`SLURM_EXPORT_ENV`, `SLURM_MEM_PER_CPU`, `SLURM_MEM_PER_NODE`,
`SLURM_CPUS_PER_TASK`, `SLURM_NTASKS`, `SLURM_JOB_ID`; srun(1) INPUT
ENVIRONMENT VARIABLES; `SLURM_CPUS_PER_TASK` only in 23.11+, §2.8). This is
how an `srun` inside the script inherits
the job's shape, and it is the root of the surprises in §2.6 and §2.7.

### 2.6 Steps: `SLURM_EXPORT_ENV`

`sbatch --export=X` (X not `ALL`) does `setenv("SLURM_EXPORT_ENV", X, 0)`
(`src/sbatch/opt.c:_opt_verify`, comment
`/* srun ignores "ALL", it is the default */`). That lands in the batch
script's environment, and every `srun` inside reads it as its own
`--export`; `SRUN_EXPORT_ENV` overrides it (`src/srun/opt.c:567-568`).

srun's `NONE` is not sbatch's `NONE`: "None of the user environment will be
defined" and no login environment is fetched (srun(1) 25.11 `--export`;
`src/srun/launch.c:_build_user_env` merges only SLURM/SPANK variables for
`NONE`; the test is the name prefix `SLURM`, 5 characters, not `SLURM_`,
`src/common/env.c:1908-1910`). Worked example (derived from the code, not
run):

```bash
#!/bin/bash
#SBATCH --export=NONE
module load xcelium/23.09     # sets PATH etc. in the batch shell
srun xrun -version            # step started with SLURM_EXPORT_ENV=NONE:
                              # the module's PATH is not in the step
```

srun itself resolves the executable through its own `PATH` before launch
(srun(1): "If executable can be resolved through PATH"), so the command can
still be found while the step lacks the module's variables.

sbatch(1) says: "To avoid steps inheriting environment export settings
(e.g. "NONE") from sbatch command, the environment variable
SLURM_EXPORT_ENV should be set to "ALL" in the job script." srun(1) says:
"To avoid steps inheriting environment export settings (e.g. "NONE") from
sbatch command, either set --export=ALL or the environment variable
SLURM_EXPORT_ENV should be set to "ALL"." (the same split at 22.05.11,
23.02.7, 23.11.11, 24.11.7, 25.05.9 and 25.11.8).

### 2.7 Running sbatch or srun from inside a job

**srun inside any allocation** (batch script, salloc shell, an `srun --pty
bash`): `SLURM_JOB_ID` is set, so srun creates a step in the enclosing
allocation rather than a new job (srun(1) `--jobid`, `SLURM_JOB_ID`). To
get a new job from inside one, the caller must remove `SLURM_JOB_ID`
(derived from `src/srun/allocate.c:660-666` and `src/srun/opt.c:579`; the
man page does not say it in words).
`SLURM_JOB_NAME` is ignored inside an allocation (srun(1) INPUT ENVIRONMENT
VARIABLES).

**sbatch inside a job** always creates a new job; it does not read
`SLURM_JOB_ID`. Its own input variables are `SBATCH_*` plus `SLURM_CLUSTERS`
and `SLURM_HINT` (`src/sbatch/opt.c` env table); sbatch(1) INPUT ENVIRONMENT
VARIABLES also lists `SLURM_CONF`, `SLURM_DEBUG_FLAGS`, `SLURM_EXIT_ERROR`,
`SLURM_STEP_KILLED_MSG_NODE_ID` and `SLURM_UMASK`, which are read elsewhere.
But with the default
`--export=ALL` the new job receives the parent's entire environment,
**including the parent's `SLURM_*` variables**. When the child starts,
slurmstepd (`src/slurmd/slurmstepd/mgr.c:401`) overwrites, name by name, the
`SLURM_*` variables it sets for the child
(`env.c:env_array_for_batch_job`) and leaves the rest. There is no general
`SLURM_*` scrubbing in `src/sbatch`, `src/slurmd` or `env.c`; sbatch unsets
only `SLURM_STEPMGR`. Individual names are removed elsewhere:
`SLURM_CONF_SERVER` (`env.c:321`), `SLURM_CPU_BIND*` for non-batch steps
(`env.c:405-408`), `SLURM_MEM_BIND*`/`SBATCH_MEM_BIND*` (`env.c:503-513`),
`SLURM_PRIO_PROCESS` (`slurmstepd/mgr.c:3055`), `SLURM_UMASK`
(`slurmstepd/ulimits.c:229`), `SLURM_RLIMIT_*` (`ulimits.c:268`),
`SLURM_MPI_TYPE` (`interfaces/mpi.c:493`). The following are derived from
the source, not from SchedMD documentation, and were not run:

- `env_array_for_batch_job` sets **either** `SLURM_MEM_PER_CPU` **or**
  `SLURM_MEM_PER_NODE` (or neither when `pn_min_memory` is 0). A parent
  submitted with `--mem-per-cpu` that
  submits a child with `--mem` gives the child both. An `srun` in the
  child then dies in `src/common/slurm_opt.c:_validate_memory_options`:
  `fatal("SLURM_MEM_PER_CPU, SLURM_MEM_PER_GPU, and SLURM_MEM_PER_NODE are mutually exclusive.")`.
  A command-line `--mem*` on that srun resets the others and avoids the
  error. (**UNVERIFIED**: a site page, Pawsey "srun Fails When Submitting
  From One Cluster to Another", is cited as describing the same failure and
  the `unset` fix; secondary, not opened in the check.)
- `SLURM_CPUS_PER_TASK` is set by sbatch only when the child used `-c`.
  Without `-c` the parent's variable stays set in the child, but slurmstepd
  overwrites its value with the child's own cpus-per-task, which the
  controller defaults to 1: the parent's value does not survive
  (`src/common/env.c:1337-1348`; `src/slurmctld/job_mgr.c:8862-8864`; the
  same overwrite at 22.05.11, 23.02.7, 23.11.11 and 24.11.7). srun in 23.11+
  reads the variable.
- `SLURM_EXPORT_ENV` from the parent survives: a child submitted with the
  default or `--export=ALL` does not call `setenv` at all, and with any
  other value sbatch's `setenv(..., 0)` does not overwrite. So a parent
  submitted with `--export=NONE` makes the child's sruns use `NONE` even if
  the child was submitted with the default.
- `SLURM_ARRAY_*` from an array parent survive into a non-array child.

The SGE analogue is narrower: with `-V`, `JOB_ID` and friends are reset by
the execd (testbed runs E5, E6: `JOB_ID` and `SGE_TASK_ID` set by `-v` or
inherited through `-V` are overwritten; not in the man pages), and
only the `SGE_TASK_*` override in §2.1 is documented (the source contradicts
it, §2.1).

### 2.8 Version differences that touch the environment

| Version | Change | Source |
|---|---|---|
| 22.05 | srun stops reading `SLURM_CPUS_PER_TASK`; use `SRUN_CPUS_PER_TASK` | RN 22.05; CL 22.05 |
| 22.05 | `--cpus-per-task` on srun implies `--exact`. Sources differ on the version: RN 23.11 says "Starting in Slurm 22.05"; srun(1) 22.05.11 has "Explicitly requesting this option implies --exact." where srun(1) 21.08.8-2 has "does not imply --exact"; NEWS lists "--cpus-per-task and --threads-per-core now imply --exact" under "Changes in Slurm 21.08.0rc1" | RN 23.11; srun(1); NEWS at `slurm-22-05-11-1` |
| 23.02.3 | `sbatch --export=NIL` added (not in 23.02.0 to 23.02.2) | CL 23.02 |
| 23.11 | srun reads `SLURM_CPUS_PER_TASK` again, `SRUN_CPUS_PER_TASK` removed | RN 23.11, CL 23.11 |
| 23.11 | SPANK option variables kept under `NIL`/`NONE` | RN 23.11 |
| 23.11 | get-user-env runs in a private PID namespace | CL 23.11 |
| 23.11.8, 24.05.1 | get-user-env also uses a mount namespace | CL 23.11, CL 24.05; `src/common/env.c` at those tags |
| 24.11 | `salloc --get-user-env` removed; get-user-env falls back to the old logic when PID or mount namespaces are unavailable | RN 24.11, CL 24.11 |
| 25.05 | `--get-user-env=timeout/mode` and `GetEnvTimeout` deprecated; 120 s default; failure requeues and holds | CL 25.05, sbatch(1) 25.05 |

So a batch script that relies on `srun` picking up `-c` from the job behaves
differently on 22.05/23.02 than on 23.11+.

### 2.9 Environment Modules across the boundary

Environment Modules keeps its state in ordinary environment variables:
`LOADEDMODULES` ("A colon separated list of all loaded modulefiles"),
`_LMFILES_` ("the full pathname for all loaded modulefiles"), `MODULEPATH`,
`MODULESHOME`, and the `__MODULES_*` family (`__MODULES_LMINIT`,
`__MODULES_LMCONFLICT`, `__MODULES_LMPREREQ`, `__MODULES_LMTAG`,
`__MODULES_SHARE_<VAR>` reference counters) (module(1) 5.6.1, envmodules
`doc/source/module.rst`, ENVIRONMENT). Initialisation behaves differently
depending on whether that state is present: "if the Modules environment is
found undefined (when both MODULEPATH and LOADEDMODULES are found either
unset or empty)" the `modulespath` and `initrc` configuration files are
evaluated; "if the Modules environment is found defined a module refresh is
automatically applied" (ibid., "Package Initialization"). The `module`
command itself is a shell function or alias, defined by the init script
(ibid.); `MODULES_SET_SHELL_STARTUP`, if set to `1`, exists "to ensure that
the module command is still defined in sub-shells" by setting
`ENV`/`BASH_ENV` (ibid., ENVIRONMENT).

What crosses into the job:

| Submission | Module state variables | `module` function |
|---|---|---|
| SGE `-V` | carried | if the job shell defines it (login shell profile, or the script sources the init file); exported bash functions are NOT carried (run on the testbed, see below the table) |
| SGE no `-V` | not carried; login shell profile may load defaults | if the job shell defines it, as above |
| Slurm `--export=ALL` | carried | as for SGE `-V`; sbatch merges all of `environ`, so exported `BASH_FUNC_*` variables in it are copied |
| Slurm `--export=NONE` | whatever `su -` login on the node produced, plus `SLURM*`/SPANK variables from the submit side | kept by the parser if the login output contains it (§2.3; read from source, not run). **UNVERIFIED**: whether a node's `su -` login sources the Modules init at all (site configuration) |
| srun step under `SLURM_EXPORT_ENV=NONE` | none | none |

Exported functions. SGE `-V` does not carry them: with `one` and `multi`
exported by `export -f`, the caller's `env` shows `BASH_FUNC_one%%` and
`BASH_FUNC_multi%%`, the job's `env` has no `BASH_FUNC` entry and both
names are undefined in the job; a plain variable holding a newline arrives
intact (run F1, `runs/sge-soge-2026-11-14-exp2.out`). This contradicts the
reading of the source (+dfsg-17 `parse_qsub.c:2102-2131`, "no name
filter"); which step drops them was not traced. Modules 5.6.1 on bash
exports the function (`tcl/envmngt.tcl.in:918-921`: `export -f
_module_raw; export -f module;`), so under `-V` the `module` function
reaches a job only if the job shell defines it again. Bash puts exported functions in `environ` under `BASH_FUNC_<name>%%`
(seen in the caller's `env` in the same run; bash 5.2 on Debian trixie).

hpc-runner's script sources the module init file (`module_init_script`, or
without it the first existing of three fixed paths, or nothing if none
exists) and runs `module purge` before loading
(`_environment.sh.j2:14-24, 34, 50-53`). **UNVERIFIED**: that this is what
makes inherited `LOADEDMODULES`/`_LMFILES_` harmless under D3 on SGE (an
effect claim; the template was read, no capture or test output was). The
same template under Slurm `--export=ALL` sees the same inherited state.

**Consequence for a front end.** `-V` maps most directly to the Slurm
default (`--export=ALL`); `inherit_env = false` has three Slurm candidates
with different results (`NONE` = login env fetched by `su -` on the node,
which can time out; `NIL` = only `SLURM_*` and SPANK option variables,
23.02.3+; explicit list = login env plus named variables). Any srun the front end or the user puts inside a batch
script inherits `SLURM_EXPORT_ENV`. Submitting from inside a job carries
stale `SLURM_*` variables unless something removes them.

---

## 3. Interactive and GUI jobs

### 3.1 SGE qrsh

- `qrsh [options] command` runs the command as an interactive job with
  rsh-like I/O on the current terminal; without a command it gives an
  rlogin-like session (submit(1) DESCRIPTION).
- `-now y` is the default for qrsh: start immediately in an INTERACTIVE
  queue or fail with exit 1. `-now n` queues like a batch job (submit(1)
  `-now`; queue_conf(5) `qtype`). The testbed found that `qrsh -now no` is
  scheduled into BATCH-only queues too (TESTBED_REVIEW.md D-T28).
- `-pty`: qrsh without a command gets a pty; qrsh with a command does not
  unless `-pty yes` (submit(1) `-pty`).
- qrsh ignores `-o`, `-e` (submit(1) `-e`), and `-S` is not available to it.
- Exit code: hpc-runner relies on qrsh returning the command's exit code;
  `tests/integration/test_submit.py::test_interactive` asserts that `exit 3`
  comes back as 3 on the testbed. submit(1) does not document this; its
  EXIT STATUS lists 0, 25 and ">0 Error occurred". The source does, for the
  builtin startup method (`clients/common/sge_client_ijs.c:604-610`; read
  from source, not run; rsh/ssh startup methods not read): "the
  UNREGISTER_CTRL_MSG contains the exit status of the qrsh_starter in case
  of qrsh <command> and the exit status of the shell for qlogin/qrsh <no
  command>. If the job was signalled, the exit code is 128+signal."
- A qrsh-side failure is not distinguishable from a command that exits 1:
  every failure path read in the qrsh client sets exit status 1
  (`clients/qsh/qsh.c:2145, 2209-2213, 2226, 2283, 2292`; read from source,
  not run). Testbed run E13: `exit 7` gives 7, `kill -9 $$` 137, `kill -15 $$` 143,
  unknown queue or resource 1. **UNVERIFIED**: the code returned when the
  connection is lost mid-job, and for an out-of-memory kill.
- Ctrl-C and terminal loss under qrsh: not described in submit(1) or
  remote_startup(5). With the builtin method the qrsh client's tty loop
  terminates on SIGHUP, SIGINT, SIGQUIT or SIGTERM
  (`sge_client_ijs.c:398-405`; read from source, not run); with an external
  rsh-type client the parent installs forwarding handlers for SIGINT,
  SIGQUIT and SIGTERM (+dfsg-17 `qsh.c:719-721`). Testbed run F4 (builtin
  method): killing the qrsh client with INT, HUP or TERM kills the remote
  job each time (qacct `exit_status 137`, `failed 100`) and the client
  exits 0. Not run with a real terminal.

### 3.2 Slurm srun, salloc

- `srun cmd` from a login node: allocation + step; blocks until resources
  are free unless `-I/--immediate[=secs]` (srun(1)). This is the analogue
  of `qrsh -now no`; `srun -I` is the analogue of `-now y`, with exit code
  `SLURM_EXIT_IMMEDIATE` (default 1, `src/srun/opt.c:90`; applied in
  `src/srun/allocate.c:212`).
- By default srun forwards the terminal's stdin to all tasks (srun(1) `-i`)
  and streams their output (**UNVERIFIED**: the `-i` entry covers stdin
  only; the stdout/stderr default sentence in srun(1) was not located). No
  pty unless `--pty`.
- `--pty` "Execute task zero with pseudo terminal mode or using pseudo
  terminal specified by <File Descriptor>", implies `--unbuffered` and sends
  other tasks' output to `/dev/null` (srun(1) 25.11 `--pty`). It is
  incompatible with `--input/--output/--error` (`src/srun/opt.c:1310-1312`
  only; the 25.11 man entry does not say it). In pty mode srun puts the
  local terminal in raw mode (`src/srun/srun_pty.c:_pty_thread` calls
  `cfmakeraw`). `src/srun/srun.c:cfmakeraw` clears `ISIG`, but it is a
  fallback compiled only when the C library has no `cfmakeraw`
  (`#ifndef HAVE_CFMAKERAW`); otherwise the libc function is what runs.
- Working directory: srun and sbatch default to the caller's cwd
  (`src/common/slurm_opt.c:arg_reset_chdir`); salloc does not set one.

### 3.3 Exit codes

| | SGE qrsh | Slurm srun |
|---|---|---|
| Command exits N | N (testbed observation and source, §3.1) | "highest exit code of all tasks" (srun(1) RETURN VALUE) |
| Command killed by signal S | 128+S per the source comment quoted in §3.1 (builtin method; read from source, not run) | 128+S, highest over tasks (ibid.) |
| Out of memory | **UNVERIFIED** (nothing in submit(1), remote_startup(5), `qsh.c` or `sge_client_ijs.c`; sge_shepherd(8) not read) | 253 is "reserved for out-of-memory errors" (srun(1) 22.05 and 25.11) |
| Launcher's own error | ">0" (submit(1) EXIT STATUS); 1 on every failure path read in `qsh.c` (§3.1); `-now y` no resources: 1 | `SLURM_EXIT_ERROR`, default 1 (`src/srun/opt.c` `error_exit = 1`; `src/srun/srun.c` reads the variable) |
| No resources with immediate | 1 (`-now y`) | `SLURM_EXIT_IMMEDIATE`, default 1 |

By default both make a launcher failure look like a command that exited 1.
On srun the two variables let a caller choose distinct codes.

For comparison, `qsub -sync y` returns the job's code, or 1 if the job
"fails to complete successfully"; for arrays, the first non-zero task code
(submit(1) `-sync`). `sbatch --wait` returns the job's code, 1 if it ended
by a signal, and for arrays "the highest value for any task" (sbatch(1)
25.11 `-W`). `salloc` returns 1 if it cannot run the command and 0 "if
success or if killed by signals HUP, INT, KILL, or QUIT" (salloc(1) RETURN
VALUE): when one of those signals kills the command salloc returns 0, not
a signal code; otherwise it returns the command's exit status
(`src/salloc/salloc.c:631-649`).

### 3.4 Ctrl-C and losing the terminal (srun)

From srun(1) SIGNALS AND ESCAPE SEQUENCES and
`src/srun/srun_job.c:_srun_signal_mgr`, `_handle_intr`:

- Without `--pty`: the first Ctrl-C prints task status ("interrupt (one
  more within 1 sec to abort)"); a second within one second sends SIGINT to
  all tasks and terminates the step; a third makes srun exit without
  waiting. With `-X/--disable-status` the first Ctrl-C is forwarded. (The
  source does not test for `--pty` in this handler; "without `--pty`" is
  this document's framing.)
- With `--pty`: the local terminal is raw (§3.2), so Ctrl-C is not turned
  into a signal locally; the man page does not say what happens.
  **UNVERIFIED**: that the byte is delivered to the remote pty, where the
  remote line discipline turns it into SIGINT for the foreground process
  (derived from `cfmakeraw`; the slurmstepd pty code was not read).
- SIGHUP or SIGTERM to srun (terminal closed, ssh dropped, `kill`): srun
  logs "forcing job termination" and sends **SIGKILL** to the tasks
  (`_srun_signal_mgr`, `case SIGTERM: case SIGHUP:`; SIGQUIT falls through
  to the same code after `info("Quit")`). A GUI started by `srun` dies with
  the terminal that ran srun unless srun itself is detached from it
  (inferred from that code; not run).

### 3.5 GUI applications: three ways to reach an X server

The DISPLAY name says where the X client connects: `:N` is the local
socket `/tmp/.X11-unix/XN` on the host where the client runs (Xserver(1)
FILES: "/tmp/.X11-unix/Xn Unix domain socket for display number n";
libxtrans `Xtranssock.c:217` at `xtrans-1.5.0`; X(7) "DISPLAY NAMES" says
only "If the hostname is not given, a connection to a server on the same
machine will be used.");
`host:N` is TCP port 6000+N on `host` (libxtrans `Xtransint.h:123`
`#define X_TCP_PORT 6000`, `Xtranssock.c:873`; not stated in X(7)). So the
same
DISPLAY string means different servers on the login node and on a compute
node. submit(1) `-display`: "Local display names (e.g. :0) cannot be used in
grid environments."

**(a) Plain DISPLAY (SGE `-V`/`-v DISPLAY`; Slurm `--export=ALL` or
`--export=DISPLAY`).** The scheduler only carries the string; the
application on the compute node connects to the X server itself and, where
the server requires cookie authorization (not checked per server), must
find a matching cookie in `$XAUTHORITY` or `~/.Xauthority` (X(7); Xau(3)
`XauFileName`). **UNVERIFIED**: that this works the same on both schedulers
(synthesis; no source states it and it was not run on either).

**(b) Slurm built-in X11 forwarding, `srun --x11` / `salloc --x11`.** How
it works (`src/common/x11_util.c`, `src/slurmd/slurmstepd/x11_forwarding.c:setup_x11_forward`,
`src/slurmd/slurmd/req.c`):

1. On the submit host, srun reads `DISPLAY`. `:N` becomes the local socket
   `/tmp/.X11-unix/XN` (srun exits if it cannot stat it); `host:N` becomes
   TCP `host:6000+N` (`x11_get_display`).
2. srun runs `/usr/bin/xauth list $DISPLAY` and extracts an
   `MIT-MAGIC-COOKIE-1` (wildcard cookies accepted from 22.05.6, not in
   22.05.0 to 22.05.5: CL 22.05 under "Changes in Slurm 22.05.6", "Permit
   using wildcard magic cookies with X11 forwarding.");
   no cookie or no xauth at `/usr/bin/xauth` and srun exits
   (`x11_get_xauth`, `XAUTH_PATH` is hard-coded); no `DISPLAY` and srun
   exits in `x11_get_display`, which is called first.
3. The target, port and cookie are stored on the job record at allocation
   (`src/slurmctld/job_mgr.c`). On each node chosen by `--x11=all|first|last`
   (the source also accepts `batch`, handled like `first`,
   `src/common/x11_util.c:68-75`; not in the man page),
   the job's `extern` step listens on a local port (6020 to 6099,
   `x11_forwarding.c:217`), writes the cookie with
   `xauth` into a node-local file under `TmpFS` (or `~/.Xauthority` with
   `X11Parameters=home_xauthority`), and the step's environment gets
   `DISPLAY=localhost:<N>.0` and `XAUTHORITY=<that file>`. If slurmd gets
   no display number back from the extern step it sets
   `DISPLAY=SLURM_X11_SETUP_FAILED` (`req.c:928-934`). Other failures
   differ: if slurmd cannot connect to the extern stepd it logs and leaves
   `DISPLAY` untouched (`req.c:919-923`); if `setup_x11_forward()` fails the
   extern step does `_exit(127)` (`slurmstepd/mgr.c:1351-1354`).
4. Each X connection the application opens is accepted by the extern step,
   which opens a new Slurm connection **back to the srun/salloc process on
   the submit host** (`SRUN_NET_FORWARD`); srun then connects to the
   original display from step 1 and relays the bytes.

Requirements:

- Built with X11 support (on by default; `--disable-x11` removes it,
  `auxdir/x_ac_x11.m4`). Without it the `--x11` option name is not
  registered, so a SPANK plugin may claim it (`slurm_opt.c`
  `slurm_opt_x11`).
- `PrologFlags=X11` in slurm.conf, else the job is rejected with
  `ESLURM_X11_NOT_AVAIL` (`job_mgr.c:validate_job_create_req`). X11
  "implicitly enables both Contain and Alloc" and "is incompatible with
  ProctrackType=proctrack/linuxproc"; `NoHold` "cannot be combined with the
  Contain or X11 flags" (slurm.conf(5) 22.05 and 25.11 `PrologFlags`).
- `X11Parameters` has one documented value in 22.05 through 25.11,
  `home_xauthority` (slurm.conf(5)).
- `xauth` at `/usr/bin/xauth` on the submit host and on compute nodes.
- Compute nodes must be able to open connections to the submit host where
  srun runs. The node connects to the job's `other_port`, the listening
  port of srun's/salloc's allocation message thread; that port is taken
  from `SrunPortRange` when set and is otherwise ephemeral
  (`src/slurmctld/node_scheduler.c:3283`; `src/api/allocate_msg.c:124-128`;
  slurm.conf(5) `SrunPortRange`; read from source, not run).
- Batch jobs cannot use it: "batch job cannot use X11 forwarding"
  (`job_mgr.c:_validate_job_desc`). It is an attribute of the allocation:
  `srun --x11` inside an existing allocation is ignored with an error-level
  log line, "Ignoring --x11 option for a job step within an existing job.
  Set x11 options at job allocation time.", and the step is still created
  (`src/srun/srun_job.c:1388-1393`; `src/slurmd/slurmd/req.c:1175-1176,
  1194-1197`; read from source, not run). srun's option processing still
  calls `x11_get_display()` and `x11_get_xauth()` first
  (`src/srun/opt.c:1320-1323`), which exit if `DISPLAY`, the socket, xauth
  or a cookie is missing.
- Fixes relevant to EDA-style GUIs landed in 24.11/25.05: "Delay closing
  sockets in eio code which fixes issues in X11 forwarding when using
  applications such as Emacs or Matlab" (CL 24.11 under 24.11.6; CL 25.05
  under 25.05.0), "Fix x11 forwarding issues causing applications (e.g.
  matlab) to intermittently crash on startup" (CL 25.05 under 25.05.2),
  "Fix x11 forwarding not working with TLS enabled" (CL 25.05 under
  25.05.0).

**(c) SGE has no built-in forwarding** with the builtin startup method
(remote_startup(5): "The builtin method does not support forwarding of X
graphics from the compute nodes");
`qsh` starts an xterm against `DISPLAY` or `-display` (submit(1)
DESCRIPTION), which is approach (a).

What each approach needs, by kind of DISPLAY on the login host:

| DISPLAY on login host | (a) plain DISPLAY, either scheduler | (b) `srun --x11` |
|---|---|---|
| `:5` (VNC/ThinLinc/ETX session on that login host) | Client on compute node connects to *its own* `:5`: wrong or no server | srun connects to `/tmp/.X11-unix/X5` on the login host; needs an xauth cookie for `:5` |
| `localhost:10.0` (`ssh -X`) | Compute node's own `localhost:6010`: no server (an inference about a typical node, not sourced) | srun connects to `localhost:6010` on the login host, read here as sshd's forward (not in the Slurm source); cookie from `xauth list` |
| `loginhost:5` (TCP) | Works (not run) if the X server listens on TCP, the node can reach `loginhost:6005`, and the cookie for `loginhost:5` is readable on the node (e.g. shared `~/.Xauthority`) | srun connects to `loginhost:6005` from the login host |

**UNVERIFIED**: whether particular VNC/ThinLinc/ETX servers listen on TCP or
create xauth cookies by default. Read so far: TigerVNC v1.15.0's
`vncserver` script creates a cookie with `xauth` (`vncserver.in:214-239`);
xorg-server 21.1.13 does not listen on TCP unless built with `listen_tcp`
(default false; `os/utils.c:652-655`, `meson_options.txt:50-51`), and
Xserver(1) has `-listen tcp`/`-nolisten tcp`. How a given TigerVNC package
is built and started was not checked; nothing was opened for ThinLinc or
ETX.

**Consequence for a front end.** `qrsh -now no` maps to blocking `srun`;
`srun --pty` changes output handling and local Ctrl-C handling (the remote
effect is **UNVERIFIED**, §3.4). A plain-DISPLAY GUI path needs
network-visible displays (**UNVERIFIED**: that it behaves as on SGE;
synthesis, not run); `srun --x11`
handles local and ssh displays but depends on site build/config, an xauth
cookie, node-to-login connectivity, and the srun process staying alive. A
front end that wants the command's exit code must account for srun's (and
salloc's) own codes, and for SIGHUP killing the tasks.

---

## 4. Resources

### 4.1 SGE

- **Slots** are the CPU unit. A serial job has one (testbed run E1); a parallel job takes N
  from a PE (`-pe smp 8`), and the PE's `allocation_rule` places them
  (sge_pe(5)).
- **`-l name=value`** (submit(1): `-l resource=expression,...`) requests a
  complex attribute. An attribute is either a
  plain requestable (matched against host/queue values, e.g. a load value
  such as `mem_free`, or a fixed one like `arch`, "An architecture string
  compiled into the sge_execd", which load_parameters(5) lists among the
  load parameters) or a **consumable**,
  which SGE books and subtracts (complex(5) `consumable`).
- Consumables are **per slot** (`consumable` 'yes'/'y': "the limit is
  multiplied by the number of slots being used by the job") or **per job**
  ('JOB'/'j': "debited as requested (without multiplication) from the
  allocated master queue") (complex(5)). complex(5) names "floating software
  licenses" as a use of consumables and says "The complex_values definition
  of the "global" host specifies cluster global consumable settings"; that
  licences are typically put there is not in the page.
- Enforced limits `h_vmem`/`s_vmem`, `h_cpu`/`s_cpu`: "For parallel processes, the
  limit is applied per slot" (queue_conf(5) RESOURCE LIMITS). `h_rt` is wall
  time per job.
- Which memory attribute a site uses, whether it is consumable, and per
  slot or per job, is site configuration. hpc-runner already models this:
  `memory_resource` (default `mem_free`) and `memory_per = "job"|"slot"`
  (D10).

### 4.2 Slurm

- **Nodes** `-N`, **tasks** `-n`, **CPUs per task** `-c`. "sbatch does not
  launch tasks"; `-n` advises that later steps "will launch a maximum of
  number tasks" (sbatch(1) `-n`). `-c` keeps a task's CPUs on one node (sbatch(1) `-c`).
  A threaded program is `-n 1 -c 8`; `-n 8` is eight separate tasks that
  may land on different nodes.
- **Memory**: `--mem` is "real memory required **per node**"; `--mem-per-cpu`
  is per allocated CPU; `--mem-per-gpu` per GPU; mutually exclusive; `--mem=0`
  means all memory of each node (sbatch(1) `--mem`). Default units MB.
  Memory is only scheduled when `SelectTypeParameters` includes memory (e.g.
  `CR_Core_Memory`, which `select/cons_tres` uses by default;
  `select/linear` by default does not consider memory; slurm.conf(5)
  `SelectTypeParameters`) and only enforced with `ConstrainRAMSpace` or
  `OverMemoryKill` (sbatch(1) `--mem` NOTE). Defaults come
  from `DefMemPerCPU`/`DefMemPerNode` (also `DefMemPerGPU`; slurm.conf(5)).
- **GRES** `--gres=gpu:2` is "requested per node" (sbatch(1) `--gres`);
  `--gpus=N` is per job, `--gpus-per-node`, `--gpus-per-task` also exist.
- **Licenses** `-L name[:count]` are cluster-wide counters from
  `Licenses=` in slurm.conf or remote licences in slurmdbd, the latter
  requested as `name@server[:count]` (sbatch(1) `-L`); "Slurm does not
  prevent jobs from using licenses that are not explicitly listed in the
  job submission specification" (slurm.conf(5) `Licenses`). OR (`a|b`) between licences is new in 25.05
  (CL 25.05).
- **Constraints** `-C feature` select nodes by admin-defined features; a
  filter, not a consumable (sbatch(1) `-C`). `--prefer` is the soft form.
- **Time** `-t`: a bare number is minutes (sbatch(1) `-t`; hpc-runner D10
  already refuses bare integers for this reason).

### 4.3 Worked examples

Site names (`smp`, `mpi`, `h_vmem`, `mem_free`, `gpu`, `xcelium_lic`) are
illustrative; each is site configuration on SGE. `h_vmem` and `mem_free` are
stock attribute names; what is site configuration is their `consumable`
setting. The stock entries are `consumable NO`, relop `<=` (read in
`source/dist/util/resources/centry/` of the gridengine/gridengine, daimh/sge
and hpc-gridware/clusterscheduler trees, which agree; not read in the SoGE
8.1.9 source). The per-slot `h_vmem` bookings below hold only where the site
sets `consumable YES`.

**1. Single-threaded, 8 GB**

```bash
qsub -l h_vmem=8G run.sh          # per-slot consumable: 1 slot x 8G
qsub -l mem_free=8G run.sh        # load-value requestable: node must report >= 8G free
sbatch -n 1 --mem=8G run.sh       # 1 task, 1 CPU, 8 GB on its node
```

**2. Eight threads, 32 GB total**

```bash
qsub -pe smp 8 -l h_vmem=4G run.sh   # per-slot: 8 x 4G = 32G booked and enforced
qsub -pe smp 8 -l mem_free=32G run.sh # requestable: not multiplied
sbatch -n 1 -c 8 --mem=32G run.sh    # per node = per job here (one node)
sbatch -n 1 -c 8 --mem-per-cpu=4G run.sh  # same total, scales with -c
```

**UNVERIFIED**: that a non-consumable request such as `mem_free=32G` is not
multiplied by slots (complex(5), submit(1) `-l` and `-pe`, and sge_pe(5)
were read; multiplication is stated only for consumables; scheduler source
not read). With `--mem-per-cpu` the total can exceed 8 x 4G: "If resources
are allocated by core, socket, or whole nodes, then the number of CPUs
allocated to a job may be higher than the task count" (sbatch(1)
`--mem-per-cpu`).

`-c 8` without `-n 1` also gives one task by default
(`src/common/slurm_opt.c:2684-2688, 5805, 5817-5819`: `ntasks` defaults to 1
and `min_cpus` = 1 x `cpus_per_task`). sbatch(1) `-n` says only "The
default is one task per node, but note that the --cpus-per-task option will
change this default". `-n 8` would ask for eight tasks.

**3. Two GPUs**

```bash
qsub -l gpu=2 run.sh                 # if 'gpu' is a per-slot consumable,
qsub -pe smp 4 -l gpu=2 run.sh       # ... this books 4 x 2 = 8 unless it is JOB
sbatch --gres=gpu:2 run.sh           # 2 per node; on one node = 2
sbatch --gpus=2 run.sh               # 2 per job, may span nodes
```

**4. An EDA tool needing one floating licence**

```bash
qsub -l xcelium_lic=1 run.sh         # global consumable (complex_values on 'global')
sbatch -L xcelium:1 run.sh           # Licenses=xcelium:N in slurm.conf, or remote via slurmdbd
```

A remote licence is requested as `name@server[:count]` (sbatch(1) `-L`:
"--license=nastran@slurmdb:12"), not as the bare `xcelium:1` shown.

Slurm does not talk to the licence server by itself (licenses.shtml 25.11
"Remote Licenses": "Remote licenses do not offer any kind of integration
with third party license managers by themselves."). For SGE the same is
**UNVERIFIED** (complex(5) describes only "Grid Engine internal
bookkeeping"; no SGE page read mentions a licence server). Both count what
jobs ask for against an admin-configured total (complex(5); slurm.conf(5)
`Licenses`).

Live licence availability. SGE: a load sensor can report a value for a
consumable, and the more restrictive of the load report and the internal
bookkeeping is kept (complex(5); sge_conf(5) `load_sensor`); no page read
describes a licence-specific sensor. Slurm, remote licences only: "Starting
with Slurm 23.02, the LastConsumed field for remote licenses is designed to
be periodically updated with the active use count from a license server",
with `sacctmgr -i update resource <name> set lastconsumed=<n>`
(licenses.shtml 25.11 "Dynamic Licenses"; the 23.02 statement was read only
in the 25.11 guide). **UNVERIFIED**: an `scontrol` update of a licence
count (not in that guide; scontrol(1) not searched).

**5. MPI on two nodes, 16 ranks each**

```bash
qsub -pe mpi 32 run.sh               # PE 'mpi' with allocation_rule 16, control_slaves TRUE
sbatch -N 2 --ntasks-per-node=16 run.sh   # script then runs: srun ./sim
```

On SGE the script's MPI launcher reads `$PE_HOSTFILE` and starts remote
ranks via `qrsh -inherit` (sge_pe(5) `control_slaves`). On Slurm the
script's `srun ./sim` is the launcher and creates step `.0` with 32 tasks.

**Consequence for a front end.** hpc-runner's `cpu` is SGE slots; on Slurm it
has two readings (`-c` for one multi-threaded task, `-n` for N tasks) with
different placement. `mem` per job (D10) is `--mem` on a single node and is
per node on more than one. GPUs and licences, which on SGE are both `-l`
resources, are separate options on Slurm.

---

## 5. Queues and partitions, projects and accounts, QOS, priority

| Concept | SGE | Slurm |
|---|---|---|
| Where jobs run | Cluster queues (`qconf -sq`), each a host list with slots, limits, `qtype BATCH/INTERACTIVE`, PE list; a job may match several | Partitions: node sets with limits; one default partition |
| Choosing | `-q` "has all the properties of a resource request and will be merged with requirements derived from the -l option" (submit(1) `-q`); without it any matching queue (**UNVERIFIED** at `-q`, which is silent on that case; nearest text is under `-l`: "Launch the job in a Grid Engine queue meeting the given resource request list.") | `-p p1,p2`; without it the default partition; with a list, the earliest-starting one, "although higher priority partitions will be considered first" (sbatch(1) `-p`) |
| Interactive vs batch | Queue type; `-now y` needs INTERACTIVE (queue_conf(5) `qtype`) | No queue type among the 25.11 partition parameters (confirmed by absence, slurm.conf(5)); srun and salloc take `-p`. Access controls (`AllowGroups`, `AllowAccounts`, `AllocNodes`, `RootOnly`, `ReqResv`, a partition QOS) can still refuse a job |
| Grouping for share/limits | **Project** `-P`; admin grants access (submit(1) `-P`). **UNVERIFIED**: that the project is the grouping for share/limits (project(5), sched_conf(5), sge_resource_quota(5) not read; the pages read state access control only) | **Account** `-A` ("The account is an arbitrary string", sbatch(1)). An account is one parameter of an association, "The entity used to group information consisting of four parameters: account, cluster, partition (optional), and user" (sacctmgr(1)); limits attach to the association |
| Accounting label | **Account string** `-A`, default `sge` (submit(1) `-A`); no policy use (**UNVERIFIED**: submit(1) `-A` states only the accounting-record use; no page read says either way) | Same `-A` |
| Enforcement | Projects/ACLs on queues (`projects`, `user_lists`, queue_conf(5)) | `AccountingStorageEnforce=associations,limits,qos,...` (slurm.conf(5)) |
| QOS | No direct equivalent (**UNVERIFIED**: sge_resource_quota(5) not read) | `--qos`; limits and priority per QOS (qos.shtml "Effects on Jobs"); restricted per association if enforced (sbatch(1) `-q`) |
| User priority | `-p -1023..1024`, users may only lower (submit(1) `-p`) | `--nice[=adj]`: +100 when given with no value, no adjustment without `--nice`; only privileged users go negative; `--priority` admin only (sbatch(1)) |

Note the letter clash: SGE `-A` is an accounting string and `-P` the
project ("free" and "policy object" for these are this document's words,
see the **UNVERIFIED** cells above); Slurm `-A` is the policy object when
`AccountingStorageEnforce` is set (without it: "The account is an arbitrary
string", sbatch(1)) and `-p` is the partition, not the priority.

**Consequence for a front end.** hpc-runner's `queue` maps to a partition and
`project` to the Slurm account (**UNVERIFIED** as fact: a mapping statement;
no Slurm backend exists in the repo to check it against); SGE's account
string has no separate Slurm field (**UNVERIFIED**: a negative that depends
on what counts as separate; sbatch(1) also has `--comment` and `--wckey`).
The mapping of SGE `-p` to Slurm `--nice` is not a sign flip only:
the range and default step differ.

---

## 6. Arrays, dependencies, output names, working directory, shell

### Arrays

| | SGE | Slurm |
|---|---|---|
| Syntax | `-t n-m:s`, `-tc N` for concurrency | `--array=0-15:4%4`, lists `1,3,7` allowed |
| Index range | from 1 (submit(1) `-t`) | from 0 to `MaxArraySize-1` (sbatch(1) `-a`) |
| Ids | one `JOB_ID`; the task is a separate column in qstat (`ja-task-ID`, qstat(1)) and a separate field in qacct (`jobnumber`, `taskid`; captures `qstat_mixed.txt`, `qacct_array_task.txt`). The dotted `job_id.task_id` form appears only in default output file names (submit(1) `-o`, `-e`) | every task has its own `SLURM_JOB_ID`; `SLURM_ARRAY_JOB_ID` is the first; a task is referred to as `36_1`, "SLURM_ARRAY_JOB_ID plus SLURM_ARRAY_TASK_ID values separated by an underscore" (job_array.shtml) |
| Task variable | `SGE_TASK_ID`, `undefined` for non-arrays | `SLURM_ARRAY_TASK_ID`, unset for non-arrays |
| Other vars | `SGE_TASK_FIRST/LAST/STEPSIZE` | `SLURM_ARRAY_TASK_MIN/MAX/STEP/COUNT` |
| Interactive | arrays are qsub-only | arrays are sbatch-only (job_array.shtml) |

job_array.shtml 23.11 to 25.11: the first task is a place holder, "causing
it to be the last to run. As a result, the task with the lowest
SLURM_JOB_ID will have the highest SLURM_ARRAY_TASK_ID." Ids do not follow
index order there. The 22.05 page has no such text and its example shows
ids in index order (job 36 is task 1).

### Dependencies

| | SGE | Slurm |
|---|---|---|
| Option | `-hold_jid id,name,...` | `-d type:id[:id][,type:id]` (`?` for OR) |
| Semantics | waits until all have "completed"; a parent exiting 100 keeps it ineligible (submit(1) `-hold_jid`) | `after`, `afterany` (default), `afterok`, `afternotok`, `aftercorr`, `afterburstbuffer`, `singleton` (sbatch(1) `-d`) |
| Array element-wise | `-hold_jid_ad` (corresponding sub-tasks "have completed") | `aftercorr` (corresponding task "has completed successfully"; not the same condition) |
| Unsatisfiable | no ok/notok option; a parent exiting 100 leaves the job ineligible (row above) | stays PENDING with reason `DependencyNeverSatisfied`, or cancelled with `--kill-on-invalid-dep=yes` / `kill_invalid_depend` (sbatch(1)) |
| Parent already gone | names "are resolved at submit time" (submit(1) `-hold_jid`); nothing found in submit(1) on a numeric id that no longer exists | `afterok`/`afternotok` need the parent "still active or within MinJobAge seconds after the specified job has ended"; else rejected if the missing job "is on the same cluster as the job submission" (sbatch(1) `-d`, 23.11 to 25.11; the sentence is not in sbatch(1) 22.05) |

`MinJobAge` defaults to 300 s (slurm.conf(5)). hpc-runner's D8 emulation
(sentinel files, exit 113) exists only because SGE lacks `afterok`.

### Output files

| | SGE | Slurm |
|---|---|---|
| Default | `<name>.o<id>` and `<name>.e<id>` (`.<task>` for arrays), separate (submit(1) `-o`, `-e`) | one file `slurm-%j.out`, arrays `slurm-%A_%a.out`, stdout and stderr together (sbatch(1) `-o`) |
| Merge | `-j y` | default; `-e` separates |
| Where | relative paths: cwd with `-cwd`, else `$HOME` (submit(1) `-e`) | relative to the working directory (`src/sbatch/opt.c:_opt_verify` makes them absolute against `--chdir`) |
| Placeholders | `$JOB_ID`, `$JOB_NAME`, `$TASK_ID`, `$USER`, `$HOME`, `$HOSTNAME` | `%j %A %a %x %u %N %t %s`, zero padding `%4j` (sbatch(1) FILENAME PATTERN) |
| Interactive | qrsh ignores `-o/-e` | srun accepts `-o/-e` except with `--pty` |

When the output directory does not exist, Slurm's behaviour depends on the
version (read from source, not run). 22.05: the open fails with "Could not
open stdout file" and the step launch returns `ESLURMD_IO_ERROR`
(`slurm-22-05-11-1:src/slurmd/slurmstepd/io.c:1036-1045`, `mgr.c:453-454`).
23.02 and later: slurmstepd creates the directory path (mode 0755) and
retries (CL 23.02: "Automatically create directories for stdout/stderr
output files."; `src/slurmd/slurmstepd/io.c:1093-1119`). SGE 8.1.9 does
not create it either: the job goes to `Eqw` with "can't open output file
... No such file or directory" and qacct `failed 26 : opening input/output
file` (testbed run E8). The repo's configuration.rst says SGE discards
output in that case; the run does not show that.

### Working directory

- SGE: `-cwd` or `-wd dir`; without either the job runs from the home
  directory (+dfsg-17 `exec_job.c:609-613`, comment "if cwd not set in job:
  take users home dir"; `builtin_starter.c:745-750`; read from source, not
  run). The man pages have no explicit sentence.
- Slurm: sbatch and srun default to the caller's cwd
  (`slurm_opt.c:arg_reset_chdir`); `-D` overrides. If the directory cannot
  be entered on the node, slurmstepd logs "couldn't chdir to ...: going to
  /tmp instead" and continues in `/tmp`
  (`src/slurmd/slurmstepd/mgr.c:_pre_task_child_privileged`). Container
  jobs are the exception: they fail instead (`mgr.c:1874-1879`).

### Shell selection

- SGE: under the default `shell_start_mode posix_compliant` the `#!` line is
  ignored; the shell is `-S` or the queue's `shell` (default `/bin/sh`)
  (sge_conf(5), queue_conf(5) `shell`), started as a login shell if listed
  in `login_shells` (§2.1). `unix_behavior` honours the shebang instead.
  `shell_start_mode` is also a per-queue parameter (queue_conf(5)); the
  repo's testbed queues set `unix_behavior`
  (`testbed/sge/soge/cell/q-all.conf:20`) under a global `posix_compliant`.
- Slurm: sbatch refuses a script without a shebang (three error lines:
  "This does not look like a batch script.  The first", "line must start
  with #! followed by the path to an interpreter.", "For instance:
  #!/bin/sh"; `src/sbatch/sbatch.c:_get_script_buffer`); the script is
  stored and
  `execve`'d (`src/slurmd/slurmstepd/task.c:exec_task`), so the kernel runs
  the shebang interpreter. It is not a login shell unless the shebang asks
  (`#!/bin/bash -l`) (**UNVERIFIED**: no Slurm text or code read says "login
  shell"; no bash documentation was opened). `--wrap` wraps a command in an
  `sh` script (sbatch(1) `--wrap`). sbatch has no option that selects the
  shell; its `-S` is `--core-spec` (sbatch(1)).
- Resource limits: Slurm propagates the submitting shell's soft rlimits by
  default (`PropagateResourceLimits`, default ALL; `--propagate`,
  slurm.conf(5), sbatch(1)); SGE applies the queue's limits
  (queue_conf(5) RESOURCE LIMITS).

**Consequence for a front end.** Slurm needs explicit `-o` (and `-e`) if
SGE-like separate files are wanted, `--chdir` is already the default
behaviour of `-cwd`, the shell comes from the generated script's shebang
rather than `-S`, and whether that shell is a login shell is decided by the
shebang. Array indices may start at 0 on Slurm only.

---

## 7. Status and accounting (brief)

| | SGE | Slurm |
|---|---|---|
| Active jobs | `qstat` (`-j id` detail, `-xml`) | `squeue` (pending, running, completing by default; `-t all` adds recent finished, squeue(1) `-t`) |
| Just finished, in memory | `qstat -s z`: last `finished_jobs` (default 100, deprecated) jobs, lost on qmaster restart (sge_conf(5)) | `scontrol show job` and `squeue -t all` until `MinJobAge` (default 300 s); "it can take longer than MinJobAge seconds to purge a job" (slurm.conf(5); scontrol(1) and squeue(1) do not name `MinJobAge` for this) |
| History | `qacct -j` reads the accounting file, flushed every `accounting_flush_time`, else `flush_time` (default 15 s) (sge_conf(5); accounting(5)) | `sacct` via slurmdbd; default start is 00:00:00 of the current day, unless `-s` is used (then 'now') or `-j` (then Unix epoch 0) (sacct(1) `-S`) |
| Granularity | one line per job; more when a job is started several times, and for PE slave tasks depending on `accounting_summary` (accounting(5)) | job, `.batch`, `.extern` (only with `PrologFlags=contain`), and one row per step; `-X` for the allocation only (job_launch.shtml; sacct(1) `-X`) |
| Exit code | `exit_status` (128+signal when killed) plus `failed` for system-level failures (accounting(5)) | `ExitCode` as `code:signal`, plus `DerivedExitCode` = highest step code (job_exit_code.shtml) |
| Non-zero exit | by default 99 reschedules the job and 100 sets it to the error state ("Without this parameter, jobs returning the value 99 are rescheduled."; "Without this parameter jobs returning the value 100 are set to the error state", sge_conf(5)); other codes: no state change (testbed run E9: exit 3 finishes with `exit_status 3`, `failed 0`; 99 gives `Rq`, 100 gives `Eqw`) | job state FAILED, reason `NonZeroExitCode` (job_exit_code.shtml) |

The visibility gap D19 describes on SGE (left qstat, not yet in qacct) has a
Slurm counterpart: a job is in the controller until `MinJobAge` and in
slurmdbd once the controller has sent the record. **UNVERIFIED**: whether
there is a gap between the two. The purge test
(`src/slurmctld/job_mgr.c:_list_find_job_old`) does not look at whether the
accounting record was delivered; no documentation or code statement about an
actual gap was found, and nothing was run.

**Consequence for a front end.** On Slurm an id may mean an array task with
its own id, a step's state is separate from the job's, and the default
`sacct` time window must be widened (or `-j` used) to find older jobs.

---

## Testbed runs

### SGE 8.1.9

Run on 2026-11-14 on the `soge` testbed (Son of Grid Engine 8.1.9, Debian
trixie; `smp` PE with `allocation_rule $pe_slots`; `all.q` with
`shell_start_mode unix_behavior`). Scripts and full output:
`runs/sge-soge-2026-11-14-exp*.{sh,out}`. One cluster, one configuration:
a result holds for this setup.

| Run | Question | Result |
|---|---|---|
| E1 | slots without `-pe` | `NSLOTS=1`, qacct `slots 1` |
| E2 | `-pe smp 2` | 2 slots on one host (this PE is `$pe_slots`) |
| E3 | default working directory and `PATH` | home directory; `PATH=/tmp/<job>.1.all.q:/usr/local/bin:/bin:/usr/bin` (the man page omits the tmpdir prefix) |
| E4 | `PATH` under `-V` | `<tmpdir>:<caller's PATH>` |
| E5 | `-v SGE_TASK_ID=77,JOB_ID=5` | ignored: job sees its own `JOB_ID`, `SGE_TASK_ID=undefined` (the man page says they can be changed) |
| E6 | `-V` with `JOB_ID=5 SGE_TASK_ID=9` in the caller | same: both overwritten |
| E16 | non-array job submitted with `-V` from an array task | child has `SGE_TASK_ID=undefined`; its `PATH` gains the parent's tmpdir as well |
| F1 | bash functions through `-V` | not carried; multi-line plain variable carried |
| E8 | `-o` into a missing directory | job goes to `Eqw`: "can't open output file ... No such file or directory"; qacct `failed 26 : opening input/output file`; the directory is not created |
| E9 | exit 3 / 99 / 100 | 3: finished, `exit_status 3`, `failed 0`. 99: requeued (`Rq`, `failed 25 : rescheduling`). 100: `Eqw` (`failed 30 : application error returned`) |
| E10 | `-l nosuch=1` | rejected at submit: `unknown resource "nosuch"`, exit 1 |
| F2 | `-l 'hostname=!exec1'` | 5 of 5 on exec2; `!exec2`: 5 of 5 on exec1 |
| G2 | BOOL complex set `TRUE` on exec1 only | `=1`, `=TRUE`, `=true`: 3 of 3 on exec1 each. `=0`, `=FALSE`, `=2`: stay `qw`. `=yes`: rejected, "is not a boolean value" |
| G1 | consumable `gpu` (default 0) with no `complex_values` anywhere | `-l gpu=1`: 3 of 3 ran. `-l gpu=5`: stayed `qw`. Cause not traced |
| E13 | qrsh exit codes | `exit 7`: 7. `kill -9 $$`: 137. `kill -15 $$`: 143. Unknown queue or resource: 1 |
| E14 | qrsh without a command | `-cwd`: "Unknown option -wd", exit 1. `-V`: "Unknown option -V", exit 1 |
| E15 | qrsh with a command | stdout and stderr both reach the terminal; default directory is home; `-cwd` honoured |
| F4 | qrsh client killed with INT, HUP, TERM while the command runs | each time the remote job is killed (qacct `exit_status 137`, `failed 100 : assumedly after job`) and the client exits 0 |
| F5 | login shell | batch job and qrsh command: not a login shell (`unix_behavior`) |
| E17 | queue without `-q` | 3 of 3 in `all.q` (says little: two queues, same hosts) |

### Slurm 24.11

Run on 2026-11-14 on the `24.11` testbed (Debian slurm-wlm 24.11.5;
`proctrack/linuxproc`, `task/none`, no cgroups, slurmdbd on MariaDB, no
`AccountingStorageEnforce`, no `PrologFlags`; exec1 has `Feature=fast`,
`Gres=gpu:1`; both nodes `TmpDisk=1000`; `Licenses=lic:2`). Scripts and
full output: `runs/slurm-24.11-2026-11-14-exp*.{sh,out}`. The same scripts
were run on 22.05; differences are in the next section.

| Run | Question | Result |
|---|---|---|
| S1 | plain `sbatch` | runs in the submit directory; `SLURM_CPUS_PER_TASK` unset; not a login shell; caller's variables present; sacct rows `N` and `N.batch` only (no `extern`) |
| S2 | `-c 2` without `-n` | `NumTasks=1 NumCPUs=2 CPUs/Task=2`; `SLURM_CPUS_PER_TASK=2` |
| S3 | `--export=NONE` | caller's variables absent; `PATH`, `HOME`, `USER` set from the login environment; `module` function present (this image's login profile defines it) |
| S4 | `--export=MARK` (a list without `ALL`) | `MARK` carried, and the login environment is loaded as for `NONE` |
| S5 | `--export=ALL,VAR2=x` with `VAR2=caller` in the caller | job sees `VAR2=x`: the explicit value wins (sbatch(1) says the caller's wins) |
| S6 | exported bash function | carried by the default `--export=ALL`; not carried by `--export=NONE` |
| S7 | parent `-c 2` submits a child without `-c` | child has `SLURM_CPUS_PER_TASK=1` |
| S8 | `-o` into a missing directory | directory created, job COMPLETED |
| S9 | batch exit codes in sacct | `exit 3`: FAILED `3:0`. `kill -9 $$`: job FAILED `0:9`, `.batch` CANCELLED `0:9`. `kill -15 $$`: FAILED `0:15` |
| S10 | `srun` | stdout and stderr reach the terminal; exit 7 gives 7; SIGKILL 137; SIGTERM 143; nothing left in `squeue` afterwards |
| S11 | `srun` failures | unknown partition: exit 1; with `SLURM_EXIT_ERROR=112`: exit 112; a command exiting 5 still gives 5. `-A nosuch` is accepted here (no enforcement) |
| S12 | `srun --export=NONE` | `HOME` and `USER` empty, `PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin:.`, no `module` function |
| S13 | `srun` client signalled | INT twice: srun exits 130, job CANCELLED. HUP or TERM once: srun exits 137, job FAILED `0:9` |
| S14 | rejected at submit | unknown partition, GRES name, feature, licence; licence count above the total; `--gres=gpu:5`, `--tmp=5000`, `--mem=999999` (no node can satisfy); `afterok` on an unknown job id. `-A nosuch` accepted |
| S15 | accepted requests | `-C fast` and `--gres=gpu:1` run on exec1; `--mem=1G` is recorded as `1G`, `--mem=1000` as `1000M` |
| S16 | `--tmp` | two jobs on exec1 at once, each `--tmp=800` of `TmpDisk=1000`: both RUNNING. A filter, nothing is booked |
| S17 | licences | three jobs with `-L lic:1`, total 2: two RUNNING, third PENDING reason `Licenses` |
| S18 | `SBATCH_PARTITION` | beats a `#SBATCH -p` directive; loses to `-p` on the command line |
| T1, U1-U3 | sacct row at submit | held jobs: present at the first query, 5 of 5 and again at +4 ms. One job of three in a later run was absent at +4 ms and present at +112 ms (`runs/slurm-24.11-2026-11-15-exp3.out`) |
| T2 | controller to sacct delay at job end | both showed COMPLETED within the same 50 ms poll, 5 of 5 |
| T3, T4 | state names | squeue `%t`/`%T`: `PD`/`PENDING`, `R`/`RUNNING`, `S`/`SUSPENDED`; sacct uses the long names. Held: `PD`, reason `JobHeldUser`. After `scontrol requeue`: `PENDING`, reason `BeginTime`; `sacct -D` shows the first run as `REQUEUED` |
| T5 | `scancel` | pending or running: sacct `CANCELLED by <uid>`, `0:0`; `.batch` `CANCELLED 0:15` |
| T6 | `afterok` on a parent that failed | without the kill flag: stays `PD`, squeue reason `DependencyNeverSatisfied`, sacct `PENDING` with Reason `None`. With `--kill-on-invalid-dep=yes`: sacct `CANCELLED` `0:0` with Reason `None`; the reason is only in `scontrol show job` (`Reason=DependencyNeverSatisfied Dependency=afterok:N(failed)`) |
| T7 | dependencies on a parent that succeeds | `afterok`, `afterany`, `after` all run; `afterok` on a parent that finished seconds ago is accepted and runs |
| T8 | array `--array=1-4%2` | `sbatch --parsable` prints the base id; pending tasks are one row `64_[1-4%2]` in squeue and sacct; finished tasks are `64_1`..`64_4` with raw ids 65, 66, 67, 64 (the last task has the base id); default output `slurm-<base>_<task>.out`; a failing task is `FAILED 1:0`, the others COMPLETED |
| T9 | default output and directory | `slurm-<id>.out` in the submit directory; `--chdir` honoured |
| T10 | time limit | sacct `TIMEOUT` `0:0`; `.batch` `CANCELLED 0:15` |
| T11 | login shell | `#!/bin/bash`: not a login shell, `~/.bash_profile` not read, also under `--export=NONE`. `#!/bin/bash -l`: login shell, profile read |
| T12 | `srun` inside `sbatch -c 2` | the step has `SLURM_CPUS_PER_TASK=2` |
| T13 | `--pty` | with a terminal: the command has a pty. Without: "Not using a pseudo-terminal, disregarding --pty option", command runs, exit 0 |
| T14 | whitespace in a directive | `#SBATCH --job-name=two words`: rejected, "Invalid directive found in batch script: words", exit 255. Quoted: accepted, name `two words` |
| T15 | finished and unknown ids | `scontrol show job` works for a job that just finished; unknown id: "Invalid job id specified", exit 1. `sacct -j <unknown>`: empty output, exit 0 |

### Slurm 22.05

Run on 2026-11-15 on the `22.05` testbed (Debian slurm-wlm 22.05.8, same
`slurm.conf`; `CgroupPlugin=cgroup/v1` because 22.05 has no `disabled`).
Output: `runs/slurm-22.05-2026-11-15-exp*.out`. Everything not listed here
matched 24.11.

| Run | 24.11.5 | 22.05.8 |
|---|---|---|
| U1-U3: sacct row after submit | there within about 0.1 s | absent for 0.65 to 1.0 s. For a job that starts at once the first row is a stub for about 0.2 s: JobName `allocation`, Partition empty, ReqMem `16?`, State and ExitCode already right |
| S8: `-o` into a missing directory | directory created | job FAILED `0:53`, `.batch` CANCELLED `0:53`, directory not created |
| S14: `-d afterok:<unknown id>` | rejected, "Job dependency problem" | accepted |
| T6: sacct Reason of a dependent whose parent failed | `None` (pending and cancelled forms) | `Dependency` (both forms; not `DependencyNeverSatisfied`) |
| T4: sacct State after `scontrol requeue` | `PENDING` | `REQUEUED` |
| T8: array `1-4%2` in squeue two seconds after submit | one row `N_[1-4%2]` PENDING | `N_1`, `N_2` RUNNING and `N_[3-4%2]` PENDING reason `JobArrayTaskLimit` (timing, not necessarily a version difference) |
| `--export=NONE` in an exec container without `SYS_ADMIN` | slurmd exits | works |
| S2: `scontrol show job` | `ReqTRES=` and `AllocTRES=` | one `TRES=` line |

A fully unprivileged exec container cannot run `--export=NONE`: slurmd
24.11.5 exits with "fatal: clone: Operation not permitted" at the first
such job and the node goes down
(`runs/slurm-24.11-2026-11-14-exp1-unprivileged.out`). With `cap_add:
SYS_ADMIN` on the exec containers it works.

## Unverified

Still unverified after the 2026-11-13 check. Each item is marked
**UNVERIFIED** in place, with what was searched.

Accepted by the user on 2026-11-15: every item in this list is closed as
it stands. None changes how hpc-runner is built: each is a corner case,
is out of scope by a line in `PLAN.md`, or depends on the site. The one
that touches the design (a job found in neither the controller nor
slurmdbd) is covered in `PLAN.md`, "Status and exit codes": a missing
job is UNKNOWN and the wait loop waits through it. Ctrl-C in an interactive job: killing the client ends the
job on both schedulers (runs F4 and S13, "Testbed runs"), which is all
that is needed.

- SGE: which sense of `accounting_summary` produces per-task accounting
  records; accounting(5) and sge_pe(5) state it in opposite senses (§1).
- Slurm: what "--get-user-env will be ignored" in sbatch(1) 22.05 refers to
  (§2.2).
- That a slow login profile matters on Slurm in a way it does not for an
  SGE `-V` job (§2.3).
- The stale-`SLURM_*` effects in §2.7 are derived from source code, not
  from SchedMD documentation, and were not run; the Pawsey site page cited
  for the `SLURM_MEM_*` conflict was not opened.
- Whether a compute node's `su -` login sources the Modules init, so that
  the `module` function is in the `--export=NONE` login output (§2.9).
- That `module purge` is what makes inherited `LOADEDMODULES`/`_LMFILES_`
  harmless under D3 on SGE (§2.9).
- qrsh: the exit code when the connection is lost mid-job, and for an
  out-of-memory kill; Ctrl-C from a real terminal; the rsh/ssh startup
  methods (§3.1, §3.3).
- srun `--pty`: that Ctrl-C reaches the remote pty and becomes SIGINT there
  (§3.4).
- That the plain-DISPLAY path works the same on both schedulers (§3.5).
- Whether common VNC/ThinLinc/ETX servers listen on TCP and create xauth
  cookies by default (§3.5).
- SGE: that a non-consumable `-l` request is not multiplied by slots
  (§4.3).
- SGE: that it does not talk to a licence server by itself; Slurm: an
  `scontrol` update of a licence count (§4.3).
- SGE: that without `-q` a job goes to any matching queue; that the project
  is the grouping for share/limits; that the account string carries no
  policy; that there is no equivalent of QOS (§5).
- That hpc-runner's `queue` maps to a partition and `project` to the Slurm
  account, and that SGE's account string has no separate Slurm field (§5).
- Whether a Slurm job can be absent from both the controller and slurmdbd
  for a short time after `MinJobAge` (§7).
- OCS: only the D18 differences are known; qsub/qrsh environment and
  interactive behaviour were not compared against OCS 9 documentation.
