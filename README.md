# hpc-runner

**Unified HPC job submission across multiple schedulers**

Write your jobs once, run them on any cluster - SGE, Slurm, PBS, or locally for testing.

## Features

- **Unified CLI** - Same commands work across SGE, Slurm, PBS
- **Python API** - Programmatic job submission with dependencies and pipelines
- **Auto-detection** - Automatically finds your cluster's scheduler
- **Interactive TUI** - Monitor jobs with a terminal dashboard
- **Job Dependencies** - Chain jobs with afterok, afterany, afternotok
- **Array Jobs** - Batch processing with throttling support
- **Virtual Environment Handling** - Automatic venv activation on compute nodes
- **Module Integration** - Load environment modules in job scripts
- **Dry-run Mode** - Preview generated scripts before submission

## Installation

```bash
pip install hpc-runner
```

Or with uv:

```bash
uv pip install hpc-runner
```

## Quick Start

### CLI

```bash
# Run interactively on the cluster (the default): output streams here, hpc waits
hpc run xterm
hpc run -t xcelium make sim

# Submit as a batch job and return immediately
hpc run --batch --cpu 4 --mem 16G --time 4:00:00 make sim

# Batch job, then block until it finishes
hpc run --batch --wait python long_job.py

# Preview the generated script without submitting
hpc run --dry-run --cpu 8 make -j8

# Array job (always batch)
hpc run --array 1-100 "python process.py --task-id \$SGE_TASK_ID"

# Raw scheduler flags go before --, the command after
hpc run -q gpu.q -l gpu=1 -- python train.py
```

### Python API

A `JobSpec` holds what you asked for; `resolve()` applies config and returns
a frozen `Job`; a scheduler submits it and returns a `JobResult` handle.

```python
from hpc_runner import Host, JobSpec, get_scheduler, load_config, resolve

config = load_config()
scheduler = get_scheduler(config=config)

spec = JobSpec("python train.py", cpu=4, mem="16G", time="4:00:00", queue="gpu")
job = resolve(spec, config, Host.capture())
result = scheduler.submit(job)

# Wait for completion
status = result.wait()
print(f"Exit code: {result.returncode}")
```

Jobs submitted from the API are batch unless `interactive=True` is set on the
spec (or by the tool/type config). An interactive run has already finished
when `submit()` returns: its `JobResult` has `job_id=None` and an `exit_code`.

Reconnect to a job by id, without the original `Job`:

```python
result = scheduler.result("12345")
print(result.status)
```

### Job Dependencies

```python
from hpc_runner import DependencyType, Host, JobSpec, get_scheduler, load_config, resolve

config = load_config()
scheduler = get_scheduler(config=config)
host = Host.capture()

# First job
prep = scheduler.submit(resolve(JobSpec("python preprocess.py", cpu=8, mem="32G"), config, host))

# Second job runs after the first succeeds (afterok is the default)
train = JobSpec("python train.py", cpu=4, mem="48G", queue="gpu").after(prep)
train_result = scheduler.submit(resolve(train, config, host))

# Each after() call appends, with its own type
report = JobSpec("python report.py").after(train_result, type=DependencyType.AFTERANY)
```

Each parent carries its own type: `afterok` (the default), `afterany`,
`afternotok` or `after` (start-after). Depending on an array job's result
waits for every task. On the command line, `--depend` takes Slurm's grammar
and may be repeated; a bare ID means `afterok`:

```bash
hpc run --batch --depend afterok:123:124,afterany:125 -- ./merge.sh
hpc run --batch --depend 123 --depend afternotok:124 -- ./cleanup.sh
```

On SGE, `-hold_jid` alone is afterany, so `afterok` and `afternotok` are
emulated: every job writes its exit code to a sentinel file in a shared
directory (one per task for an array job), and a guard at the top of the
dependent checks each parent's code before the command runs
(`[schedulers.sge] emulate_afterok`, on by default; with it off only
`afterany` is accepted). A dependent whose dependency is not satisfied does
not run: it exits with the reserved code 113 and reports
`JobStatus.SKIPPED`, and its own dependents are skipped in turn. `after`
cannot be expressed on SGE and raises `NotSupportedError`.

`result.wait()` returns `UNKNOWN` instead of polling forever once the
scheduler has been unable to report the job for `unknown_after` seconds
(default 60).

### Pipelines

```python
from hpc_runner import JobSpec, Pipeline, load_config

with Pipeline("ml_workflow", config=load_config()) as p:
    p.add(JobSpec("python preprocess.py", cpu=8), name="preprocess")
    p.add(JobSpec("python train.py", queue="gpu"), name="train", depends_on=["preprocess"])
    p.add("python evaluate.py", name="evaluate", depends_on=["train"])

# Auto-submitted when the with-block exits; each step's spec is resolved then.
# Results are accessible on the pipeline object.
for name, result in p.results.items():
    print(name, result.job_id, result.status)
p.wait()
```

## Scheduler Support

