# Trace: what it records and why

Rewritten 2026-11-15. The earlier design (2026-10-31, decisions D-TR1 to
D-TR12) also had `hpc trace compare` and `hpc trace bundle`; both were
removed on the user's decision. Git keeps them (`84384c7`, `e3f8849`).

## Why

- The testbed covers one build of each scheduler. A user's cluster is a
  different build, and nothing from it may be committed.
- When hpc-runner misreads a site's scheduler, the report must hold
  everything about the call: what was typed, the config, the script, what
  the scheduler said and what hpc-runner made of it.
- A trace is that record. It is local, raw and private. Nothing is
  scrubbed and nothing packs or replays it: it is read as files.

## What is recorded

Under `<dir>/<YYYY-MM-DD>/`, two kinds of JSON file.

One per `hpc` command line, `<HHMMSS.mmm>-<pid>-hpc.json`:

- `kind`: `"invocation"`.
- `argv`: the command line as typed.
- `config`: every config file read, in merge order, path and text.
- `env`: every environment variable, one per line, sorted by name. All of
  it, not a list of prefixes: a scheduler's own variables change what a
  command does (`SBATCH_PARTITION` beats a `#SBATCH -p` line,
  `slurm/SGE_SLURM_DIFFERENCES.md` run S18), and a user who must remove a
  value before passing a trace on deletes or edits that line.
- `stdout`, `stderr`: everything `hpc` printed, without terminal control
  codes. Cut at 1,000,000 characters (`output_truncated`).
- `error`: type and message of the error that ended the command; a
  traceback too when it was not a deliberate `HPCToolsError`.
- `exit_code`, `time`, `elapsed`, `pid`, `cwd`, `version`.

One per scheduler command, `<HHMMSS.mmm>-<pid>-<cmd>.json`:

- `argv`, `returncode`, `stdout`, `stderr`: the same keys as the JSON
  fixtures under `tests/unit/fixtures/`.
- `error`: set for a command that never returned (missing binary, timeout).
- `script`: path and text of the job script, on the command that
  submitted it.
- `time`, `elapsed`, `pid`, `cwd`, `version`.

The pid in the file name ties a command line to its scheduler commands.

## How

- Scheduler commands are recorded in `run_command`, the one subprocess
  seam, so every scheduler is covered without code of its own.
- A scheduler calls `recorder.note_script(path, text)` when it writes a
  job script. The next recorded command whose argv names that path takes
  the text. A new scheduler must make the same call.
- The command-line record is made by `main()` in `cli/main.py`
  (`recorder.Invocation`): it copies what is written to `sys.stdout` and
  `sys.stderr`, and writes the record when the process ends, if a trace is
  active by then.
- Activation: `hpc --trace DIR` for one command, or `[trace] enabled =
  true` (read by `get_scheduler()`) for every command. The flag wins. The
  package never activates on import.
- Every scheduler command is written, repeats included, so each command
  line has all of its scheduler commands beside it. There was a dedupe
  (for `hpc monitor` polling); it was removed with monitor tracing on the
  user's decision, 2026-11-15.
- `hpc monitor` is never traced: the group callback in `cli/main.py` calls
  `recorder.disable()`, which also stops `[trace] enabled` from starting
  one. A trace is for debugging what a command submitted or read, not for
  watching status. `hpc --trace DIR monitor` is a usage error.
- Every traced command ends with one line on stderr, `trace: N records
  written to <day directory>`, printed after the record is written so it
  is not in it. Stderr, so `--json` output stays clean. Without it a
  `[trace] enabled` left on is silent, and nothing removes what it writes.
- Nothing is ever removed. The records are the user's to keep or delete;
  the day directories make that a matter of deleting a directory. There
  was a sweep (`max_days`); the user removed it, 2026-11-15.
- `[trace]` keys: `enabled`, `dir` (default `~/.cache/hpc-runner/trace`).
  Unknown keys and wrong types are errors (`dedupe` and `max_days`, keys
  until 2026-11-15, are now unknown).

## Not recorded

- The local scheduler: it runs no scheduler command.
- An interactive job's own output: it goes straight to the terminal.
- `hpc monitor`: nothing, neither its screen nor its scheduler commands.
- Use through the Python API has no command-line record; scheduler
  commands and scripts are recorded once a trace is active.
- If the terminal's encoding is misconfigured, click writes around the
  copy and that output is missing from the record (not tested).
