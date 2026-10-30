# hpc-runner QA review — 2026-10-28

Scope: full read of `src/hpc_runner` (core, schedulers, cli, workflow, templates;
TUI skimmed), test suite layout, README and Sphinx docs. Every bug below was
reproduced on this checkout, not inferred from reading.

Baseline: 327 tests pass, `mypy --strict` clean, `ruff` clean.

## Verdict

The skeleton is right: a scheduler-agnostic `Job`, a `BaseScheduler` with a
renderer registry, one template and one parser module per scheduler, a lazy
registry, and config-driven resource names. That is the correct shape for
adding Slurm and PBS.

Three things stop it being a solid base today:

1. The config/passthrough layer has correctness bugs in advertised features
   (`--` passthrough, `resources` merging, afterok from the CLI, `read_stdout`
   on SGE). None are covered by tests.
2. `Job` is not the "pure data container" its docstring claims. It resolves
   against a global config singleton inside `__init__`, which couples the model
   to process state and is why every test needs a cache-reset fixture.
3. Scheduler identity leaks into core (`sge_args`/`slurm_args`, `_sge_resource`,
   `base.index` array ids) and the `BaseScheduler` contract is not honoured by
   its own subclasses, so Slurm would copy SGE's shape rather than fill in a
   contract.

Recommended order of work is at the end.

## 1. Confirmed bugs

**B1. `--` passthrough is broken for batch SGE jobs.** `hpc run --batch -q batch.q -l gpu=2 -- python train.py` renders

```
#$ -q
#$ -batch.q
#$ -l
#$ -gpu=2
```

`SGEScheduler._build_directives` iterates `raw_args` token by token and prefixes
each with `#$`. `build_submit_command` (which would have passed them correctly on
the `qsub` command line) is dead code: `_submit_batch` runs `qsub <script>` with
no options. Interactive mode happens to work because `_build_qrsh_command`
extends the argv. No test renders directives with `raw_args` set. `scheduler.py:244`.

**B2. Config merge crashes on `resources`.** `_merge` deduplicates lists with a
`set`, and `resources` is a list of dicts. Any two layers that both define
`resources` (defaults + a type, or project + local file) raise
`TypeError: unhashable type: 'dict'`. The "fully populated" example in README.md
and `docs/source/configuration.rst` triggers it. `config.py:147`.

**B3. `Job(["bash", "-c", "echo hello world"])` loses quoting.** The list form
is joined with `" ".join`, so the job runs `bash -c echo hello world`. The CLI
path uses `shlex.join` and is correct. `job.py:193`.

**B4. Nothing rendered into the script is shell-quoted.** `env_vars`,
`env_prepend`, `env_append`, module names, module paths, venv path and the
cleanup `rm -f` path go into the template raw. `env_vars={"MSG": 'say "hi"'}`
produces `export MSG="say "hi""`. Add a `sh` Jinja filter wrapping `shlex.quote`
and use it everywhere except `job.command`, which is deliberately raw.

**B5. Kwargs do not follow config merge semantics.** In TOML, `modules = ["-",
"x"]` resets the list and plain lists append-with-dedup. Passed as a kwarg,
`modules=["-", "x"]` renders `module load -`, and `modules=["x"]` replaces the
config list outright. This is the general form of the "modules overwritten"
bug already found. `job.py:243`.

**B6. CLI `--depend afterok:123` is silently afterany on SGE.** The type prefix
is split off and discarded, and the afterok guard in `batch.sh.j2` only fires
for programmatic `job.dependencies`, never for the `job.dependency` string.
`scheduler.py:257`, `batch.sh.j2:106`.

**B7. `result.read_stdout()` is always empty on SGE.** `SGEScheduler` does not
override `get_output_path`, so the README Quick Start example prints nothing.
The path is knowable at submit time (`<cwd>/<name>.o<jobid>` or the `-o` value)
and should be recorded on the `JobResult` then, not derived later.

**B8. Unknown config keys are accepted silently.** `[defaults] modles = [...]`
loads without complaint. For a tool whose purpose is removing "works for me"
drift, a typo in a module list must be an error.

**B9. Documented config that does nothing.** `stdout = "hpc.%N.%J.out"` is
Slurm-ish syntax, SGE wants `$JOB_NAME.$JOB_ID`, and nothing translates.
`[schedulers.sge] merge_output` and `[defaults] scheduler = "auto"` are
documented and shipped in `defaults/config.toml` but never read.

**B10. `hpc config show` imports `tomli_w`**, which is not a declared dependency.

