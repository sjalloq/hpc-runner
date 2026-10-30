# Session review points — 2026-10-28/29 overnight

## Summary

Branch `refactor/decisions` (off `main` at 1689ed8), seven commits, one per
task, each reviewed by me after an Opus 5.5 sub-agent implemented it:

| Commit | Task | Tests |
|---|---|---|
| 7dcb0af | D13: exception hierarchy, one subprocess seam, CLI errors, B10, B11 | 360 |
| 2107478 | D12 + D7: JobSpec / resolve / frozen Job, handles, interactive attr, B1, B3 | 441 |
| 0ade374 | D1 + D2 + D11 + D14: list rule, types from tools, strict schema, config check/resolve, B2, B5, B8 | 544 |
| b156d92 | D3 + D4 + D5 + D15 + B4: expansion rule, ${hpc.*}, passthrough, venv, quiet modules, shared template | 649 |
| ef4117d | D8 + D16 fixes: per-parent deps, SKIPPED/113, batched statuses, wait() UNKNOWN, B6 | 730 |
| d27af07 | D9 + D10: owned output paths, Memory/Duration, B7, B9 | 888 |
| 73fd552 | Sweep: golden scripts, executable docs, QA hygiene, CI on every push | 1003 |

`pytest`, `mypy --strict`, `ruff check src tests`, `ruff format` and the
Sphinx `-W` build are clean at HEAD. `main` is untouched. Nothing pushed.
D6 (hooks) stays deferred and D16 (pipeline lifecycle) stays parked, as
decided. Also new and untracked: `../testbed/TESTBED_RESEARCH.md` (prior art and fact
checks, every claim with a URL) and `../testbed/TESTBED_PLAN.md` (the implementation
plan for review, with eight open questions at the end).

Below: every judgement call I made where DECISIONS.md was silent or
ambiguous, so the sub-agents never had to choose. Each needs a yes/no or a
correction. None is blocking; everything was implemented under the stated
assumption.

## Task 1 — D13 (exceptions, subprocess seam, CLI errors)

- **B10 `tomli_w`**: removed the import and kept the existing manual TOML
  formatter in `hpc config show`, rather than adding `tomli-w` as a
  dependency. Reason: no new dependency for a display path that already had a
  fallback.
- **Default command timeout**: `BaseScheduler.COMMAND_TIMEOUT = 60 s` for
  every query/submit call; interactive `qrsh` passes `timeout=None` because it
  blocks for the job's lifetime. D13 says "applies a timeout" without a value.
- **`LocalScheduler` keeps `subprocess.Popen`** for running the job itself;
  the seam is for scheduler CLIs. Noted in a comment.
- **`ConfigNotFoundError` deleted** along with `ValidationError` (zero raise
  sites, D13 rule 7).
- **`PipelineError` subclasses** (`DuplicateStepError`, `UnknownStepError`,
  `DependencyCycleError`, `NotSubmittedError`) added as D13.3 says, even though
  D16 will likely reshape `Pipeline`.
- **`list_active_jobs`** still returns `[]` on a non-zero `qstat` (some sites
  exit non-zero with an empty queue) but no longer swallows a missing binary.

- **`JobResult.result()` returns `int | None`**: a COMPLETED job whose exit
  code cannot be recovered returns `None` rather than raising.
- **`SubmissionError` also carries `argv`** so `--verbose` can print the
  command; D13 listed only stderr and the script.
- Committed as 7dcb0af. 360 tests.

## Task 2 — D12 + D7 (JobSpec / resolve / Job, interactive attribute)

- **No convenience `submit()` helper.** D12 forbids `Job(command, cpu=4)`;
  I also did not add a module-level `hpc_runner.submit(spec)` wrapper. The
  README quick start is four lines (`load_config`, `get_scheduler`,
  `resolve`, `submit`). Add a wrapper if that feels too long.
- **`get_scheduler(name=None, *, config=None)`** loads a fresh config when
  `config` is None. That is the one place a default config load remains; it
  is a fresh `load_config()`, never a cache. `resolve(spec, config, host)`
  has no defaults at all.
- **Arrays live on the spec**: `JobSpec(array="1-100")` → `Job.array:
  ArrayRange`. `JobArray` is deleted; `scheduler.submit(job)` returns an
  `ArrayJobResult` (subclass of `JobResult`) when `job.array` is set, so
  there is one submit method. D9 refers to `ArrayJobResult.task_stdout_path`
  so the subclass keeps that name.
- **`array` + `interactive=True`** raises `NotSupportedError` from `resolve`.
- **`JobResult.job_id` is `None`** for a completed interactive run (D7.5),
  `job` is `None` for a handle rebuilt from an id (`scheduler.result(id)`).
