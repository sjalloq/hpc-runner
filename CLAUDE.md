# CLAUDE.md

hpc-runner submits jobs to an HPC scheduler (SGE today, Slurm next) by tool
name or job type, from a TOML config that also fixes the Environment Modules
every job loads, so a flow that works for one user works for all. What it
does and how it is used: `README.md`, `docs/source/`. How the code is put
together (layout, architecture, tests, testbed, releases):
`docs/source/development.rst`. Package `hpc-runner` on PyPI, `hpc_runner` to
import, `hpc` on the command line.

## Commands

```bash
source sourceme                                   # .venv with the dev extras; --clean rebuilds
pytest                                            # unit suite (tests/unit); integration is deselected
mypy src/hpc_runner && ruff check src/hpc_runner tests && ruff format src/hpc_runner tests
make -C docs html SPHINXOPTS="-W --keep-going"    # warnings are errors
pytest tests/unit/test_golden.py --update-golden  # only after an intended template change
hpc-testbed pytest --up                           # integration suite against SGE in Docker
hpc-testbed --flavour ocs capture                 # regenerate that flavour's fixtures from its running stack
hpc run --dry-run echo hello                      # preview a script; HPC_SCHEDULER=local hpc run ... runs one here
```

## Rules

- **Provenance.** Every statement about this code, a scheduler or a library
  names where it was read or run in this session: file and line, a command
  and its output, a source tag, a capture. A statement without one is
  written as `unverified`, never as fact. A plan that touches existing code
  includes a pass over that code, with the same care as the pass over the
  external system. Procedure: `docs/agents/evidence.md`.
- **Scheduler output comes from captures.** Parser tests read
  `tests/unit/fixtures/<family>/<flavour>/`, written by `hpc-testbed capture`
  from the Docker testbed in `testbed/`. A text no capture provides is an
  escalation, not a hand-written fixture. Nothing captured from a real work
  cluster enters the repo, scrubbed or not.
- **One seam.** Scheduler CLIs run only through `run_command()` in
  `src/hpc_runner/schedulers/_subprocess.py`; tests patch it
  (`tests/fakes.py`), never `subprocess.run`.
- **Policy lives in config.** Which modules load, which paths are searched,
  whether the environment is inherited: a `[types.x]` entry, not a CLI
  option. Raw scheduler flags travel in `-S/--scheduler-args`; every short
  letter belongs to `hpc run`.
- **The scheduler is named, never detected**: `scheduler = "sge"` in config
  or `HPC_SCHEDULER`; none is a `ConfigError`.
- **Sub-agents** make no design decisions (an open question comes back to
  the main session with the options) and run no `pytest` (the main session
  runs the suite serially and passes the output in; parallel runs have
  crashed this machine).
- **Rendered scripts**: every interpolated value through the `sh` Jinja
  filter; every script rendered in the suite is checked with `bash -n`;
  goldens in `tests/unit/golden/` change only with an intended template
  change.
- **Commits**: a subject and a short body describing the change, 10 to 15
  lines in all, with no tool attribution trailers. Longer prose goes in the
  project note, not the commit.

## Invariants

- A job is three values: `JobSpec` (what was asked, `None` = unspecified) →
  `resolve(spec, config, host)` → `Job` (frozen). Merge rules live only in
  `core/resolve.py`; schedulers render a `Job` and never mutate it.
- `HPCConfig` is always passed explicitly; there is no global cache.
- One `JobStatus` enum for every scheduler; `reduce_statuses()` is the only
  way several become one.
- stderr merges into stdout unless `--stderr` is given.

## Working notes and agent docs

- `docs/dev/README.md` is the index: every piece of work, its status, the
  one file to read and the next action. Read it first; update it whenever a
  status or next action changes.
- `docs/dev/<topic>/`: one folder per piece of work, holding its decision
  log of numbered items (D1.., D-T.., D-TR.., D-S..) and any reference the
  code cites. A plan or review is deleted once it has been carried out
  (git keeps it). Not built by Sphinx, never at the repo root. There are no
  tickets: work is taken from a decision log, one item at a time.
- `docs/agents/evidence.md`: the provenance procedure.
- `hpc monitor` styling: `docs/dev/tui/TEXTUAL_STYLING_COOKBOOK.md`, rules
  and checklist at the top. Verify visually after any TUI edit.