**B11. Missing binaries crash.** `get_status` catches `CalledProcessError`
(impossible, `check` is not set) but not `FileNotFoundError`, so a host without
`qstat` gets a traceback instead of `SchedulerError`.

## 2. Idiomatic Python

What is good: src layout, hatch + hatch-vcs, strict mypy and ruff both clean,
`from __future__ import annotations`, `pathlib`, `tomllib` with the 3.10 shim,
dataclasses for value types, lazy imports in the registry, Jinja with
`trim_blocks`/`lstrip_blocks`.

What to fix:

- **`Job.__init__` lists its 20-odd fields three times** (signature, overrides
  dict, attribute loop). The custom `JobAttribute` descriptor exists to give
  introspection that `dataclasses.fields()` already provides. Either derive the
  lists from the descriptors or replace the descriptors with a
  `@dataclass(kw_only=True)` and a `RENDERABLE` tuple. The current form will
  drift the next time a field is added.
- **Hidden global state.** `get_config()` cache, the Jinja `_env` singleton and
  `LocalScheduler._job_counter`. A library caller cannot hold two configs, and
  `Job()` cannot be constructed without touching process-wide state. Make
  `config` an explicit parameter with `get_config()` as the default.
- **The exception hierarchy is defined but not used.** `SubmissionError`,
  `SchedulerError`, `ValidationError`, `ConfigNotFoundError` have zero raise
  sites. Callers get `RuntimeError` from a failed job-id parse, raw
  `CalledProcessError` from `qsub`, `ValueError` from an unknown scheduler name
  and from `get_job_details`. A programmatic user cannot write
  `except SubmissionError`.
- **Circular import in the CLI** (`main` imports subcommands at the bottom,
  subcommands import `main`) papered over with three `type: ignore[has-type]`.
  Move `Context` and `pass_context` to `cli/_context.py`.
- **Function-local imports** that exist to dodge the `Job` ↔ `config` cycle are
  a symptom of the layering problem in section 3.
- **`DependencyType` is a `str` Enum but `Job.dependency_type` is `str`** and
  `Pipeline` stringifies it. Use the enum end to end.
- `JobResult(job_id="interactive")` is a magic sentinel. `wait(timeout=0)`
  means forever because of `if timeout and`.
- **Subprocess calls are ad hoc**: no `timeout=`, `check` varies, `errors=`
  varies. One `_run(argv, *, timeout)` helper per scheduler gives a single seam
  to test through instead of the global `patch("subprocess.run")` in
  `conftest.py`, which patches every subprocess in the process.
- `logging` is used only in `sge/scheduler.py` and `--verbose` never configures
  it. Logging every argv at DEBUG is the single most useful debugging aid for a
  tool that shells out.

## 3. Structure and readiness for Slurm/PBS

**Scheduler names baked into core:**

- `Job.sge_args` / `slurm_args` / `pbs_args` and
  `BaseScheduler.get_scheduler_args` via `getattr(job, f"{name}_args")`. Replace
  with `Job.scheduler_args: dict[str, list[str]]`.
- `Resource._sge_resource` / `_slurm_gres` fields, unused.
- `ArrayJobResult.task_id` formats `f"{base}.{index}"`. That is SGE/PBS;
  Slurm uses `base_index`. Delegate to the scheduler.
- `JobStatus.TIMEOUT` is never produced. SGE's `qacct failed` code carries it.

**The base contract is not honoured by its subclasses.** `generate_script` is
declared `(job, array_range)` but SGE adds `keep_script, script_path` and Local
adds `stdout_path, stderr_path`. `generate_interactive_script` likewise. The CLI
dry run calls the base signature and only works because of defaults. Define one
shape and keep it. A small `RenderRequest` dataclass (`mode`, `array`,
`script_path`, `keep_script`) is the least brittle option.

**Two submission paths, one dead.** `render_args` / `build_submit_command`
produce a `qsub` argv that `_submit_batch` never uses, because everything goes
in as `#$` directives. Pick one. Directives-only is simpler, works for
`#SBATCH` and `#PBS` identically, and means `--dry-run` shows exactly what is
submitted. Delete the argv path for batch (keep it for `qrsh`/`srun`, which have
no directives). The dead path is where B1 hid.

**Three copies of the environment template.** `sge/batch.sh.j2`,
`sge/interactive.sh.j2` and `local/job.sh.j2` share ~70 identical lines (module
init, purge, use, load, venv, unset, env_*, MAKEFLAGS). Extract
`templates/_environment.sh.j2` and `{% include %}` it. A scheduler template
then owns only its directives, any scheduler shim (the afterok sentinel) and
the exec line. Slurm becomes ~20 lines of template.

