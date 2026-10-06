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
# Basic job submission
hpc run python train.py

# With resources
hpc run --cpu 4 --mem 16G --time 4:00:00 "python train.py"

# GPU job
hpc run --queue gpu --cpu 4 --mem 32G "python train.py --epochs 100"

# Preview without submitting
hpc run --dry-run --cpu 8 "make -j8"

# Interactive session
hpc run --interactive bash

# Array job
hpc run --array 1-100 "python process.py --task-id \$SGE_TASK_ID"

# Wait for completion
hpc run --wait python long_job.py
```

### Python API

```python
from hpc_runner import Job

# Create and submit a job
job = Job(
    command="python train.py",
    cpu=4,
    mem="16G",
    time="4:00:00",
    queue="gpu",
)
result = job.submit()

# Wait for completion
status = result.wait()
print(f"Exit code: {result.returncode}")

# Read output
print(result.read_stdout())
```

### Job Dependencies

```python
from hpc_runner import Job

# First job
preprocess = Job(command="python preprocess.py", cpu=8, mem="32G")
result1 = preprocess.submit()

# Second job runs after first succeeds
train = Job(command="python train.py", cpu=4, mem="48G", queue="gpu")
train.after(result1, type="afterok")
result2 = train.submit()
```

### Pipelines

```python
from hpc_runner import Pipeline

with Pipeline("ml_workflow") as p:
    p.add("python preprocess.py", name="preprocess", cpu=8)
    p.add("python train.py", name="train", depends_on=["preprocess"], queue="gpu")
    p.add("python evaluate.py", name="evaluate", depends_on=["train"])

# Auto-submitted when the with-block exits.
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

### Example Configuration

```toml
[defaults]
cpu = 1
mem = "4G"
time = "1:00:00"
inherit_env = true

[schedulers.sge]
parallel_environment = "smp"
memory_resource = "mem_free"
purge_modules = true

[types.gpu]
queue = "gpu"
resources = [{name = "gpu", value = 1}]

[types.interactive]
queue = "interactive"
time = "8:00:00"
```

Use named job types:

```bash
hpc run --job-type gpu "python train.py"
```

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

# Output handling
merge_output = true             # Merge stderr into stdout (-j y)

# Module system
purge_modules = true            # Run 'module purge' before loading job modules
silent_modules = false          # Suppress module command output (-s flag)
module_init_script = ""         # Path to module init script (auto-detected if empty)

# Environment
expand_makeflags = true         # Expand $NSLOTS in MAKEFLAGS for parallel make
unset_vars = []                 # Environment variables to unset in jobs
                                # e.g. ["https_proxy", "http_proxy"]
```

**Fully populated config example:**

```toml
[defaults]
scheduler = "auto"
cpu = 1
mem = "4G"
time = "1:00:00"
queue = "batch.q"
use_cwd = true
inherit_env = true
stdout = "hpc.%N.%J.out"
modules = ["gcc/12.2", "python/3.11"]
resources = [
  { name = "scratch", value = "20G" }
]

[schedulers.sge]
parallel_environment = "smp"
memory_resource = "mem_free"
time_resource = "h_rt"
merge_output = true
purge_modules = true
silent_modules = false
expand_makeflags = true
unset_vars = ["https_proxy", "http_proxy"]

[tools.python]
cpu = 4
mem = "16G"
time = "4:00:00"
queue = "short.q"
modules = ["-", "python/3.11"]   # leading "-" replaces the list instead of merging
modules = ["$XCELIUM_MODULE"]    # $VAR / ${VAR} expanded on the submit host; unset or
                                 # empty is an error, never a silent bare "module load"
resources = [
  { name = "tmpfs", value = "8G" }
]

[types.interactive]
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
hpc run [OPTIONS] [-- COMMAND]

Options:
  --job-name TEXT       Job name
  --cpu INTEGER         Number of CPUs
  --mem TEXT            Memory (e.g., 16G, 4096M)
  --time TEXT           Time limit (e.g., 4:00:00)
  --queue TEXT          Queue/partition name
  --nodes INTEGER       Number of nodes (MPI jobs)
  --ntasks INTEGER      Number of tasks (MPI jobs)
  --directory PATH      Working directory
  --job-type TEXT       Job type from config
  --module TEXT         Module to load (repeatable)
  --module-path PATH    Module path to use (repeatable)
  --stdout TEXT         Stdout file path pattern
  --stderr TEXT         Separate stderr file (default: merged)
  --array TEXT          Array spec (e.g., 1-100, 1-100%5)
  --depend TEXT         Job dependencies
  --inherit-env/--no-inherit-env
  --interactive         Run interactively (qrsh/srun)
  --local               Run locally (no scheduler)
  --dry-run             Show script without submitting
  --wait                Wait for completion
  --keep-script         Keep job script for debugging
  -h, --help            Show help

Other commands:
  hpc status [JOB_ID]   Check job status (--history, --watch, --json)
  hpc cancel JOB_ID     Cancel a job
  hpc monitor           Interactive TUI
  hpc config show       Show active configuration
  hpc config init       Create starter config
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
