# Scheduler trace: design and decisions — 2026-10-31

Why: the test QA fix pass (`docs/dev/test-review/TEST_QA_REVIEW.md` §6)
found that five of its ten bugs were the parser meeting real scheduler
output for the first time. Captured fixtures fixed that for the testbed's
one gridengine build. A user's cluster is a different build, and nothing
from it may be committed. The trace is the bridge: hpc-runner records what
the scheduler said and what it made of it, locally, so the user can check
the parsers against their own site and report a mismatch with the exact
text attached.

## What it is

- **Recorder.** `run_command` (the one subprocess seam) writes one JSON
  file per scheduler call when a trace is active: argv, return code,
  stdout, stderr, elapsed seconds, timestamp, pid, cwd, hpc-runner
  version, and `error` for a call that never returned (binary missing or
  timed out). Interactive calls (`capture=False`) record `null` output.
- **Activation.** `hpc --trace DIR` for one invocation, or `[trace]
  enabled = true` in the config for every invocation during a window of
  normal use. The flag wins. The config path is read by `get_scheduler()`,
  so the CLI, the TUI and the programmatic API all record.
- **`hpc trace compare DIR`.** Runs every recorded output back through the
  parsers and lists what did not parse: an exception, an empty result, a
  `JobInfo` with `UNKNOWN` status, a missing runtime or exit code when the
  record had the field. With `--fixtures tests/unit/fixtures/sge` it also
  lists argv shapes the trace contains that no captured fixture covers.
- **`hpc trace bundle DIR`.** Packs a trace for an issue report:
  usernames, hostnames, job names, projects and home paths replaced by
  stable tokens, plus a `meta.json` (versions, scheduler banner, the
  `[schedulers.*]` config). `--raw` skips the scrub for private use.

## Decisions

- **D-TR1 Recording lives in the seam, not the scheduler.** Everything a
  parser ever sees passes through `run_command`; one hook there covers
  SGE today and Slurm/PBS when they land. The local scheduler is exempt
  from the seam and is not traced (it runs no scheduler CLI).
- **D-TR2 Activation is a module-level tracer, set once.** `trace.activate`
  installs it; `get_scheduler()` calls `activate_from_config` which is a
  no-op when a tracer is already active, so the flag beats the config.
  Schedulers constructed directly (tests, embedding) trace only if the
  caller activates. The package never activates on import.
- **D-TR3 Record shape equals fixture shape.** The per-call JSON has the
  same `argv/returncode/stdout/stderr` keys as the JSON fixtures written by
  `tests/integration/capture.py`, so a record from a user's cluster can be
  dropped into a fixture root and replayed by the unit suite unchanged.
- **D-TR4 Layout is one directory per day, one file per call.**
  `<dir>/<YYYY-MM-DD>/<HHMMSS.mmm>-<pid>-<cmd>.json`. The pid in the name
  stitches the calls of one `hpc status` back together without reading
  files; the day directory makes the sweep a name comparison.
- **D-TR5 Dedupe by exact repeat.** A call whose argv, return code, stdout
  and stderr equal the previous record *for that argv* is not written.
  `hpc monitor` polls every few seconds and most snapshots are identical;
  the trace keeps the moments something changed. The record that is kept
  is the first of a run of identical ones. Off with `dedupe = false`.
- **D-TR6 Sweep by day directory.** At activation, day directories older
  than `max_days` (default 14) are removed. Only directories whose name is
  a date are touched, so a wrong `dir` cannot delete anything else.
- **D-TR7 `[trace]` is a top-level config section**, keys `enabled`
  (bool), `dir` (str, `~` expanded, default `~/.cache/hpc-runner/trace`),
  `max_days` (int), `dedupe` (bool). Strict like every other section:
  unknown keys and wrong types are errors. Scalars, later file wins.
- **D-TR8 Scrubbing happens only on export.** The local trace is private
  and raw so `compare` works on real names. `bundle` replaces values found
  in structured places (qacct `owner`/`hostname`/`jobname`/`project`/
  `department`, qstat XML `JB_owner`/`JB_name`/`queue_name`, qstat text
  columns, argv `-u`/`-o`/`-N` values, the current user, the hostname,
  `$HOME`) everywhere in every file, prints the map to stderr so the user
  can check it, and refuses to write if a collected name is empty or a
  single character (it would corrupt the text). No heuristic for free
  text: a job's own stdout is never in a trace, only the scheduler's.
- **D-TR9 `compare` is SGE-aware through one dispatch table.** It maps an
  argv shape to the parser that consumes it; another scheduler adds rows.
  Argv is normalised to a shape by replacing job ids (`\d+(\.\d+)?`),
  script paths, user names after `-u`/`-o` and job names after `-N` with
  placeholders, so `qacct -j 1203` and `qacct -j 1204` are one shape.
- **D-TR10 Fixture coverage comes from the manifest, not a hard-coded
  list.** `capture.py` records the argv of every file it writes in
  `manifest.json`; `compare --fixtures` reads those argv, shapes them the
  same way, and reports trace shapes with no fixture. The fixtures are
  regenerated for this (ids change, structure does not).
- **D-TR11 `compare` exits 1 when it finds anything**, so a cron job over
  a trace directory can act on it; `--json` for machines.

## Out of scope

- Tracing the local scheduler (D-TR1).
- Scrubbing free text or paths other than `$HOME` (D-TR8); the user
  reviews the map before attaching a bundle.
- Env-var activation; the flag and the config are the two paths.