**`sge/scheduler.py` is 1000 lines, half of it XML parsing** for the TUI.
Move `_parse_qstat_j_xml` and helpers into `sge/parser.py` so `scheduler.py`
is submit/status/cancel. That is also the split Slurm will want
(`squeue --json`, `sacct -P`).

**Config is untyped nested dicts all the way down.** `_merge` has list
semantics (append + dedup, leading `"-"` resets) that are documented only in
the README and break on lists of dicts (B2). Typos are silent (B8). Recommend a
typed `JobDefaults` dataclass per layer with a validation pass that rejects
unknown keys and wrong types, and documents merge rules in one place. This is
also where `mem`/`time` should become value types (`Memory`, `Duration`)
parsed once in core: Slurm wants `16G`, PBS wants `16gb`, `4:00:00` works for
both but `1d` for neither.

**`Job` conflates three layers:** what the user asked for, resolution against
config, and the resolved result. Suggested split:

```
JobSpec       dataclass, exactly what the caller wrote, no defaults applied
resolve(spec, config) -> Job     pure function; the only place merge rules live
Job           fully resolved; what schedulers render; no config access
```

Then `Job` really is a data container, tests need no cache fixture, and the
CLI, `Pipeline.add` and library callers all go through `resolve`.

**Detection.** PBS is detected only if `PBS_CONF_FILE` is set; `qsub --version`
printing `pbs_version` is more reliable. The SGE `qstat -help` sniff is fine.

**No site override of templates.** `module_init_script` is the only hook.
Allow `template = "/site/path.j2"` under `[schedulers.<name>]`.

## 4. Programmatic UX: can a user do the non-default thing?

Walked each scenario against the current API.

| Scenario | Today | Recommendation |
|---|---|---|
| Add a module on top of the tool's list | `extra_modules=` works; `modules=` silently replaces; `"-"` reset leaks (B5) | One rule for kwargs and config: lists merge like layers, leading `"-"` resets. `extra_*` becomes sugar or goes. |
| Set / prepend / append env vars | Kwargs and config work; values unquoted (B4); not on the CLI | Quote. Add `--env K=V` (repeatable). |
| Run shell before / after the job | Not possible. Only `module_init_script`, per scheduler not per job | Add `pre_commands: list[str]`, `post_commands: list[str]` on Job, type and tool config. Post runs in the EXIT trap so it sees failures and `$_exit_code`. Optionally `setup_script = path` (sourced). |
| Use no venv, or a different one | Auto-captured from `VIRTUAL_ENV`; only escape is the undocumented `venv = ""` | `venv = false` in config, `--no-venv` on CLI, `venv=None` vs `venv=False` in the API. Surprising default when the user wants module python. |
| Request a license / site resource | `ResourceSet` works; config works until merged (B2); CLI only via raw `-l` | Fix B2. Add `--resource name=value`. |
| Find my output file | Impossible on SGE (B7) | Set `-o` explicitly to a known path and store it on `JobResult` at submit. Deterministic across schedulers. |
| Reconnect to a job after my script died | No `JobResult.from_id`; `JobResult` requires a `Job` | `scheduler.get_result(job_id)`; make `job` optional on `JobResult`. |
| Preview the script | Works, but signatures differ per scheduler | `scheduler.preview(job, interactive=False) -> str` as the stable entry. |
| Type and tool together | `-t gpu python x.py` discards `[tools.python]` entirely | Layer defaults → tool → type → kwargs, or document that `-t` is exclusive. Layering is what a user expects. |
| Mixed dependency types | `.after()` sets one type for all parents | Accumulate `(result, type)` pairs; `after(ok=[...], any=[...])`. |
| Pass raw scheduler flags | `raw_args` tokens | Fine once B1 is fixed. |
| Stop `-V` leaking my shell | `inherit_env=True` default with `module purge` | `-V` exports the submitting shell's `LD_LIBRARY_PATH`, `PATH`, etc., which is the exact "works for me" vector. Consider `inherit_env = false` by default with an `env_passthrough = ["DISPLAY", ...]` allowlist. At minimum document the trade-off. |
| Job name sanitisation | `user_cmd`; SGE rejects names starting with a digit | Sanitise in the scheduler. |

## 5. What else belongs in the review

- **Test strategy.** All 327 tests are unit tests mocked through one global
  `subprocess.run` patch. There are no golden-file tests of rendered scripts
  (would have caught B1 and B5) and no `bash -n` syntax check of rendered
  scripts (would have caught B4). Both are cheap. An integration job against
  a Son-of-Grid-Engine container in CI is the next step; failing that, run the
  README snippets against the `local` scheduler so the docs cannot drift.