| Scheduler | Status | Notes |
|-----------|--------|-------|
| SGE | Fully implemented | qsub, qstat, qdel, qrsh |
| Local | Fully implemented | Run as subprocess (for testing) |
| Slurm | Planned | sbatch, squeue, scancel |
| PBS | Planned | qsub, qstat, qdel |

### Auto-detection Priority

1. `HPC_SCHEDULER` environment variable
2. SGE (`SGE_ROOT` or `qstat` available)
3. Slurm (`sbatch` available)
4. PBS (`qsub` with PBS)
5. Local fallback

## Configuration

hpc-runner uses TOML configuration files. Discovery uses a two-level model:

1. **Project config:** `$HPC_RUNNER_CONFIG` env var (if set), otherwise `<git-root>/hpc-runner.toml`
2. **Local override:** `./hpc-runner.toml` in cwd (when different from level 1)

Both levels are deep-merged (local override wins). You can bypass discovery with `--config /path/to/file.toml`.

Every file starts with `config_version = 1` and is validated on load: an
unknown key or a wrong type is an error naming file, line, section and key
(with "did you mean"), so `hpc run` refuses a broken config before touching
the scheduler. `hpc config check [PATH]` prints every problem;
`hpc config resolve [-t TYPE] COMMAND...` shows the job a command would get
and which layer set each value.

### Example Configuration

```toml
config_version = 1

[defaults]
cpu = 1
mem = "4G"
time = "1:00:00"
inherit_env = true
modules = ["gcc/12"]

[schedulers.sge]
parallel_environment = "smp"
memory_resource = "mem_free"

[tools.xrun]
modules = ["xcelium/23.09"]
resources = [{ name = "xcelium_lic", value = 1 }]

[tools.slang]
modules = ["slang/0.9"]

[tools.xterm]
queue = "interactive"
time = "8:00:00"

[types.lint]
tools = ["xrun", "slang"]   # pulled in, in this order, then the keys below
cpu = 2
```

### How config is layered

The chain is fixed, least specific first:

```
hpc run CMD ...        defaults -> tools.<CMD> -> tools.<CMD>.options.<first match> -> command line
hpc run -t T CMD ...   defaults -> tools.<each of types.T.tools> -> types.T -> command line
```

With `-t`, the tool inferred from the command is not consulted: the type is
the whole flow. Scalars: the most specific layer wins. Tables (`env_vars`,
`scheduler_args`, ...) merge by key.

Lists (`modules`, `modules_path`, `modules_path_append`, `env_unset`,
`env_passthrough`, `resources`, `scheduler_args.<sched>`) **append** across layers with
duplicates dropped; `resources` match by `name`, later value wins. To do
anything else, use a verb table:

```toml
modules = { set = ["gcc/9"] }                                   # replace
modules = { remove = ["python/3.11"], add = ["python/3.12"] }   # edit
```

In Python the same verbs are `Set([...])` and `Edit(remove=[...], add=[...])`.

`scheduler_args` is the escape hatch for flags hpc-runner does not model,
allowed on any layer and used only by the named scheduler:

```toml
[defaults.scheduler_args]
sge = ["-binding", "linear:1"]
```

Use a named job type:

```bash
hpc run -t lint make lint
```

### Variables and the job environment

One rule: *every `$VAR` in config is resolved where you submit from; runtime
values come from `${hpc.*}`; anything else is shell in your script or
Makefile.* An undefined `$VAR` is an error; `$$` is a literal `$`.

- Runtime values: `${hpc.cpus}`, `${hpc.job_id}`, `${hpc.task_id}`,
  `${hpc.tmpdir}`, `${hpc.job_name}`, `${hpc.host}`, rendered by the
  scheduler (SGE: `$NSLOTS`, `$JOB_ID`, `$SGE_TASK_ID`, `$TMPDIR`,
  `$JOB_NAME`, `$HOSTNAME`). `MAKEFLAGS = "-j${hpc.cpus}"` works everywhere.
- Resolver variables: `${CONFIG_DIR}` (directory of the config file that set
  the value), `${GIT_ROOT}`, `${CWD}`, `${USER}`.

The job inherits your environment (`inherit_env = true`, SGE `-V`), then its
script always runs `module purge` and loads the configured modules: the
modules the config names are the versions you get, whatever your shell had
loaded. Nothing else in the inherited environment is reset. For a strict
job, `inherit_env = false` drops `-V` and passes only the variables named in
`env_passthrough`.

```toml
[tools.make]
env_vars = { MAKEFLAGS = "-j${hpc.cpus}" }

[types.regress]
inherit_env = false
env_passthrough = ["DISPLAY", "LM_LICENSE_FILE"]
env_prepend = { PATH = "${CONFIG_DIR}/bin" }
venv = "${CONFIG_DIR}/.venv"     # or false for none; unset = the active $VIRTUAL_ENV
```

After modules and venv the script applies, in order, `env_unset`,
`env_prepend`, `env_append` (`:`-separated) and `env_vars`. Values are
shell-quoted. Module commands are quiet unless they fail; `hpc --verbose run`
shows their output.

### SGE Configuration

SGE clusters vary widely in how resources are named. The `[schedulers.sge]`
section lets you match your site's conventions without touching job definitions.

