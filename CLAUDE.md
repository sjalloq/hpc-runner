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