- **Docs drift.** README and Sphinx promise `%N`, `merge_output`, and
  `read_stdout` on SGE. Executable docs fix this permanently.
- **Packaging.** `tomli_w` missing (B10). `textual` is a heavy hard dependency
  for a CLI; make it a `[tui]` extra. `requires-python >= 3.10` is fine.
- **Robustness.** No subprocess timeouts; `wait()` cannot be interrupted
  cleanly; `envsubst` is assumed present on compute nodes for MAKEFLAGS.
- **Security posture.** The command is deliberately raw shell. Config files
  from a cloned repo are also executed (module names, env values) without
  quoting. Acceptable for the threat model, but state it, and quote (B4).
- **Local scheduler state is in-process only**, so `hpc status` from another
  shell sees nothing. Fine for a test backend; document it.
- **CLI gaps.** No `--env`, `--resource`, `--priority`, `--no-venv`, `--pre`.
  `--wait` is meaningless with the interactive default. `--local` duplicates
  `--scheduler local`.
- **Observability.** `--verbose` should set `logging` to DEBUG and every
  subprocess argv should be logged.

## 6. Recommended order

1. Fix B1, B2, B5, B6, B7. Add golden-script tests and a `bash -n` test for
   every rendered template. These are the advertised features.
2. Quote everything in templates (B4). Add the `sh` filter.
3. Extract the shared environment template. Make directives the single batch
   path and delete `render_args`/`build_submit_command` for batch.
4. Typed config schema with unknown-key errors (B8); document merge semantics
   once; make kwargs follow them (B5).
5. Split `JobSpec` / `resolve()` / `Job`; make `config` an explicit parameter;
   drop the cache-reset fixture.
6. Remove scheduler names from core: `scheduler_args` dict, delegated array
   task ids, drop `Resource._sge_*`.
7. Add the hooks: `pre_commands`/`post_commands`, `venv=false`, `--env`,
   `--resource`, output path on `JobResult`, `JobResult.from_id`.
8. Use the exception hierarchy; add subprocess timeouts and logging.
9. Then Slurm, which at that point is `slurm/args.py`, a 20-line template,
   `slurm/parser.py`, and a ~300-line scheduler.

## 7. Programmatic API and Pipeline (independent sub-agent review, 2026-10-28)

A fresh reviewer was briefed on D12 only and asked to walk realistic usage
of the Python API. Reproduced in full; it independently reached D8 and D12.

### 7.a Verdict

The Python API is a thin, honest wrapper over `qsub`, and for "submit one job, wait, read exit code" it is fine. The `Pipeline` class, however, is a name-registry plus a `for` loop over `scheduler.submit()`, and it stops exactly where real workflows start: it cannot hold an array job, cannot depend on anything submitted outside itself, cannot retry a stage that *ran and failed* (only one that failed to *submit*), cannot be cancelled as a unit, cannot be resumed from another process, and its `wait()` can spin forever because `JobStatus.UNKNOWN` is treated as "still running". Two of the pain points are outright bugs: array tasks all overwrite the same `${JOB_ID}.rc` sentinel so the afterok guard on an array parent is unsound, and `AFTER`/`AFTERNOTOK` are accepted by the API and then silently rendered as plain `-hold_jid` (afterany) on SGE. The shape to move towards is: `JobResult` as a cheap, reconstructible future with batched status polling; `Pipeline` as a declarative DAG of steps with per-edge dependency types, a persisted manifest, and explicit `submit / status / wait / cancel / retry` verbs; and the context-manager auto-submit removed.

### 7.b Usage walkthrough

### 1. One job, wait

```python
r = Job("python train.py", cpu=4, mem="16G").submit()
status = r.wait(timeout=3600)
print(r.returncode, r.read_stdout(tail=20))
```

Works, with three surprises. `wait()` calls `is_complete` then `status` again on return (`core/result.py:81-85`), and on SGE each `status` is up to three subprocesses (`qstat -j`, `qstat`, `qacct`; `sge/scheduler.py:503-535`), so one poll iteration is ~6 process spawns. `returncode` does the same again (`result.py:64-66`). `timeout=0` is falsy and means "forever" (`result.py:82`). And on SGE `read_stdout()` always returns `""` because `SGEScheduler` never overrides `get_output_path` (`schedulers/base.py:154` returns `None`; only `local/scheduler.py:163` implements it), despite `docs/source/programmatic_api.rst:90` showing it. The only real backend cannot read job output.

### 2. Fan-out / fan-in of N jobs