**How job fields map to SGE flags:**

| Job Field | SGE Flag | Configurable Via |
|-----------|----------|------------------|
| `cpu`     | `-pe <pe_name> <slots>` | `parallel_environment` |
| `mem`     | `-l <resource>=<value>` | `memory_resource` |
| `time`    | `-l <resource>=<value>` | `time_resource` |
| `queue`   | `-q <queue>` | direct |
| `resources` | `-l <name>=<value>` | direct |

**Full `[schedulers.sge]` reference:**

```toml
[schedulers.sge]
# Resource naming -- these must match your site's SGE configuration
parallel_environment = "smp"    # PE name for CPU slots (some sites use "mpi", "threaded", etc.)
memory_resource = "mem_free"    # Memory resource name (common alternatives: "h_vmem", "virtual_free")
time_resource = "h_rt"          # Time limit resource name (commonly "h_rt")

# Module system ('module purge' always runs first)
module_init_script = ""         # Path to module init script (auto-detected if empty)
module_system = ""              # "modules" (exports MODULES_VERBOSITY=concise), "lmod" or ""

# Dependencies ('-hold_jid' alone is afterany)
emulate_afterok = true          # afterok/afternotok via exit sentinels; false: afterany only
```

Output is merged (`-j y`) unless a job sets `stderr`. Variables to unset in
the job are a job setting, `env_unset`, on `[defaults]`, a tool or a type.

**Fully populated config example:**

```toml
config_version = 1

[defaults]
cpu = 1
mem = "4G"
time = "1:00:00"
queue = "batch.q"
use_cwd = true
inherit_env = true
stdout = "hpc.%N.%J.out"
modules = ["gcc/12.2", "python/3.11"]
modules_path = ["/site/modulefiles"]           # module use <path>, searched first
modules_path_append = ["$HOME/modulefiles"]   # module use --append <path>, searched last
resources = [
  { name = "scratch", value = "20G" }
]
env_unset = ["https_proxy", "http_proxy"]   # unset in the job script
env_prepend = { PATH = "${CONFIG_DIR}/tools/bin" }

[schedulers.sge]
parallel_environment = "smp"
memory_resource = "mem_free"
time_resource = "h_rt"
module_system = "modules"

[tools.python]
cpu = 4
mem = "16G"
time = "4:00:00"
queue = "short.q"
modules = { set = ["python/3.11", "$PY_EXTRA_MODULE"] }   # replace the list
                                 # $VAR / ${VAR} expanded on the submit host; unset or
                                 # empty is an error, never a silent bare "module load"
resources = [
  { name = "tmpfs", value = "8G" }
]

[tools.xterm]
queue = "interactive.q"
time = "8:00:00"
cpu = 2
mem = "8G"

[types.gpu]
queue = "gpu.q"
cpu = 8
mem = "64G"
time = "12:00:00"
resources = [
  { name = "gpu", value = 1 }
]
```

## TUI Monitor

Launch the interactive job monitor:

```bash
hpc monitor
```

Key bindings:
- `q` - Quit
- `r` - Refresh
- `u` - Toggle user filter (my jobs / all)
- `/` - Search
- `Enter` - View job details
- `Tab` - Switch tabs

## CLI Reference

```
hpc run [OPTIONS] [SCHEDULER_FLAGS --] COMMAND...

Runs interactively by default; --batch submits and returns.

Options:
  --name TEXT           Job name
  --cpu INTEGER         Number of CPUs
  --mem TEXT            Memory (e.g., 16G, 4096M)
  --time TEXT           Time limit (e.g., 4:00:00)
  --queue TEXT          Queue/partition name
  --nodes INTEGER       Number of nodes (MPI jobs)
  --ntasks INTEGER      Number of tasks (MPI jobs)
  --directory PATH      Working directory
  -t, --type TEXT       Job type from config
  --stdout TEXT         Stdout file path pattern
  --stderr TEXT         Separate stderr file (default: merged)
  --array TEXT          Array spec (e.g., 1-100, 1-100%5)
  --depend TEXT         Job dependency, repeatable: ID or TYPE:ID[:ID...][,TYPE:ID...]
  -b, --batch           Submit as a batch job (default: interactive via qrsh/srun)
  --local               Run locally (no scheduler)
  --dry-run             Show script without submitting
  --wait                Block until a batch job completes
  --keep-script         Keep job script for debugging
  -h, --help            Show help

Other commands:
  hpc status [JOB_ID]   Check job status (--history, --watch, --json)
  hpc cancel JOB_ID     Cancel a job
  hpc monitor           Interactive TUI
  hpc config show       Show active configuration
  hpc config init       Create starter config
  hpc config check      Validate configuration (exit 1 on any error)
  hpc config resolve    Show a command's resolved job and each value's source
```

## Development

```bash
# Setup environment
source sourceme
source sourceme --clean  # Clean rebuild

# Run tests
pytest
pytest -v
pytest -k "test_job"

# Type checking
mypy src/hpc_runner

# Linting
ruff check src/hpc_runner
ruff format src/hpc_runner
```

## License

MIT License - see LICENSE file for details.
