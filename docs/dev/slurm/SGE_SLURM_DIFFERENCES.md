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
single page covering qsub, qrsh, qsh, qlogin, qalter), **sge_conf(5)**,
**queue_conf(5)**, **complex(5)**, **sge_pe(5)**, **accounting(5)**,
**sge_request(5)**. Open Cluster Scheduler (OCS) pages were not compared line
by line; OCS differences are only those already recorded in the repo (D18).

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

Anything not confirmed from a primary source is marked **UNCONFIRMED**.
Output blocks marked "sketch" are illustrations of documented behaviour, not
captured output.

---

## 1. Mental model

### SGE

A job is one script (or binary with `-b y`) bound to a set of slots. With no
`-pe` it gets one slot in one queue instance; with `-pe <pe> N` it gets N
slots spread over hosts according to the PE's `allocation_rule`
(`$pe_slots` = all on one host, an integer = that many per host,
`$fill_up`, `$round_robin`; sge_pe(5) `allocation_rule`). The script runs
once, on the master host; anything on the other hosts is started by the
script (via `qrsh -inherit` under tight integration, `control_slaves TRUE`,
sge_pe(5)). qsub, qrsh, qsh and qlogin are four front ends to the same
thing: a job. `qrsh` is an interactive job, not a sub-unit of another one.

### Slurm

Slurm separates three things:

| Object | What it is | Created by |
|---|---|---|
| **Job (allocation)** | A set of nodes/CPUs/memory/GRES held for a time | `sbatch`, `salloc`, or `srun` when not already inside one |
| **Job step** | One launch of processes inside an allocation, with its own id (`123.0`, `123.1`), resources, accounting and exit code | `srun` (and implicitly: the `batch` step, the `extern` step) |
| **Task** | One process of a step (an MPI rank); `-n` counts tasks | `srun` launches N tasks of a step |

What each command creates:

- **sbatch**: an allocation, then "a single copy of the batch script on the
  first node in the set of allocated nodes" (sbatch(1) 25.11 DESCRIPTION).
  The script runs as the special `batch` step. It is not "the job" in the SGE
  sense; it is one step that happens to run first.
- **srun** outside any allocation: creates an allocation *and* one step in
  it, waits, and releases the allocation when the step ends (srun(1)
  DESCRIPTION: "If necessary, srun will first create a resource allocation").
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
`--overlap`). Since 22.05 `--cpus-per-task` on srun implies `--exact`
(RN 23.11, quoted in §2.8).

### Worked example

Same request, 4 CPUs, a script that runs a tool:

```bash
# SGE: one job, one script, 4 slots on one host
qsub -pe smp 4 -l h_rt=1:00:00 -cwd -V run.sh

# Slurm: one allocation with one task of 4 CPUs; the script is step "batch"
sbatch -n 1 -c 4 -t 1:00:00 run.sh
```

If `run.sh` calls `srun tool` twice, Slurm records four rows (sketch of
`sacct -j 123 -o JobID,JobName,State,ExitCode`):

```
JobID         JobName   State      ExitCode
123           run.sh    COMPLETED  0:0
123.batch     batch     COMPLETED  0:0
123.extern    extern    COMPLETED  0:0
123.0         tool      COMPLETED  0:0
123.1         tool      FAILED     1:0
```

The job's own exit code is the batch script's; each srun's is on its step
(job_exit_code.shtml "Job Step Exit Codes"; the derived exit code is the
highest step exit code, ibid. "Derived Exit Code"). SGE has no equivalent
rows: what the script runs is invisible to qacct unless it is a `qrsh
-inherit` task of a tightly integrated PE.

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

**`-v var[=value],...`**: adds named variables, with the submitter's value
if no value is given (submit(1) `-v`; qsub, qrsh with command, qalter).

**No `-V`**: the job does not start from an empty environment. It gets:

1. What SGE sets (submit(1) ENVIRONMENT VARIABLES): `HOME`, `USER`,
   `LOGNAME`, `SHELL` (from passwd; "not necessarily the shell in use"),
   `PATH=/usr/local/bin:/usr/ucb:/bin:/usr/bin`, `HOSTNAME`, `TMPDIR`, `TMP`,
   `TZ` (from execd), `ENVIRONMENT=BATCH`, `JOB_ID`, `JOB_NAME`, `JOB_SCRIPT`,
   `REQUEST`, `QUEUE`, `NSLOTS`, `NHOSTS`, `NQUEUES`, `PE`, `PE_HOSTFILE`,
   `RESTARTED`, `ARC`/`SGE_ARCH`, `SGE_BINARY_PATH`, `SGE_CWD_PATH`,
   `SGE_STDOUT_PATH`/`SGE_STDERR_PATH`/`SGE_STDIN_PATH`,
   `SGE_JOB_SPOOL_DIR`, `SGE_BINDING`, `SGE_TASK_ID` (`undefined` for a
   non-array job) and `SGE_TASK_FIRST/LAST/STEPSIZE`, and `TERM` for
   interactive jobs only.
2. The submit-side copies `SGE_O_HOME`, `SGE_O_HOST`, `SGE_O_LOGNAME`,
   `SGE_O_MAIL`, `SGE_O_PATH`, `SGE_O_SHELL`, `SGE_O_TERM`, `SGE_O_TZ`,
   `SGE_O_WORKDIR` (ibid.).
