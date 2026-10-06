# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Provides a front end for submitting jobs to an HPC cluster using SGE, Slurm, LSF, etc.  The aim is to enable two things:

1. Abstract away the intricacies of scheduler CLI args and allow the user to submit jobs based on tool name or job type.
2. Use Environment Modules to construct a clean environment for a job and avoid the 'works for me' problem.

### Job/Type Abstraction

Usage model:

```bash
# Launch an extern on the cluster via SGE:
qsub -q <queue> -N <job_name> -cwd -V -l <resources> xterm

# Launch an xterm on the cluster via hpc-runner (interactive is the default):
hpc run xterm

# Or launch a script that uses a particular tool using a job type:
hpc run -t xcelium run_sim.sh

# Submit as a batch job and return immediately:
hpc run --batch -t xcelium run_sim.sh
```

Configuration file:

* use a TOML based config file to define defaults for jobs
* define tool flows and specify all scheduler arguments
    * tool flows are detected via the first argument passed to 'hpc run'
* definine job types for flows that don't pass the tool
    * when using makefiles or scripts, the runner can't extract the tool name from the command line

### Consistent Environment

One of the common pitfalls of HPC flows is that what works for one user doesn't always work for another.  Using
Environment Modules means that common tools flows use a fixed tool version for all users.

To accomplish this, the scheduler must purge all modules as part of its setup script and load any modules defined
for the flow.  Each tool or type defined in the configuration file must also define the set of module files that must
be loaded.


## Build & Development Commands

```bash
# Setup virtual environment
source sourceme           # Creates .venv with Python 3.11, installs deps
source sourceme --clean   # Clean rebuild of .venv

# Run tests
pytest                    # All tests
pytest -v                 # Verbose
pytest tests/test_core/   # Specific directory
pytest -k "test_job"      # By name pattern

# Type checking and linting
mypy src/hpc_runner
ruff check src/hpc_runner
ruff format src/hpc_runner

# CLI usage
hpc --help
hpc run --dry-run echo hello
hpc --scheduler sge run --batch --cpu 4 --mem 8G python script.py
```

## Architecture

**Package**: `hpc-runner` (PyPI) / `hpc_runner` (import) / `hpc` (CLI)

### Core Abstractions (`src/hpc_runner/core/`)

- **Job** - Central job model with command, resources (cpu/mem/time), modules, dependencies
- **JobResult/ArrayJobResult** - Returned from submission, provides status polling and output access
- **JobStatus** - Unified enum: PENDING, RUNNING, COMPLETED, FAILED, CANCELLED, TIMEOUT, UNKNOWN
- **ResourceSet** - Collection of named resources (gpu, licenses, etc.)
- **HPCConfig** - TOML-based config with two-level discovery: $HPC_RUNNER_CONFIG or <git-root>/hpc-runner.toml (project), then ./hpc-runner.toml (local override)

### Scheduler System (`src/hpc_runner/schedulers/`)

- **BaseScheduler** - ABC defining submit(), cancel(), get_status(), generate_script()
- **SGEScheduler** - Sun Grid Engine (qsub/qstat/qdel). All SGE-specific settings (PE name, resource names) are configurable via `[schedulers.sge]` config section
- **LocalScheduler** - Runs jobs as local subprocesses (for testing without cluster)
- **detection.py** - Auto-detects scheduler: HPC_SCHEDULER env > SGE_ROOT > sbatch > PBS > local fallback
- **Registry** - `get_scheduler(name)` with lazy imports, `register_scheduler()` for custom schedulers

### CLI (`src/hpc_runner/cli/`)

Uses `rich-click` for styled output. One entry point, **`hpc`** (group), with subcommands `run`, `status`, `cancel`, `kill`, `config`, `monitor`. (A separate `submit` command with its own short-option set existed until October 2026; it was removed because maintaining two front ends to the same `Job()` call doubled every option, test and doc line, and the two disagreed on what a short flag meant.)

**`hpc run` defaults to interactive** (qrsh/srun): most hand-typed uses want a GUI to pop up or want to wait for the result. `--batch`/`-b` submits and returns. Array jobs are always batch. Scripts have full control of their options, so they pay the two extra characters.

**Short-option namespace rule**: any short flag click does not recognise is forwarded verbatim to the scheduler, so a short flag `hpc run` defines *shadows* the scheduler's flag of the same letter. Only claim a short flag when hpc-runner already owns that meaning through a long option. Currently claimed: `-t/--type` (shadows qsub/sbatch `-t` = array/time, covered by `--array`/`--time`) and `-b/--batch` (shadows qsub `-b` = binary, irrelevant since jobs always run via a generated script). Unknown *long* options are rejected, as is a single-dash flag that appears without a `--` separator, so a typo can never be silently forwarded or submitted as the command.