```python
p = Pipeline("sweep")
for i in range(N):
    p.add(f"python sim.py --seed {i}", name=f"sim{i}", cpu=2)
p.add("python merge.py", name="merge", depends_on=[f"sim{i}" for i in range(N)])
p.submit(); p.wait()
```

Awkward: N separate `qsub` calls when the scheduler's answer to this is an array job, and `Pipeline.add()` constructs a `Job`, never a `JobArray` (`pipeline.py:83`). There is no fan-in helper, so the user builds the name list by hand. `wait()` then polls each of N+1 jobs sequentially with its own `qstat`/`qacct` pair (`pipeline.py:218-220`) rather than one `qstat` for all.

### 3. Multi-stage, stage 2 depends on all of stage 1

Same code as above with `depends_on` listing every stage-1 name. The dependency type is per *job*, not per *edge* (`PipelineJob.dependency_type`, `pipeline.py:25`), which is fine here but see 6.

### 4. Retrying a failed stage

Impossible without rebuilding. After `wait()`, suppose `train` FAILED. `pjob.result` is set, so `submit()` raises `"Pipeline has already been submitted"` (`pipeline.py:138-139`). The documented retry (`programmatic_api.rst:264-282`) covers only *submission* failure. The user must construct a fresh `Pipeline`, re-add `train` and every descendant, and has no help identifying descendants.

### 5. Resuming after the submitting process died

Impossible. `JobResult` needs a live `scheduler` and `Job` (`result.py:34-36`); the pending JobSpec/handle decision fixes the per-job half. But `Pipeline` holds no persisted state at all: no manifest, no `Pipeline.load()`, no `status()` that reads from job ids. A pipeline that takes two days is tied to a Python process that must survive two days.

### 6. Mixed dependency types

```python
p.add("cleanup.sh", name="cleanup", depends_on=["train"], dependency_type=DependencyType.AFTERANY)
```

Per-job only, so "run `report` after `train` succeeds *or* after `fallback` runs regardless" needs an intermediate no-op job. Worse: `AFTER` and `AFTERNOTOK` are in the enum (`dependency.py:16-17`) and accepted by `add()`, but the SGE template only has a guard for `afterok` (`batch.sh.j2:106`) and `_build_dependency_string` ignores the type entirely (`sge/scheduler.py:253-265`). Both silently become afterany. Nothing in `SGEScheduler` validates or raises.

### 7. Array job as a pipeline stage

Impossible (`add()` is `Job`-only). Also unsound if you wire it by hand: the sentinel path is `${JOB_ID}.rc` (`batch.sh.j2:23`), and every task of an array shares `$JOB_ID`, so the last task to exit overwrites the file. A dependent guard then sees whichever task finished last; a failed task 7 is masked by a successful task 100. `_read_exit_sentinel` even codifies this fallback to the base id (`sge/scheduler.py:484-485`). `ArrayJobResult.task_status()` runs `qstat -j 123.4` and then looks up `base_id` in the plain `qstat` table (`sge/scheduler.py:514-516`), so every task reports the same status; per-task status is nominal. `-hold_jid_ad` is mentioned in `dependency.py:10` and never used.

### 8. Cancelling a whole pipeline

```python
for r in p.results.values(): r.cancel()
```

No `Pipeline.cancel()`. Order matters: with `-hold_jid` (afterany) cancelling a parent *releases* dependents; they run unless `enforce_afterok` is on and the guard aborts them. Dependents must be cancelled first (reverse topological order). The user has no idea this is a concern.

### 9. The context manager

```python
with Pipeline("x") as p:
    p.add(...)
    p.submit()      # perfectly reasonable thing to type
# -> RuntimeError: Pipeline has already been submitted, raised from __exit__
```

`__exit__` calls `submit()` unconditionally (`pipeline.py:231-233`), and `submit()` raises if everything is already submitted. Submission errors surface as if raised by the `with` body; `keep_script`, dry-run, scheduler override cannot be passed; and the value a `with` block normally promises (cleanup on exit) is absent.

### 7.c Prioritised design changes

### 1. Make `JobResult` a cheap future and fix `wait()` (highest)

Problem: `is_complete` excludes `UNKNOWN` (`result.py:49-56`), so a job that aged out of `qacct` with sentinels disabled makes `wait()` loop forever; each poll is several subprocesses; `Pipeline.wait()` multiplies that by N; no batch status query exists on `BaseScheduler`.