- **`LocalScheduler` raises `NotSupportedError`** for arrays and for any
  dependency (it silently ignored dependencies before; D8 says nothing may
  silently degrade).
- **Scheduler passthrough** on batch SGE renders as one `#$ <tokens>` line,
  so `qsub` parses it exactly as it would the command line (B1).
- **`project`** added to `JobSpec`/`Job` now (D14.1) because it is just one
  more scalar in the dataclass; SGE renders `-P`.
- **`venv` semantics in this task**: None → capture, str → path, False →
  none. D4's `${CONFIG_DIR}` expansion and the activate-file check land in
  task 4.
- **`--depend` grammar** in this task: `TYPE:ID[:ID...]` or bare `ID`
  (bare = afterok, matching `after()`'s default). Task 5 adds the comma form
  and repeatability per D8.

## Task 3 — D1 + D2 + D14 + D11 (merge model, types, strict config)

- **`Job.scheduler_args` keeps the whole per-scheduler mapping** and each
  backend reads its own key. D14.3 says "only the active scheduler's list
  kept at resolve time", which would need `resolve` to know the scheduler.
  Keeping `resolve` scheduler-agnostic seemed more in the spirit of D12.
- **`types.T.tools` across config files follows the D1 list rule** (append
  + dedupe, `{ set = [...] }` to replace) rather than a special replace rule,
  so there is one list rule everywhere.
- **`[tools.x.options]` are not applied when a type is named** (D2 says the
  tool inferred from argv is not consulted; I extended that to the options
  of tools the type references, since the type is "the whole spec").
- **Unknown `[schedulers.<name>]` section is a `logging` warning** and is
  printed by `hpc config check`; no `warnings.warn`.
- **Line numbers** come from scanning the raw TOML text for the section
  header and key (tomllib has no positions); fallback to the header line.
- **`hpc config resolve` takes only a command and `-t`**, no other `run`
  options, since it is for inspecting config, not a dry run.

## Task 4 — D3 + D4 + D5 + D15 + B4 (environment, expansion, quoting)

- **`${CONFIG_DIR}` in a spec (Python/CLI) value is an error**, since there
  is no config file for it to refer to. `${CWD}`, `${USER}`, `${GIT_ROOT}`
  work everywhere; `${GIT_ROOT}` outside a git checkout is an error.
- **`env_passthrough` with `inherit_env = true` is silently ignored** (the
  variables are inherited anyway); it still shows in provenance.
- **`--verbose` lifts the module stderr redirect** by passing `verbose=`
  through `scheduler.submit(job, verbose=...)` / `preview(job, verbose=...)`
  rather than putting a field on `Job`. D15 says only that `--verbose`
  lifts it.
- **The modlog temp file** is created once per script with `mktemp` and
  removed by the single EXIT trap (shared with the sentinel publish on SGE).
- **`module purge` failing is fatal**, same as a failed load.
- **Expansion is applied to `queue`, `name`, `project`, `mem`, `time`,
  `scheduler_args` tokens** as well as the strings D5 lists, so that `$VAR`
  behaves the same in every config string. `command` is never expanded.
- **`module_system` key** added to `[schedulers.<name>]` with values
  `"modules" | "lmod" | ""`; only `"modules"` has an effect today
  (`MODULES_VERBOSITY=concise`).

## Task 7 — sweep (docs, golden tests, QA hygiene, CI)

- **`--wait` without `--batch` is a usage error**; `--local` deleted in
  favour of `--scheduler local` (QA §5 CLI gaps).
- **`--env KEY=VALUE` and `--resource name=value`** added to `hpc run`
  (QA §4 rows); no short flags.
- **`textual` stays a hard dependency.** QA §5 suggests a `[tui]` extra;
  that is a packaging decision for you.
- **Python floor stays 3.10.** QA 7.f records "raise to 3.11: yes" as a
  position, not a decision, so nothing changed.

## Findings from the testbed research that touch the code (../testbed/TESTBED_RESEARCH.md)

- **Exit 100 is error state, not requeue** (sge_shepherd.8): D8's "SGE
  re-queues a job whose script exits 100" is wrong in detail, but 113 is
  still a safe choice (99 requeues, 100 errors). The docstring next to the
  constant should say that.
- **`-hold_jid` treats a parent that exited 100 as never complete**, so a
  dependent stays held forever. Our guard exits 113, not 100, so this does
  not bite, but it is worth a doc sentence.
- **`qacct` cannot mark an `h_rt` kill**: it shows `failed 100` and
  `exit_status 137` like any signal death; `failed 37` appears only for
  qmaster enforcement. Mapping `TIMEOUT` needs `ru_wallclock` against the
  requested `h_rt`, or stays unmapped. Task 5 maps `failed 37` only.
- **`$TASK_ID` in `-o` expands to `0` for non-array jobs**, so task 6 must
  only append `.${hpc.task_id}` for arrays (the brief already says so).
- **`qrsh` defaults to `-now y`**: `hpc run xterm` fails immediately when
  no interactive slot is free instead of queueing. Product question: should
  interactive submits pass `-now no` by default, or expose it? Not changed.
- **`qrsh` ignores `-o`/`-e`**, consistent with D9.6 (`stdout_path` is
  `None` for interactive runs).
- **`qrsh -V`/`-v` do apply when a command is given** (code, not the man
  page), so D3's `-V` default works for interactive runs too.

- Agent calls I accepted: `ArrayJobResult.wait()` keeps returning
  `dict[int, JobStatus]` (D16 will revisit); `ArrayJobResult.result()`
  raises `NotSupportedError`; dependencies are spec-only (config layers
  cannot name job ids); `after()` on an interactive result raises
  `ValueError`; a Pipeline step name overrides `spec.name` as before;
  `HpcMonitorApp` still auto-detects the scheduler.
- **The untracked root `Makefile` is broken**: `uv run --frozen` with no
  `uv.lock` deleted `.venv` and recreated it on Python 3.13, and it points
  sphinx at `docs/` where `conf.py` lives in `docs/source/`. The agent
  rebuilt `.venv` per `sourceme`. Fix or delete that Makefile.
- Committed as task 2. 441 tests.

- Agent calls I accepted: `scheduler_args` lists follow the D1 verbs but
  **skip the dedupe** (argv repeats `-l`); `resources = { remove = [...] }`
  entries are full `{ name, value }` tables matched by name (a bare name
  string is not accepted); `{ set = [...], add = [...] }` together is an
  error; an unknown `-t TYPE` error lists the defined types (no file/line,
  it came from the command line); `array` is rejected in config.
- **Behaviour change to know about**: a spec list now appends to the config
  list (D1), so `hpc run -l gpu=1 -- cmd` passthrough appends after any
  config `scheduler_args`, and `JobSpec(modules=[...])` adds to the config
  modules. Use `Set([...])` to replace.
- **Config loads lazily in the CLI** so `hpc config check/init/path` still
  work when the config is broken; every other command refuses a broken
  config before touching the scheduler.
- Committed as task 3. 544 tests.

- Agent calls I accepted: a `$` that starts no variable (`$1`, `$(cmd)`,
  trailing `$`) stays literal rather than demanding `$$`; `${A:-x}` style
  is an "unsupported" error; a venv path containing `${hpc.*}` is rejected
  by the activate check (venvs must be on the shared filesystem);
  `${CONFIG_DIR}` is bound per file at load time, so loaded config values
  show the absolute directory; `$${hpc.cpus}` (literal) is still rendered
  as a runtime value, a known edge case.
- **`${hpc.*}` in `name`/`queue`/`stdout` directives** is carried through
  but not rendered on SGE until task 6.
- Committed as task 4. 649 tests.

## Task 5 — D8 + D16 fixes (dependencies, SKIPPED, batched status)

- **Bare-id parent on the CLI** (`--depend afterok:123`): hpc-runner cannot
  know whether 123 is an array job, and arrays now publish per-task
  sentinels. Ruling: the guard looks for `123.rc`, and if absent falls back
  to every `123.*.rc` present (at least one required), applying the test to
  all of them. `-hold_jid` guarantees every task has published first.
- **Interactive job with `--depend`** now raises `NotSupportedError`
  ("use --batch") instead of silently ignoring the dependency, which is
  what happened before this session.
- Agent calls I accepted: `qacct -j <base> -t <task>` for array task ids
  (per the man page); an active array base id reports a running task's
  state, else the first task's; `HPC_EXIT_DEP_FAILED` lives in
  `hpc_runner.core.result` and is not re-exported at top level; `Pipeline`
  builds parent dependencies with task lists so array steps work.
- **`failed 37` → TIMEOUT only.** An execd `h_rt` kill stays `FAILED` with
  exit 137 (see the research findings above).

- Committed as task 5. 730 tests.

## Task 6 — D9 + D10 (output paths, Memory/Duration)

- **`memory_per` defaults to `"slot"`** for SGE, which is today's behaviour
  (the value went straight to `-l mem_free=` and SGE multiplies by slots).
  `mem` means per job, so the renderer divides by `cpu`.
  *Reversed 2026-10-30:* the default is `"job"`. The premise was wrong: a
  stock `mem_free` is a requestable, which SGE does not multiply by slots,
  so the division under-requested memory on a default site (including the
  testbed). D10 records the corrected default.
- **`${hpc.host}` in an output pattern** makes `stdout_path` `None` (the
  node is unknown at submit) and `read_stdout()` raises with a clear
  message; `${hpc.cpus}`/`${hpc.tmpdir}` in a path are config errors.
- **`read_stdout()` returns `None`** for interactive runs and for a missing
  file, instead of `""`.
- **Local scheduler ignores `mem` and `time`** (no enforcement added).
- **Job-name sanitisation** on SGE: a leading digit gets a `j` prefix,
  other invalid characters become `_`; the sanitised name is what appears
  in `-N` and in the recorded output path.

- Rulings on the agent's questions: SGE `-o`/`-e` are always rendered
  absolute (joined onto the resolved workdir) so `use_cwd = false` cannot
  desynchronise the recorded path; a directory component depending on
  `${hpc.*}` is rejected (SGE discards output to a missing directory);
  `read_stdout()` on an array result raises and `read_task_stdout(n)` is
  added; merged stderr gives `stderr_path = None`; `stdout = ""` is a
  schema error.
- Agent calls I accepted: zero `mem`/`time` rejected; bare fractional
  `mem` rejected; duration units lowercase only; in colon forms only the
  first field may be 60 or more; the SGE invalid-name character set
  (whitespace, non-printable, `/ : @ \ * ?`) is from memory and should be
  confirmed on the testbed.

- **Bug caught in review**: the local scheduler did not sanitise job
  names, so a name with `/` broke the default output path. Baseline
  sanitisation (whitespace, `/`, non-printable → `_`) now lives in
  `BaseScheduler.job_name()`; SGE adds its own rules.
- Committed as task 6. 888 tests.

## What is left from QA_REVIEW.md after tonight

Section 6 order, item by item:

1. B1, B2, B5, B6, B7 fixed; golden-script and `bash -n` tests in task 7.
2. B4 quoting done (`sh` filter everywhere).
3. Shared environment template done; batch path is directives-only.
4. Typed schema with unknown-key errors done (B8); merge semantics in one
   place; kwargs follow them.
5. JobSpec / resolve / Job split done; config explicit; cache fixture gone.
6. Scheduler names out of core: `scheduler_args` dict done, `Resource._sge_*`
   and task-id delegation in task 7.
7. Hooks: `venv=false`, `--env`, `--resource`, output path on `JobResult`,
   `scheduler.result(id)` done. **`pre_commands`/`post_commands` not built:
   D6 says deferred.**
8. Exception hierarchy, timeouts, logging done.
9. **Slurm backend: not started.** With the seam, units, `${hpc.*}` and
   the shared template in place it is now `slurm/args.py`, a short
   template, `slurm/parser.py` and a scheduler class.

Still open (decisions for you, not bugs):

- D16 pipeline lifecycle (parked; `Pipeline` only got the minimal
  adaptation plus batched `wait()`).
- `textual` as a `[tui]` extra (QA §5 packaging).
- Python floor 3.11 (QA 7.f position).
- ~~`TIMEOUT` on SGE for execd `h_rt` kills.~~ Decided 2026-10-29: no inference, stays `FAILED`/137.
- ~~`qrsh -now no` for interactive submits.~~ Done 2026-10-29: always passed, no key.
- TUI status-filter popup does not list SKIPPED (only the table colours it).
- `ruff check tests` has ~24 pre-existing lint errors in TUI tests; CI lints
  `src` only. Worth adding `tests` to the lint job once cleaned.
- The root `Makefile` (untracked) is broken, see task 2 notes.

- Agent calls I accepted in task 7: `--wait` with `--array` is allowed
  (arrays are always batch); `--resource gpu=2` keeps the value as the
  string `"2"`; `Pipeline.wait()` reports an array step as the common task
  status, else the first task that did not complete (interim until D16).
- Drift the sweep found and fixed worth knowing: README/doc examples that
  quoted the whole command as one word (`hpc run "python train.py"`) would
  have run a command named `python train.py` after `shlex.join`; the
  auto-detection order in the README was wrong; `hpc config init --global`
  never existed.
- CI now lints `tests` as well as `src`, builds docs with `-W` via the
  tracked `docs/Makefile`, and has an `integration` job with `if: false`.
- Committed as task 7. 1003 tests, 1 skip.
