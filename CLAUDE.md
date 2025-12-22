# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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
hpc run --dry-run "echo hello"
hpc --scheduler sge run --cpu 4 --mem 8G "python script.py"
```

## Architecture

**Package**: `hpc-runner` (PyPI) / `hpc_runner` (import) / `hpc` (CLI)

### Core Abstractions (`src/hpc_runner/core/`)

- **Job** - Central job model with command, resources (cpu/mem/time), modules, dependencies
- **JobResult/ArrayJobResult** - Returned from submission, provides status polling and output access
- **JobStatus** - Unified enum: PENDING, RUNNING, COMPLETED, FAILED, CANCELLED, TIMEOUT, UNKNOWN
- **ResourceSet** - Collection of named resources (gpu, licenses, etc.)
- **HPCConfig** - TOML-based config with hierarchy: ./hpc-tools.toml > pyproject.toml > git root > ~/.config > package defaults

### Scheduler System (`src/hpc_runner/schedulers/`)

- **BaseScheduler** - ABC defining submit(), cancel(), get_status(), generate_script()
- **SGEScheduler** - Sun Grid Engine (qsub/qstat/qdel). All SGE-specific settings (PE name, resource names) are configurable via `[schedulers.sge]` config section
- **LocalScheduler** - Runs jobs as local subprocesses (for testing without cluster)
- **detection.py** - Auto-detects scheduler: HPC_SCHEDULER env > SGE_ROOT > sbatch > PBS > local fallback
- **Registry** - `get_scheduler(name)` with lazy imports, `register_scheduler()` for custom schedulers

### CLI (`src/hpc_runner/cli/`)

Uses `rich-click` for styled output. Commands: `run`, `status`, `cancel`, `config`.

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

**IMPORTANT: After ANY edit to TUI code (app.py, monitor.tcss, or related files), you MUST:**

1. Run the snapshot review tool and verify all checks pass:
   ```bash
   python -m hpc_runner.tui.snapshot
   ```

2. Review the output for:
   - All backgrounds should be transparent (ANSI_DEFAULT or a=0)
   - Active tab should be `#88c0d0` (muted teal)
   - No solid background colors breaking transparency

3. If any check fails, fix the issue before considering the edit complete.

This prevents visual regressions like accidentally introducing solid backgrounds when transparency is expected.