```python
class JobStatus(Enum):
    ...
    @property
    def is_terminal(self) -> bool:
        return self in {COMPLETED, FAILED, CANCELLED, TIMEOUT}

class BaseScheduler(ABC):
    @abstractmethod
    def get_statuses(self, job_ids: Sequence[str]) -> dict[str, JobStatus]: ...   # one qstat
    def get_status(self, job_id): return self.get_statuses([job_id])[job_id]

@dataclass(frozen=True)
class JobResult:
    job_id: str
    scheduler: BaseScheduler
    job: Job | None = None                      # None when rebuilt from an id
    def poll(self) -> JobStatus: ...            # one round trip, never cached
    def wait(self, *, timeout: float | None = None, poll_interval: float = 5.0,
             unknown_after: float = 60.0) -> JobStatus:
        """Raise TimeoutError on timeout; return UNKNOWN if status stays UNKNOWN
        for `unknown_after` seconds rather than spinning."""
    def result(self) -> int:                    # concurrent.futures-style
        """wait(), then return exit code or raise JobFailed(status, job_id)."""

def wait_all(results: Iterable[JobResult], *, timeout=None, poll_interval=5.0,
             return_when: Literal["ALL_DONE", "FIRST_FAILURE"] = "ALL_DONE") -> dict[str, JobStatus]:
    """Groups by scheduler and calls get_statuses() once per tick."""
```

Trade-off: `get_statuses` adds one abstract method per backend; the `UNKNOWN` escape hatch means callers must handle a non-terminal return, but that is better than an unkillable loop.

### 2. Replace `PipelineJob` with a `Step` that carries per-edge dependencies and allows arrays and external parents

Problem: `add(command, name, depends_on, **job_kwargs)` conflates "what to run" with "where in the DAG", cannot take a `JobArray` or an existing `JobResult`/job id, and `dependency_type` is per-node (`pipeline.py:57-66`). `**job_kwargs` also means a typo in a Job kwarg errors inside `Job.__init__`, not at the call site the user sees.

```python
@dataclass(frozen=True)
class Edge:
    parent: str | JobResult          # step name, or an already-submitted job
    kind: DependencyType = DependencyType.AFTEROK

@dataclass
class Step:
    name: str
    spec: JobSpec | ArraySpec        # what the caller wrote, unresolved
    after: tuple[Edge, ...] = ()
    result: JobResult | ArrayJobResult | None = None

class Pipeline:
    def add(self, spec: JobSpec | ArraySpec | str, *, name: str | None = None,
            after: Iterable[str | Step | JobResult | Edge] = ()) -> Step: ...

# usage
sims = p.add(ArraySpec(JobSpec("python sim.py --seed $SGE_TASK_ID", cpu=2), 1, 100), name="sims")
p.add("python merge.py", name="merge", after=[sims])
p.add("notify.sh", name="notify", after=[Edge("merge", DependencyType.AFTERANY), Edge(prev_run_result)])
```

Keep the `str` convenience for `spec` so one-liners stay one-liners; everything else goes through `JobSpec`, which is where the already-decided `resolve(spec, config, host)` lives. Because `Pipeline` no longer mutates `job.dependencies`/`dependency_type` at submit time (`pipeline.py:157-158`), the frozen `Job` plan works: dependencies become an input to `resolve()`. Trade-off: `depends_on=["a","b"]` becomes `after=["a","b"]`; a tiny rename for a large gain in expressiveness. Slurm's `--dependency=afterok:1:2,afterany:3` is per-edge for exactly this reason.

### 3. Explicit run lifecycle; drop auto-submit on `__exit__`

Problem: see walkthrough 4, 8, 9. The pipeline has no verbs for the post-submit half of its life.

```python
class Pipeline:
    def submit(self, *, scheduler=None, dry_run: bool = False) -> PipelineRun
    def __enter__/__exit__   # removed, or: __exit__ only validates, never submits

@dataclass
class PipelineRun:
    pipeline: Pipeline
    results: Mapping[str, JobResult | ArrayJobResult]
    def status(self) -> dict[str, JobStatus]            # one get_statuses() call
    def wait(self, *, timeout=None, return_when="ALL_DONE") -> dict[str, JobStatus]
    def failed(self) -> list[str]
    def cancel(self) -> None                            # reverse topological order
    def retry(self, *, steps: Iterable[str] | None = None) -> PipelineRun
        """Resubmit the named (default: FAILED) steps and all their descendants,
        re-wiring `after` edges to the new ids; untouched steps keep their ids."""
    def save(self, path: Path) -> None                  # JSON manifest: step -> job_id, spec
    @classmethod
    def load(cls, path: Path, scheduler=None) -> PipelineRun
```