3. **The execution daemon's own environment**, if `execd_params INHERIT_ENV`
   is true, which is the default: "any environment variable that is set in
   the shell which starts the execution daemon ... will be set in the
   environment of any jobs ... unless ... explicitly overridden, such as PATH
   or LOGNAME" (sge_conf(5) `INHERIT_ENV`).
4. Whatever the job shell's startup files add. Under the default
   `shell_start_mode posix_compliant`, a shell named in `login_shells`
   (default `sh,bash,csh,tcsh,ksh`) is started as a login shell, so
   `/etc/profile` and `~/.profile`/`~/.bash_profile` run (sge_conf(5)
   `shell_start_mode`, `login_shells`; queue_conf(5) `shell_start_mode`).

Some SGE-set variables can be overridden by `-v`/`-V`: `SGE_TASK_ID`,
`SGE_TASK_FIRST`, `SGE_TASK_LAST`, `SGE_TASK_STEPSIZE` ("It is possible to
change the predefined value of this variable with -v or -V"), while
`JOB_SCRIPT` "can not be overwritten" (submit(1) ENVIRONMENT VARIABLES).
Whether `qsub -V` run from inside an array task therefore hands the parent's
`SGE_TASK_ID` to a non-array child is **UNCONFIRMED**.

**Default request files**: `$SGE_ROOT/$SGE_CELL/common/sge_request`,
`~/.sge_request`, `./.sge_request` may contain any option, including `-V`.
Processing order is: default files, then `#$` lines, then the command line,
so "the command line can be used to override the embedded flags and the
default request settings. The embedded flags, however, will override the
default settings" (submit(1) DESCRIPTION; sge_request(5)). `-clear` discards
everything before it. Repeatable options accumulate across sources
(sge_request(5)).

**qrsh**: `-V` and `-v` apply to "qrsh with command"; `-display` to "qsh and
qrsh with a command". The `QRSH_WRAPPER` paragraph of submit(1) DESCRIPTION
also says "The options -cwd, -v, -V, and -display only apply to batch jobs",
which contradicts the per-option text; how that sentence is meant is
**UNCONFIRMED**. (hpc-runner's `tests/integration/test_environment.py`
checks the environment of batch jobs only.)

### 2.2 Slurm `--export` (sbatch)

sbatch(1) 25.11 `--export={[ALL,]<vars>|ALL|NIL|NONE}`; "SLURM_* variables
are always propagated".

| Mode | What sbatch sends | Login env loaded on the node? |
|---|---|---|
| (unset) / `ALL` | All of sbatch's environment | No (unless `--get-user-env`) |
| `NONE` | Only `SLURM_*` and SPANK option variables | **Yes**, implicitly, "as if --get-user-env was specified" |
| `NIL` (23.02+) | Only `SLURM_*` and SPANK option variables | No |
| `VAR1,VAR2=x` | `SLURM_*`, SPANK, and the named variables | **Yes**, implicitly |
| `ALL,VAR2=x` | All, plus VAR2 only if the caller does not already have it ("all user environment variables ... will take precedence over any explicitly given") | No |

Source, `src/sbatch/sbatch.c:_fill_job_desc_from_opts`: `ALL` merges
`environ`; `NIL` merges only SLURM/SPANK; `NONE` merges only SLURM/SPANK and
sets `get_user_env = true`; any other list goes through `env_merge_filter`
and sets `get_user_env = true`. When true, sbatch adds
`SLURM_GET_USER_ENV=1` to the job's environment.

**22.05 wording versus 22.05 behaviour.** sbatch(1) 22.05 says of `NONE`
"--get-user-env will be ignored". The 22.05 code
(`slurm-22-05-11-1:src/sbatch/sbatch.c:_fill_job_desc_from_opts`) sets
`opt.get_user_env_time = 0` for `NONE` and for explicit lists and then
`SLURM_GET_USER_ENV=1` whenever that is `>= 0`. So the login environment is
loaded in 22.05 too; "ignored" refers to the user's own `--get-user-env`
timeout/mode. 23.11+ man pages describe this correctly. `NIL` (no login env)
appeared in 23.02 (CL 23.02 "sbatch - Added new --export=NIL option").

### 2.3 What "the user's login environment" means mechanically

On the first node, before the batch step starts, slurmd
(`src/slurmd/slurmd/req.c:_get_user_env`) sees `SLURM_GET_USER_ENV=1` and
calls `env_array_user_default()` (`src/common/env.c`), which, as root:

```
su - <user> -c '/bin/echo; ...; /bin/echo XXXXSLURMSTARTPARSINGHEREXXXX;
                <slurmstepd getenv | /bin/env>; /bin/echo XXXXSLURMSTOPPARSINGHEREXXXXX'
```

(`_child_fn`: `execle(SUCMD, "su", "-", username, "-c", cmdstr, ...)`, or
without `-` if Slurm was built with `LOAD_ENV_NO_LOGIN`). It runs in new PID
and mount namespaces when the kernel allows it, falling back to plain fork
(24.11 change, CL 24.11). The text between the markers is parsed into
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
`S`/`L` mode (sbatch(1) 22.05 `--get-user-env[=timeout][mode]`). 25.05
deprecated both and set the default timeout to 120 s (CL 25.05: "Deprecate
GetEnvTimeout ... default timeout ... will be 120 seconds"); sbatch(1) 25.05+
states "If the user environment retrieval fails or times out, the job will
be aborted, requeued and held". A slow or interactive `~/.bash_profile`
therefore matters on Slurm in a way it does not for an SGE `-V` job.
Whether exported shell functions (`BASH_FUNC_module%%`, multi-line values)
survive this `env`-output parsing is **UNCONFIRMED**.

`--get-user-env` exists only on sbatch; it was removed from salloc in 24.11
(RN 24.11 "Remove salloc --get-user-env"), and srun never had it.

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
script file").

### 2.5 What Slurm adds to the job

Batch script (sbatch(1) OUTPUT ENVIRONMENT VARIABLES): `SLURM_JOB_ID`
(and legacy `SLURM_JOBID`), `SLURM_JOB_NAME`, `SLURM_JOB_NODELIST`,
`SLURM_JOB_NUM_NODES`, `SLURM_JOB_PARTITION`, `SLURM_JOB_ACCOUNT`,
`SLURM_JOB_QOS`, `SLURM_CPUS_ON_NODE`, `SLURM_JOB_CPUS_PER_NODE`,
`SLURM_NTASKS` (when requested), `SLURM_CPUS_PER_TASK` (only when `-c`
given), `SLURM_MEM_PER_NODE` or `SLURM_MEM_PER_CPU` (one of them),
`SLURM_TASKS_PER_NODE`, `SLURM_SUBMIT_DIR`, `SLURM_SUBMIT_HOST`,
`SLURM_CLUSTER_NAME`, `SLURM_ARRAY_*` for arrays, `SLURM_EXPORT_ENV`
("Same as --export"), and more. The SGE `SGE_O_*` family has no Slurm
equivalent beyond `SLURM_SUBMIT_DIR`/`SLURM_SUBMIT_HOST`.

Several of these **output** variables are also srun **input** variables
(`SLURM_EXPORT_ENV`, `SLURM_MEM_PER_CPU`, `SLURM_MEM_PER_NODE`,
`SLURM_CPUS_PER_TASK`, `SLURM_NTASKS`, `SLURM_JOB_ID`; srun(1) INPUT
ENVIRONMENT VARIABLES). This is how an `srun` inside the script inherits
the job's shape, and it is the root of the surprises in §2.6 and §2.7.

### 2.6 Steps: `SLURM_EXPORT_ENV`

`sbatch --export=X` (X not `ALL`) does `setenv("SLURM_EXPORT_ENV", X, 0)`
(`src/sbatch/opt.c:_opt_verify`, comment "srun ignores ALL, it is the
default"). That lands in the batch script's environment, and every `srun`
inside reads it as its own `--export`.

srun's `NONE` is not sbatch's `NONE`: "None of the user environment will be
defined" and no login environment is fetched (srun(1) 25.11 `--export`;
`src/srun/launch.c:_build_user_env` merges only SLURM/SPANK variables for
`NONE`). Worked example:

```bash
#!/bin/bash
#SBATCH --export=NONE
module load xcelium/23.09     # sets PATH etc. in the batch shell
srun xrun -version            # step started with SLURM_EXPORT_ENV=NONE:
                              # the module's PATH is not in the step
```

Both sbatch(1) and srun(1) say: "To avoid steps inheriting environment
export settings (e.g. "NONE") from sbatch command, the environment variable
SLURM_EXPORT_ENV should be set to "ALL" in the job script."

### 2.7 Running sbatch or srun from inside a job

**srun inside any allocation** (batch script, salloc shell, an `srun --pty
bash`): `SLURM_JOB_ID` is set, so srun creates a step in the enclosing
allocation rather than a new job. To get a new job from inside one, the
caller must remove `SLURM_JOB_ID` (srun(1) `--jobid`, `SLURM_JOB_ID`).
`SLURM_JOB_NAME` is ignored inside an allocation (srun(1) INPUT ENVIRONMENT
VARIABLES).

**sbatch inside a job** always creates a new job; it does not read
`SLURM_JOB_ID`. Its own input variables are `SBATCH_*` plus `SLURM_CLUSTERS`
and `SLURM_HINT` (`src/sbatch/opt.c` env table). But with the default
`--export=ALL` the new job receives the parent's entire environment,
**including the parent's `SLURM_*` variables**. When the child starts, slurmd
overwrites the `SLURM_*` variables it sets for the child
(`env.c:env_array_for_batch_job`) and leaves the rest. No code removes stale
ones (no general `SLURM_*` scrubbing in `src/sbatch`, `src/slurmd` or
`env.c`; only `SLURM_STEPMGR` is unset by sbatch). Derived from the source,
not from SchedMD documentation:

- `env_array_for_batch_job` sets **either** `SLURM_MEM_PER_CPU` **or**
  `SLURM_MEM_PER_NODE`. A parent submitted with `--mem-per-cpu` that
  submits a child with `--mem` gives the child both. An `srun` in the
  child then dies in `src/common/slurm_opt.c:_validate_memory_options`:
  `fatal("SLURM_MEM_PER_CPU, SLURM_MEM_PER_GPU, and SLURM_MEM_PER_NODE are mutually exclusive.")`.
  A command-line `--mem*` on that srun resets the others and avoids the
  error. (A site page, Pawsey "srun Fails When Submitting From One Cluster
  to Another", describes the same failure and the `unset` fix; secondary.)
- `SLURM_CPUS_PER_TASK` is set only when the child used `-c`; otherwise the
  parent's value survives, and srun in 23.11+ reads it.
- `SLURM_EXPORT_ENV` from the parent survives (sbatch's `setenv(..., 0)`
  does not overwrite), so a parent submitted with `--export=NONE` makes the
  child's sruns use `NONE` even if the child was submitted with the
  default.
- `SLURM_ARRAY_*` from an array parent survive into a non-array child.

The SGE analogue is narrower: with `-V`, `JOB_ID` and friends are reset by
the execd, and only the `SGE_TASK_*` override in §2.1 is documented.

### 2.8 Version differences that touch the environment

| Version | Change | Source |
|---|---|---|
| 22.05 | srun stops reading `SLURM_CPUS_PER_TASK`; use `SRUN_CPUS_PER_TASK` | RN 22.05; CL 22.05 |
| 22.05 | `--cpus-per-task` on srun implies `--exact` | RN 23.11 |
| 23.02 | `sbatch --export=NIL` added | CL 23.02 |
| 23.11 | srun reads `SLURM_CPUS_PER_TASK` again, `SRUN_CPUS_PER_TASK` removed | RN 23.11, CL 23.11 |
| 23.11 | SPANK option variables kept under `NIL`/`NONE` | RN 23.11 |
| 24.11 | `salloc --get-user-env` removed; get-user-env uses PID/mount namespaces with fallback | RN 24.11, CL 24.11 |
| 25.05 | `--get-user-env=timeout/mode` and `GetEnvTimeout` deprecated; 120 s default; failure requeues and holds | CL 25.05, sbatch(1) 25.05 |

So a batch script that relies on `srun` picking up `-c` from the job behaves
differently on 22.05/23.02 than on 23.11+.

### 2.9 Environment Modules across the boundary

Environment Modules keeps its state in ordinary environment variables:
`LOADEDMODULES` ("A colon separated list of all loaded modulefiles"),
`_LMFILES_` ("the full pathname for all loaded modulefiles"), `MODULEPATH`,
`MODULESHOME`, and the `__MODULES_*` family (`__MODULES_LMINIT`,
`__MODULES_LMCONFLICT`, `__MODULES_LMPREREQ`, `__MODULES_LMTAG`,
`__MODULES_SHARE_<VAR>` reference counters) (module(1), envmodules
`doc/source/module.rst`, ENVIRONMENT). Initialisation behaves differently
depending on whether that state is present: "if the Modules environment is
found undefined (when both MODULEPATH and LOADEDMODULES are found either
unset or empty)" it is initialised; "if the Modules environment is found
defined a module refresh is automatically applied" (ibid.). The `module`
command itself is a shell function or alias, defined by the init script;
`MODULES_SET_SHELL_STARTUP` exists "to ensure that the module command is
still defined in sub-shells" by setting `ENV`/`BASH_ENV` (ibid.).

What crosses into the job:

| Submission | Module state variables | `module` function |
|---|---|---|
| SGE `-V` | carried | only if the job shell defines it (login shell profile, or the script sources the init file) |
| SGE no `-V` | not carried; login shell profile may load defaults | as above |
| Slurm `--export=ALL` | carried | as above; exported `BASH_FUNC_*` variables are in `environ` and are copied (sbatch merges all of `environ`) |
| Slurm `--export=NONE` | whatever `su -` login on the node produced | **UNCONFIRMED** (see §2.3) |
| srun step under `SLURM_EXPORT_ENV=NONE` | none | none |

hpc-runner's script sources the module init file and runs `module purge`
before loading (`_environment.sh.j2`), which is what makes inherited
`LOADEDMODULES`/`_LMFILES_` harmless under D3 on SGE. The same template
under Slurm `--export=ALL` sees the same inherited state.

**Consequence for a front end.** `-V` maps most directly to the Slurm
default (`--export=ALL`); `inherit_env = false` has three Slurm candidates
with different results (`NONE` = login env fetched by `su -` on the node,
which can time out; `NIL` = nothing, 23.02+; explicit list = login env plus
named variables). Any srun the front end or the user puts inside a batch
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
  EXIT STATUS lists 0, 25 and ">0 Error occurred". How a qrsh-side failure
  (connection lost, job killed) is distinguished from the command's own code
  is **UNCONFIRMED**.
- Ctrl-C and terminal loss under qrsh: **UNCONFIRMED** (not described in
  submit(1); remote_startup(5) not consulted).

### 3.2 Slurm srun, salloc

- `srun cmd` from a login node: allocation + step; blocks until resources
  are free unless `-I/--immediate[=secs]` (srun(1)). This is the analogue
  of `qrsh -now no`; `srun -I` is the analogue of `-now y`, with exit code
  `SLURM_EXIT_IMMEDIATE` (default 1, `src/srun/opt.c`).
- By default srun forwards the terminal's stdin to all tasks and streams
  their output (srun(1) `-i`). No pty unless `--pty`.
- `--pty` "Execute task zero with pseudo terminal mode", implies
  `--unbuffered`, sends other tasks' output to `/dev/null`, and is
  incompatible with `--input/--output/--error` (srun(1) 25.11 `--pty`;
  `src/srun/opt.c`). In pty mode srun puts the local terminal in raw mode
  (`src/srun/srun_pty.c:_pty_thread` calls `cfmakeraw`, which clears `ISIG`
  in `src/srun/srun.c:cfmakeraw`).
- Working directory: srun and sbatch default to the caller's cwd
  (`src/common/slurm_opt.c:arg_reset_chdir`); salloc does not set one.

### 3.3 Exit codes

| | SGE qrsh | Slurm srun |
|---|---|---|
| Command exits N | N (testbed observation, above) | "highest exit code of all tasks" (srun(1) RETURN VALUE) |
| Command killed by signal S | **UNCONFIRMED** | 128+S, highest over tasks (ibid.) |
| Out of memory | **UNCONFIRMED** | 253 is "reserved for out-of-memory errors" (srun(1) 22.05 and 25.11) |
| Launcher's own error | ">0" (submit(1) EXIT STATUS); `-now y` no resources: 1 | `SLURM_EXIT_ERROR`, default 1 (`src/srun/opt.c` `error_exit = 1`; `src/srun/srun.c` reads the variable) |
| No resources with immediate | 1 (`-now y`) | `SLURM_EXIT_IMMEDIATE`, default 1 |

By default both make a launcher failure look like a command that exited 1.
On srun the two variables let a caller choose distinct codes.

For comparison, `qsub -sync y` returns the job's code, or 1 if the job
"fails to complete successfully"; for arrays, the first non-zero task code
(submit(1) `-sync`). `sbatch --wait` returns the job's code, 1 if it ended
by a signal, and for arrays "the highest value for any task" (sbatch(1)
25.11 `-W`). `salloc` returns 1 if it cannot run the command and 0 "if
success or if killed by signals HUP, INT, KILL, or QUIT" (salloc(1) RETURN
VALUE): it does not return the command's code on those signals.

### 3.4 Ctrl-C and losing the terminal (srun)

From srun(1) SIGNALS AND ESCAPE SEQUENCES and
`src/srun/srun_job.c:_srun_signal_mgr`, `_handle_intr`:

- Without `--pty`: the first Ctrl-C prints task status ("interrupt (one
  more within 1 sec to abort)"); a second within one second sends SIGINT to
  all tasks and terminates the step; a third makes srun exit without
  waiting. With `-X/--disable-status` the first Ctrl-C is forwarded.
- With `--pty`: the terminal is raw, so Ctrl-C is a byte delivered to the
  remote pty, where the remote line discipline turns it into SIGINT for the
  foreground process (derived from `cfmakeraw`; the man page does not say).
- SIGHUP or SIGTERM to srun (terminal closed, ssh dropped, `kill`): srun
  logs "forcing job termination" and sends **SIGKILL** to the tasks
  (`_srun_signal_mgr`, `case SIGTERM: case SIGHUP:`). A GUI started by
  `srun` dies with the terminal that ran srun unless srun itself is
  detached from it.

### 3.5 GUI applications: three ways to reach an X server

The DISPLAY name says where the X client connects: `:N` is the local
socket `/tmp/.X11-unix/XN` on the host where the client runs;
`host:N` is TCP port 6000+N on `host` (X(7) "DISPLAY NAMES"). So the same
DISPLAY string means different servers on the login node and on a compute
node. submit(1) `-display`: "Local display names (e.g. :0) cannot be used in
grid environments."

**(a) Plain DISPLAY (SGE `-V`/`-v DISPLAY`; Slurm `--export=ALL` or
`--export=DISPLAY`).** The scheduler only carries the string; the
application on the compute node connects to the X server itself and must
find a matching cookie in `$XAUTHORITY` or `~/.Xauthority`. This works the
same on both schedulers.

**(b) Slurm built-in X11 forwarding, `srun --x11` / `salloc --x11`.** How
it works (`src/common/x11_util.c`, `src/slurmd/slurmstepd/x11_forwarding.c:setup_x11_forward`,
`src/slurmd/slurmd/req.c`):

1. On the submit host, srun reads `DISPLAY`. `:N` becomes the local socket
   `/tmp/.X11-unix/XN` (srun exits if it cannot stat it); `host:N` becomes
   TCP `host:6000+N` (`x11_get_display`).
2. srun runs `/usr/bin/xauth list $DISPLAY` and extracts an
   `MIT-MAGIC-COOKIE-1` (wildcard cookies accepted since 22.05, CL 22.05);
   no cookie, no xauth at `/usr/bin/xauth`, or no `DISPLAY` and srun exits
   (`x11_get_xauth`, `XAUTH_PATH` is hard-coded).
3. The target, port and cookie are stored on the job record at allocation
   (`src/slurmctld/job_mgr.c`). On each node chosen by `--x11=all|first|last`,
   the job's `extern` step listens on a local port, writes the cookie with
   `xauth` into a node-local file under `TmpFS` (or `~/.Xauthority` with
   `X11Parameters=home_xauthority`), and the step's environment gets
   `DISPLAY=localhost:<N>.0` and `XAUTHORITY=<that file>`; on failure
   `DISPLAY=SLURM_X11_SETUP_FAILED` (`req.c`).
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
  srun runs. Which port range applies (`SrunPortRange`) is **UNCONFIRMED**.
- Batch jobs cannot use it: "batch job cannot use X11 forwarding"
  (`job_mgr.c:_validate_job_desc`). It is an attribute of the allocation;
  whether `srun --x11` inside an allocation created without `--x11` errors
  or is ignored is **UNCONFIRMED**.
- Fixes relevant to EDA-style GUIs landed in 24.11/25.05: "Delay closing
  sockets in eio code which fixes issues in X11 forwarding when using
  applications such as Emacs or Matlab", "Fix x11 forwarding issues causing
  applications (e.g. matlab) to intermittently crash on startup", "Fix x11
  forwarding not working with TLS enabled" (CL 24.11, CL 25.05).

**(c) SGE has no built-in forwarding** with the builtin startup method;
`qsh` starts an xterm against `DISPLAY` or `-display` (submit(1)
DESCRIPTION), which is approach (a).

What each approach needs, by kind of DISPLAY on the login host:

| DISPLAY on login host | (a) plain DISPLAY, either scheduler | (b) `srun --x11` |
|---|---|---|
| `:5` (VNC/ThinLinc/ETX session on that login host) | Client on compute node connects to *its own* `:5`: wrong or no server | srun connects to `/tmp/.X11-unix/X5` on the login host; needs an xauth cookie for `:5` |
| `localhost:10.0` (`ssh -X`) | Compute node's own `localhost:6010`: no server | srun connects to `localhost:6010` on the login host, i.e. sshd's forward; cookie from `xauth list` |
| `loginhost:5` (TCP) | Works if the X server listens on TCP, the node can reach `loginhost:6005`, and the cookie for `loginhost:5` is readable on the node (e.g. shared `~/.Xauthority`) | srun connects to `loginhost:6005` from the login host |

Whether particular VNC/ThinLinc/ETX servers listen on TCP or create xauth
cookies by default is **UNCONFIRMED** (product-specific; not researched).

**Consequence for a front end.** `qrsh -now no` maps to blocking `srun`;
`srun --pty` changes Ctrl-C semantics and output handling. A plain-DISPLAY
GUI path behaves as on SGE and needs network-visible displays; `srun --x11`
handles local and ssh displays but depends on site build/config, an xauth
cookie, node-to-login connectivity, and the srun process staying alive. A
front end that wants the command's exit code must account for srun's (and
salloc's) own codes, and for SIGHUP killing the tasks.

---

## 4. Resources

### 4.1 SGE

- **Slots** are the CPU unit. A serial job has one; a parallel job takes N
  from a PE (`-pe smp 8`), and the PE's `allocation_rule` places them
  (sge_pe(5)).
- **`-l name=value`** requests a complex attribute. An attribute is either a
  plain requestable (matched against host/queue values, e.g. a load value
  such as `mem_free`, or a static one like `arch`) or a **consumable**,
  which SGE books and subtracts (complex(5) `consumable`).
- Consumables are **per slot** (`consumable YES`: "the limit is multiplied
  by the number of slots being used by the job") or **per job** (`JOB`:
  "debited as requested (without multiplication) from the allocated master
  queue") (complex(5)). Licences are typically global consumables in the
  `global` host's `complex_values` (ibid.).
- Enforced limits `h_vmem`/`s_vmem`, `h_cpu`: "For parallel processes, the
  limit is applied per slot" (queue_conf(5) RESOURCE LIMITS). `h_rt` is wall
  time per job.
- Which memory attribute a site uses, whether it is consumable, and per
  slot or per job, is site configuration. hpc-runner already models this:
  `memory_resource` (default `mem_free`) and `memory_per = "job"|"slot"`
  (D10).

### 4.2 Slurm

- **Nodes** `-N`, **tasks** `-n`, **CPUs per task** `-c`. "sbatch does not
  launch tasks"; `-n` advises how many tasks later steps will run
  (sbatch(1) `-n`). `-c` keeps a task's CPUs on one node (sbatch(1) `-c`).
  A threaded program is `-n 1 -c 8`; `-n 8` is eight separate tasks that
  may land on different nodes.
- **Memory**: `--mem` is "real memory required **per node**"; `--mem-per-cpu`
  is per allocated CPU; `--mem-per-gpu` per GPU; mutually exclusive; `--mem=0`
  means all memory of each node (sbatch(1) `--mem`). Default units MB.
  Memory is only scheduled when `SelectTypeParameters` includes memory (e.g.
  `CR_Core_Memory`) and only enforced with `ConstrainRAMSpace` or
  `OverMemoryKill` (sbatch(1) `--mem` NOTE; slurm.conf(5)). Defaults come
  from `DefMemPerCPU`/`DefMemPerNode` (slurm.conf(5)).
- **GRES** `--gres=gpu:2` is "requested per node" (sbatch(1) `--gres`);
  `--gpus=N` is per job, `--gpus-per-node`, `--gpus-per-task` also exist.
- **Licenses** `-L name[:count]` are cluster-wide counters from
  `Licenses=` in slurm.conf or remote licences in slurmdbd; "Slurm does not
  prevent jobs from using licenses that are not explicitly listed"
  (slurm.conf(5) `Licenses`). OR (`a|b`) between licences is new in 25.05
  (CL 25.05).
- **Constraints** `-C feature` select nodes by admin-defined features; a
  filter, not a consumable (sbatch(1) `-C`). `--prefer` is the soft form.
- **Time** `-t`: a bare number is minutes (sbatch(1) `-t`; hpc-runner D10
  already refuses bare integers for this reason).

### 4.3 Worked examples

Site names (`smp`, `mpi`, `h_vmem`, `mem_free`, `gpu`, `xcelium_lic`) are
illustrative; each is site configuration on SGE.

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

`-c 8` without `-n 1` also gives one task by default (sbatch(1) `-n`: "The
default is one task per node, but note that the --cpus-per-task option will
change this default"); `-n 8` would ask for eight tasks.

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

Neither scheduler talks to the licence server by itself; both count what
jobs ask for against an admin-configured total (complex(5); slurm.conf(5)
`Licenses`). Live licence availability needs a load sensor (SGE) or
periodic `sacctmgr`/`scontrol` updates (Slurm): mechanisms **UNCONFIRMED**
here.

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
| Choosing | `-q` is "a resource request" merged with `-l`; without it any matching queue (submit(1) `-q`) | `-p p1,p2`; without it the default partition; with a list, the earliest-starting one (sbatch(1) `-p`) |
| Interactive vs batch | Queue type; `-now y` needs INTERACTIVE (queue_conf(5) `qtype`) | No queue type; any partition accepts srun/salloc |
| Grouping for share/limits | **Project** `-P`; admin grants access (submit(1) `-P`) | **Account** `-A`, an association in slurmdbd |
| Accounting label | **Account string** `-A`, default `sge`, no policy (submit(1) `-A`) | Same `-A` |
| Enforcement | Projects/ACLs on queues (`projects`, `user_lists`, queue_conf(5)) | `AccountingStorageEnforce=associations,limits,qos,...` (slurm.conf(5)) |
| QOS | No direct equivalent (resource quota sets were not reviewed) | `--qos`; limits and priority per QOS; restricted per association if enforced (sbatch(1) `-q`) |
| User priority | `-p -1023..1024`, users may only lower (submit(1) `-p`) | `--nice[=adj]`, default +100, only privileged users go negative; `--priority` admin only (sbatch(1)) |

Note the letter clash: SGE `-A` is a free accounting string and `-P` the
policy object; Slurm `-A` is the policy object and `-p` is the partition,
not the priority.

**Consequence for a front end.** hpc-runner's `queue` maps to a partition and
`project` to the Slurm account; SGE's account string has no separate Slurm
field. The mapping of SGE `-p` to Slurm `--nice` is not a sign flip only:
the range and default step differ.

---

## 6. Arrays, dependencies, output names, working directory, shell

### Arrays

| | SGE | Slurm |
|---|---|---|
| Syntax | `-t n-m:s`, `-tc N` for concurrency | `--array=0-15:4%4`, lists `1,3,7` allowed |
| Index range | from 1 (submit(1) `-t`) | from 0 to `MaxArraySize-1` (sbatch(1) `-a`) |
| Ids | one `JOB_ID`, tasks `JOB_ID.TASK` in qstat/qacct | every task has its own `SLURM_JOB_ID`; `SLURM_ARRAY_JOB_ID` is the first; shown as `36_2` (job_array.shtml) |
| Task variable | `SGE_TASK_ID`, `undefined` for non-arrays | `SLURM_ARRAY_TASK_ID`, unset for non-arrays |
| Other vars | `SGE_TASK_FIRST/LAST/STEPSIZE` | `SLURM_ARRAY_TASK_MIN/MAX/STEP/COUNT` |
| Interactive | arrays are qsub-only | arrays are sbatch-only (job_array.shtml) |

job_array.shtml shows that the task with the lowest `SLURM_JOB_ID` can be
the last task to run: ids do not follow index order.

### Dependencies

| | SGE | Slurm |
|---|---|---|
| Option | `-hold_jid id,name,...` | `-d type:id[:id][,type:id]` (`?` for OR) |
| Semantics | waits until all have "completed"; a parent exiting 100 keeps it ineligible (submit(1) `-hold_jid`) | `after`, `afterany` (default), `afterok`, `afternotok`, `aftercorr`, `afterburstbuffer`, `singleton` (sbatch(1) `-d`) |
| Array element-wise | `-hold_jid_ad` | `aftercorr` |
| Unsatisfiable | n/a (no ok/notok) | stays PENDING with reason `DependencyNeverSatisfied`, or cancelled with `--kill-on-invalid-dep=yes` / `kill_invalid_depend` (sbatch(1)) |
| Parent already gone | name resolved at submit | `afterok`/`afternotok` need the parent "still active or within MinJobAge seconds after the specified job has ended"; else rejected (sbatch(1) `-d`) |

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

What Slurm does when the output directory does not exist is
**UNCONFIRMED** here (the repo's configuration.rst says SGE discards
output in that case).

### Working directory

- SGE: `-cwd` or `-wd dir`; without either the job runs from the home
  directory. That default is inferred from the relative-path rule above;
  an explicit statement was not found (**UNCONFIRMED** wording).
- Slurm: sbatch and srun default to the caller's cwd
  (`slurm_opt.c:arg_reset_chdir`); `-D` overrides. If the directory cannot
  be entered on the node, slurmstepd logs "couldn't chdir to ...: going to
  /tmp instead" and continues in `/tmp`
  (`src/slurmd/slurmstepd/mgr.c:_pre_task_child_privileged`).

### Shell selection

- SGE: under the default `shell_start_mode posix_compliant` the `#!` line is
  ignored; the shell is `-S` or the queue's `shell` (default `/bin/sh`)
  (sge_conf(5), queue_conf(5) `shell`), started as a login shell if listed
  in `login_shells` (§2.1). `unix_behavior` honours the shebang instead.
- Slurm: sbatch refuses a script without a shebang ("This does not look
  like a batch script. The first line must start with #! ...",
  `src/sbatch/sbatch.c:_get_script_buffer`); the script is stored and
  `execve`'d (`src/slurmd/slurmstepd/task.c:exec_task`), so the kernel runs
  the shebang interpreter. It is not a login shell unless the shebang asks
  (`#!/bin/bash -l`). `--wrap` wraps a command in an `sh` script (sbatch(1)
  `--wrap`). There is no `-S`.
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
| Just finished, in memory | `qstat -s z`: last `finished_jobs` (default 100, deprecated) jobs, lost on qmaster restart (sge_conf(5)) | `scontrol show job` and `squeue -t all` until `MinJobAge` (default 300 s) (slurm.conf(5)) |
| History | `qacct -j` reads the accounting file, flushed every `accounting_flush_time`, else `flush_time` (default 15 s) (sge_conf(5); accounting(5)) | `sacct` via slurmdbd; default window is from midnight today unless `-j` is given, then from the epoch (sacct(1) `-S`) |
| Granularity | one record per job/task | job, `.batch`, `.extern`, and one row per step; `-X` for the allocation only (sacct(1) `-X`) |
| Exit code | `exit_status` (128+signal when killed) plus `failed` for system-level failures (accounting(5)) | `ExitCode` as `code:signal`, plus `DerivedExitCode` = highest step code (job_exit_code.shtml) |
| Non-zero exit | no state change by itself | job state FAILED, reason `NonZeroExitCode` (job_exit_code.shtml) |

The visibility gap D19 describes on SGE (left qstat, not yet in qacct) has a
Slurm counterpart: a job is in the controller until `MinJobAge` and in
slurmdbd once the controller has sent the record; whether there is a gap
between the two on a busy controller is **UNCONFIRMED**.

**Consequence for a front end.** On Slurm an id may mean an array task with
its own id, a step's state is separate from the job's, and the default
`sacct` time window must be widened (or `-j` used) to find older jobs.

---

## Open questions / UNCONFIRMED

1. Whether `qsub -V` from inside an SGE array task gives a non-array child
   the parent's `SGE_TASK_ID` (submit(1) says `-V` can change it).
2. How submit(1)'s "The options -cwd, -v, -V, and -display only apply to
   batch jobs" (QRSH_WRAPPER paragraph) relates to the per-option text that
   allows `-V`/`-v`/`-display` for qrsh with a command.
3. qrsh's exit code for a command killed by a signal, for OOM, and for a
   qrsh-side failure; qrsh's handling of Ctrl-C and of SIGHUP (only "exit N
   returns N" is observed, by `test_interactive`).
4. Whether exported shell functions (`BASH_FUNC_module%%`) and multi-line
   values survive Slurm's `--get-user-env` parsing of `env` output, and
   whether SGE `-V` carries `BASH_FUNC_*` variables.
5. Whether `srun --x11` inside an allocation created without `--x11` errors
   or is silently ignored.
6. Which port range compute nodes use to connect back to srun for X11
   (`SrunPortRange` assumed, not checked).
7. Whether common VNC/ThinLinc/ETX servers listen on TCP and create xauth
   cookies by default (decides whether approach (a) or (b) in §3.5 works).
8. Slurm behaviour when the `-o` directory does not exist on the node.
9. SGE default working directory without `-cwd`: inferred as `$HOME` from
   the relative-path rule, not stated outright.
10. Whether a Slurm job can be absent from both the controller and slurmdbd
    for a short time after `MinJobAge`.
11. How live licence counts are fed to either scheduler (load sensors,
    `scontrol`/`sacctmgr` updates).
12. The stale-`SLURM_*` effects in §2.7 are derived from source code, not
    from SchedMD documentation, and have not been run against a real
    Slurm; only the `SLURM_MEM_*` conflict is corroborated (by a site page).
13. OCS: only the D18 differences are known; qsub/qrsh environment and
    interactive behaviour were not compared against OCS 9 documentation.