**Policy belongs in config, not on the CLI**: options that override a standing policy (which modules load, which module paths are searched, whether the environment is inherited) were removed from the CLI in October 2026. If a job needs a different environment, add a `[types.x]` entry. Per-run knobs (type, cpu, mem, time, queue, name, array, depend, stdout/stderr, directory, extra-module) stay.

**Scheduler passthrough on `hpc run`**: Use `--` to pass raw scheduler arguments. Everything before `--` is scheduler passthrough (set on `job.raw_args`), everything after is the command. Without `--`, all args are the command (no heuristic).
```bash
hpc run echo hello                              # no passthrough
hpc run -q batch.q -l gpu=2 -- python train.py  # passthrough
hpc run --cpu 4 -q batch.q -- mpirun -N 4 ./sim # mixed
```

**Design rationale for `--`**: `hpc run` reserves short-form flags (e.g. `-q`, `-l`, `-N`) for scheduler passthrough and uses long-form options for itself, with the two deliberate exceptions noted above. This means an unclaimed short flag is unambiguously a scheduler argument. However, hpc-runner cannot determine where scheduler args end and the command begins without knowing every scheduler's option grammar — specifically, whether a flag like `-N` is a standalone flag or takes a value argument. The `--` separator is the standard Unix solution to this ambiguity (used by `git`, `ssh`, `docker exec`, etc.). Without it, `hpc run -q batch.q python train.py` is ambiguous: is `python` an argument to `-q` or the start of the command? The `--` makes it explicit.

### Workflow (`src/hpc_runner/workflow/`)

**Pipeline** - Job dependency graphs with topological sorting. Jobs reference other jobs by name; dependencies are resolved at submit time.

### Templates (`src/hpc_runner/templates/`)

Jinja2 templates for job scripts. Each scheduler has its own template in `schedulers/{name}/templates/job.sh.j2`.

### TUI (`src/hpc_runner/tui/`)

**HpcMonitorApp** - Textual-based terminal UI for monitoring HPC jobs. Entry point: `hpc monitor`.

- **app.py** - Main application with custom Nord-inspired theme
- **styles/monitor.tcss** - CSS styling following Rovr aesthetic (see `docs/TEXTUAL_STYLING_COOKBOOK.md`)
- **snapshot.py** - Visual review utility for development

## Key Design Decisions

- **Merged output by default**: stderr goes to stdout unless `--stderr` specified
- **Configurable SGE settings**: PE name, memory resource name, time resource name all come from config, not hardcoded
- **Descriptor pattern**: Scheduler arguments use Python descriptors for type-safe flag/directive generation

## TUI Development Rules

### Styling Requirements (CRITICAL)

All TUI components MUST follow these styling patterns. **Do NOT use DEFAULT_CSS in components** - put all styles in `monitor.tcss` for consistency.

**Core Principles:**
- **Transparent backgrounds everywhere** - use `background: transparent` on all widgets
- **Rounded borders** - use `border: round $border-blurred` (unfocused) or `border: round $border` (focused)
- **No solid colored backgrounds** except for highlighted/selected items
- **Border titles in $primary** - use `border-title-color: $primary`

**Standard Widget Patterns:**
```css
/* Panels and containers */
MyWidget {
    background: transparent;
    border: round $border-blurred;
    border-title-color: $primary;
    border-title-background: transparent;
}

MyWidget:focus, MyWidget:focus-within {
    border: round $border;
}

/* Buttons - transparent with border */
Button {
    background: transparent;
    border: round $border-blurred;
    color: $foreground;
}

Button:hover {
    background: $boost;
    border: round $border;
}

/* Popups/overlays - transparent background */
Popup {
    layer: overlay;
    background: transparent;
    border: round $primary;
}
```

**CSS Variables (defined in monitor.tcss):**
- `$border-blurred` - muted border for unfocused elements
- `$border` - bright border for focused elements
- `$primary` - teal accent color (#88C0D0)
- `$error` - red for destructive actions

**Verification:**

After ANY edit to TUI code, verify visually that:
1. All backgrounds are transparent (terminal shows through)
2. Borders are rounded (╭╮╰╯ characters)
3. No solid color blocks except for selected/highlighted items
4. Focus states brighten borders appropriately