`dry_run=True` should render every step's script with placeholder parent ids and return them, which is also what a user wants before trusting the afterok guard. `save/load` is Nextflow's `-resume` / Snakemake's incomplete-rerun reduced to the minimum: a run directory with a manifest is what makes "the Python process died" survivable. Trade-off: `submit()` returns a new object instead of a `dict`; the `with` sugar goes. I would not keep `__exit__` submit even as an option: a context manager whose exit does the expensive, irreversible thing is the trap the test at `test_pipeline.py:116` enshrines.

### 4. Make SGE honest about dependency kinds and arrays

Problem: silent degradation of `AFTER`/`AFTERNOTOK` and the shared array sentinel (walkthrough 6, 7).

- In `SGEScheduler.resolve`/`_build_dependency_string`, raise `UnsupportedDependencyError(kind)` for kinds the backend cannot honour, unless `enforce_afterok` emulation covers them. `AFTERNOTOK` is emulatable with the same sentinel (guard inverts the test); `AFTER` (start-after) is not expressible with `-hold_jid` at all.
- Sentinel path `${JOB_ID}${SGE_TASK_ID:+.$SGE_TASK_ID}.rc`; guard iterates every task id of an array parent (the scheduler knows the range at submit time); use `-hold_jid_ad` for array-to-array edges of equal size.
- `ArrayJobResult.task_status` must read per-task state from `qstat -xml` (which does carry `tasks`), not look up the base id.

Trade-off: one more sentinel file per task; the guard loop grows with N but `-hold_jid` already guarantees they exist.

### 5. Exceptions

Problem: `SubmissionError` exists (`core/exceptions.py:12`) but SGE raises `RuntimeError` (`sge/scheduler.py:356,452`) and lets `subprocess.CalledProcessError` escape; `Pipeline` raises `RuntimeError` and `ValueError` for six different conditions. Users cannot `except` anything specific.

```python
class PipelineError(HPCToolsError): ...
class DuplicateStepError(PipelineError): ...
class UnknownStepError(PipelineError): ...
class DependencyCycleError(PipelineError): ...
class NotSubmittedError(PipelineError): ...
class JobFailed(SchedulerError):  # from JobResult.result()
    status: JobStatus; job_id: str; exit_code: int | None
```

Wrap `CalledProcessError` into `SubmissionError` with `stderr` attached; qsub's stderr is the only useful diagnostic and it is currently lost.

### 7.d Smaller hygiene items

- `Job.after(*jobs, type="afterok")` (`core/job.py:438-454`): `type` shadows the builtin, takes `str` not `DependencyType`, and *replaces* `dependency_type` while *extending* `dependencies`, so `after(a, type="afterok").after(b, type="afterany")` silently makes `a` afterany too.
- `Job.dependency_type: str` (`job.py:341`) vs `PipelineJob.dependency_type: DependencyType`, bridged by `str(...)` at `pipeline.py:158`. Pick the enum everywhere. `DependencyType(str, Enum)` with a hand-written `__str__` (`dependency.py:19`) should be `enum.StrEnum` (Python 3.11 is the floor).
- The terminal-status 4-tuple is duplicated at `result.py:51-56` and `result.py:148-153`; see `JobStatus.is_terminal` above.
- `JobResult.job_id == "interactive"` (`sge/scheduler.py:403`) is a magic string; `cancel()` on it runs `qdel interactive`. Use a distinct `InteractiveResult` or `job_id: str | None`.
- `_cached_status` (`result.py:38`) is never read or written.
- `ArrayJobResult.base_job_id` vs `JobResult.job_id`; no shared base/Protocol, so `Pipeline.results` cannot be typed to hold both. `ArrayJobResult.wait()` has no `timeout`.
- `JobArray.indices` is a property returning a fresh iterator (`job_array.py:44-46`); return `range(...)`. `submit_array` (`base.py:107`) lacks `keep_script`, and the SGE implementation writes the script to `/tmp` (`sge/scheduler.py:435`) while `_submit_batch` deliberately uses the shared cache dir with a self-delete; the two paths have drifted.
- `_topological_sort` (`pipeline.py:167-194`) is dead weight: `add()` refuses unknown names, so insertion order is already topological, and a cycle can only be introduced by poking `_name_map` as the test does (`test_pipeline.py:103`). Either allow forward references (then the sort earns its keep) or delete it. A `PipelineJob` from another pipeline passed to `depends_on` raises `KeyError` at `pipeline.py:176`, not a useful error.
- `add()` sets `job.name = f"{self.name}_{name}"` after `Job()` already ran `_generate_name()` (`pipeline.py:83-84`); with the default pipeline name this yields `pipeline_step_1`. Make the prefix explicit (`name_prefix=` or none) and do it in the spec, not by mutation.
- `get_job()` returns `None` for a missing name while `results[...]` raises `KeyError`; `results` builds a new dict on every access. Return `Mapping` and raise consistently.
- `read_stdout(tail=0)` and `wait(timeout=0)` are falsy and ignored (`result.py:82,105`); test `is not None`.
- `programmatic_api.rst:148-152` documents `j2.dependencies = [r1]; j2.dependency_type = "afterok"` attribute mutation as the low-level API; README `train.after(result1, type="afterok")`. Both go away with the frozen `Job`; update when the spec split lands.
- `Pipeline.wait()` returns `dict[str, JobResult]` (the same objects the caller already has) rather than statuses; `JobResult.wait()` returns `JobStatus`; `ArrayJobResult.wait()` returns `dict[int, JobStatus]`. Standardise on statuses.

---

### 7.e Maintainer notes

- One factual slip: the reviewer says Python 3.11 is the floor. `pyproject.toml`
  says `>=3.10`, so `enum.StrEnum` is not available without raising the floor.
  Either raise it (3.10 is EOL October 2026) or keep the hand-written `__str__`.
- Overlaps with DECISIONS.md: (c)1 `JobResult` as handle and (c)2 specs as
  input to `resolve` are D12; (c)4 and the `after()` items are D8, which
  independently reached the same per-task sentinel and per-edge type fixes;
  (c)5 is D13; the `"interactive"` sentinel is D7.
- New here, not in DECISIONS.md: `wait()` spinning forever on `UNKNOWN`;
  batched `get_statuses` on the scheduler; `PipelineRun` lifecycle with
  `cancel` in reverse topological order, `retry` of failed steps plus
  descendants, and `save`/`load` manifest; dropping context-manager
  auto-submit; array steps in a pipeline; external parents as edges;
  per-task array status being nominal today.

### 7.f Discussion notes on the pipeline lifecycle (D16) — PARKED for a future session

Status: parked 2026-10-28. The shape in 7.c may be the wrong starting point;
the maintainer's view is that the workflow layer probably needs wholesale
change, and that the first step is understanding real use cases and how a
user wants to *define a job list* (declaratively? from a Makefile? from
Python?) before choosing an API. Nothing below is decided. The notes record
the position reached so the next session can start from it.

**Correctness items that can proceed regardless of the design:**

- `wait()` treats `UNKNOWN` as "still running" and spins forever when a job
  has aged out of accounting with sentinels disabled. Fix: `JobStatus.is_terminal`
  plus an `unknown_after` escape in `wait()`.
- One `qstat` per poll for all ids (`BaseScheduler.get_statuses(ids)`),
  not three subprocesses per job per tick. Every backend has a bulk query
  and the TUI already uses it.

**Where the main session agreed with the reviewer's shape (7.c), for the record:**

- Per-edge dependency types (`Edge(parent, kind)`) with parents being step
  names, steps, or external results: this is D8's per-parent types applied
  to the pipeline, and edges become an input to `resolve()` per D12.
- A run object returned by `submit()` with `status`, `wait`, `failed`,
  `cancel`. Cancel must be in reverse topological order: on SGE `-hold_jid`
  is afterany, so cancelling a parent first *releases* its children.
- Context-manager auto-submit removed outright.

**Positions on the reviewer's open questions (not decided):**

- `retry()`: in the library. "Failed steps plus descendants, edges re-wired
  to new ids, untouched steps keep theirs" is graph bookkeeping every
  caller would get wrong the same way. No policy beyond that.
- `save()`/`load()`: minimal JSON manifest (step, spec, edges, job id) at a
  caller-chosen path; no run-directory convention, no automatic resume, no
  output caching.
- `return_when="FIRST_FAILURE"`: yes, default `ALL_DONE`.
- Python floor to 3.11: yes (3.10 EOL October 2026; drops the `tomli` shim,
  gains `StrEnum`).
- Disagree with keeping forward references to justify `_topological_sort`:
  insertion order is already topological because `add()` refuses unknown
  parents. Delete the sort, keep the rule.

**Questions for the future session:**

- What do real job lists look like for the primary user (EDA flows: lint,
  sim regressions, synthesis)? Are they DAGs at all, or mostly fan-out
  regressions plus a merge step?
- Should a job list be definable in TOML/YAML alongside the config, or only
  in Python? Does a Makefile already own the DAG, making the pipeline
  layer redundant for most flows?
- Is an array job the natural unit for regressions, making "array as a
  step" the main requirement rather than an edge case?
- What does "resume" mean to the user: re-attach to running jobs, or
  re-run only what failed?
