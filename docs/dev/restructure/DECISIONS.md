# hpc-runner: design decisions to settle before the refactor

Companion to `QA_REVIEW.md`. The review mixed two kinds of finding: bugs with
one correct fix, and places where the code does *something* but nobody has
decided what the tool should do. This file holds the second kind. Each entry
gives what happens today, the options, what a user most likely expects, and a
recommendation to argue with. Record the outcome under **Decision** and the
entry becomes the spec for the fix.

Bugs that need no discussion and are not listed here: B1 passthrough rendering,
B3 list-command quoting, B4 shell quoting, B7 output path, B10 `tomli_w`,
B11 missing-binary handling, the dead `build_submit_command` path.

---

## D1. What does it mean to set a list in a more specific layer?

Applies to `modules`, `modules_path`, `modules_path_append`, `resources`,
`unset_vars`, and `raw_args` if it ever becomes configurable.

**Today:** config layers append and dedupe; a leading `"-"` resets. Kwargs
replace. Lists of dicts crash. `extra_modules` exists because kwargs replace.

**The question:** when `[defaults] modules = ["gcc/12"]` and
`[tools.xcelium] modules = ["xcelium/23"]`, does an xcelium job load both or
only xcelium? And when a type says `modules = ["python/3.11"]` on top of a
tool, is that a *correction* (replace) or an *addition* (append)?

**Options:**

- (a) **Append everywhere, explicit reset.** Lists accumulate down the layers;
  `"-"` at position 0 resets. Same rule for kwargs. `extra_modules` goes.
  Simple, one rule, but the reset marker is a magic string inside data and
  the user cannot *remove one item*.
- (b) **Replace everywhere.** Most specific wins outright, like scalars.
  Predictable and what TOML users expect from "override", but every tool
  entry must repeat the site-wide modules, which is the drift the project
  exists to prevent.
- (c) **Append by default, with explicit verbs.** `modules = [...]` appends;
  `modules_replace = [...]` or a table form `modules = { set = [...] }` /
  `{ add = [...], remove = [...] }` for the rare cases. Kwargs the same:
  `modules=` appends, `modules=Replace([...])` or `replace_modules=`.
- (d) **Replace by default, with explicit append.** `modules = [...]` replaces;
  `modules_add = [...]` appends. Mirrors `modules_path` vs
  `modules_path_append`, which already exists.

**What users expect:** for *modules* specifically, append. A tool entry says
"this tool additionally needs X". For *resources*, append too (a GPU type adds
a GPU; it does not remove the scratch request). For `raw_args`, append. There
is no list where replace is the natural reading, but there are real cases
where one item must go (a type that needs a *different* python).

**Recommendation:** (c), implemented minimally: plain list = append+dedupe,
and a table form `{ set = [...] }` or `{ remove = [...] }` when needed. Drop
the `"-"` marker. Kwargs follow the identical rule through the identical
`_merge` so there is one implementation. Dedupe must compare dicts by value.

**Decision (2026-10-28):** (c). One rule, implemented once inside `resolve`:

1. A plain list in any layer, including `JobSpec`, appends to the
   accumulated list and dedupes by value, first-seen order preserved.
2. Explicit verbs as a table for the rare cases:
   `modules = { set = [...] }` replaces outright;
   `modules = { remove = [...], add = [...] }` applies remove then add.
   Python mirrors it: `modules=[...]` appends, `modules=Set([...])` /
   `modules=Edit(remove=[...], add=[...])` for the verbs.
3. The `"-"` marker and the `extra_modules` / `extra_modules_path` /
   `extra_modules_path_append` parameters are deleted; they are "append".
4. Dedupe: lists of scalars by equality; lists of named tables
   (`resources`) by `name`, last layer wins for the value. So a `gpu` type
   setting `gpu = 2` over a default `gpu = 1` requests 2, not both.
5. `resolve` records which layer contributed each item, for
   `hpc config resolve`.

---

## D2. How do `[defaults]`, `[tools.x]`, `[tools.x.options]`, `[types.x]` and the CLI layer?

**Today:** `-t type` selects `types.x` *instead of* `tools.<cmd>`. The tool
config is discarded entirely. Options specialise tools only.

**Options:**

- (a) **Exclusive** (today). A type is a complete alternative profile.
  Simple mental model; but `hpc run -t gpu python train.py` loses
  `[tools.python]`'s modules, which surprises.
- (b) **Layered:** defaults → tool → tool.options → type → CLI. A type is a
  modifier applied on top of whatever the command would have got.
- (c) **Layered, type first:** defaults → type → tool → CLI. The command is
  the more specific intent.
- (d) **Types can extend:** `[types.gpu] extends = "python"` or
  `inherit_tool = true` per type, exclusive otherwise.

**What users expect:** the README sells types for cases where the tool cannot
be inferred (`make`, scripts). In that use the tool entry is `make` or nothing,
so exclusive and layered agree. The divergence is `-t gpu python train.py`,
and there the user clearly wants both.

**Recommendation:** (b). Precedence reads left to right as "more specific
wins", and with D1's append rule the modules of both are loaded. Document the
chain in one place and have `hpc config resolve <cmd> [-t type]` print the
merged result with the layer each value came from.

**Decision (2026-10-28):** none of (a)–(d) as written. A type *defines a
flow and all its dependencies*; it is not a modifier. Types compose from
tool entries by reference rather than restating them:

```toml
[tools.xrun]
modules = ["xcelium/23.09"]
resources = [{ name = "xcelium_lic", value = 1 }]

[tools.slang]
modules = ["slang/0.9"]

[types.lint]
tools = ["xrun", "slang"]   # pulled in, in this order, then the keys below
cpu = 2
```

Resolution order is fixed by the tool, not configurable:

```
without -t:  defaults → tools.<argv[0]> → tools.<argv[0]>.options.<match> → spec
with -t T:   defaults → tools.<each of types.T.tools, in order> → types.T → spec
```

- With a type named, the tool inferred from argv is **not** consulted. The
  type is the whole specification; `hpc config resolve -t T` shows exactly
  what `[types.T]` says.
- One type per job. No multi-`-t`; a combined flow is its own named type.
- References are one level deep: tools do not reference tools, types do
  not reference types. A referenced tool that does not exist is an error,
  as is a named type that does not exist.
- Scalars: later wins. Lists: D1. Provenance records the layer name.
- `hpc run -t gpu python train.py` style modifiers are not a use case;
  a GPU request without a flow is `--queue`/`--resource` on the command
  line, and a GPU flow is a type that lists its tools.

---

## D3. Does a job inherit the submitting shell's environment?

**Today:** `inherit_env = true` by default, rendered as `qsub -V`, then
`module purge` and the configured loads run inside the job.

**The tension:** `-V` carries the submitter's `PATH`, `LD_LIBRARY_PATH`,
`PYTHONPATH`, `LM_LICENSE_FILE`, proxies, and whatever modules they had
loaded. `module purge` unwinds the module-managed parts *only if* the module
system on the node tracks them the same way; the rest leaks. That is precisely
the "works for me" failure. On the other side, `DISPLAY`, `HOME`, `USER`,
license variables and `SSH_AUTH_SOCK` are things a job legitimately needs.

**Options:**

- (a) Keep `-V` default; document the caveat. Easiest, least safe.
- (b) **Default off, allowlist.** `inherit_env = false`, plus
  `env_passthrough = ["DISPLAY", "LM_LICENSE_FILE", ...]` rendered as explicit
  `-v NAME=value` (SGE) / `--export=NONE,NAME=value` (Slurm). The job gets a
  clean login-shell environment plus named variables.
- (c) Default on, denylist (`unset_vars`, which exists). Fights entropy.
- (d) Per type/tool choice with a site default; no global answer.

**What users expect:** interactive users (`hpc run xterm`) expect their
environment to follow them. Batch flow owners expect reproducibility. These are
different users of the same command.

**Recommendation:** (b) for batch, with the CLI interactive default mapping to
a `[types.interactive]`-style profile that sets `inherit_env = true`. That
keeps `hpc run xterm` working while making the reproducible path the default
for scripts. Needs D7 (what the CLI default profile is).

**Decision (2026-10-28, revised after discussion):** keep `-V`. The
earlier allowlist, "inherit minus toolchain" and `/etc/profile` reset
variants were all rejected: guessing which variables to reset breaks correct
setups in ways harder to debug than the problem they solve.

**The reproducibility contract:** tool versions come from modules.
`module purge` then `module load` guarantees every tool the config names is
the version the config names, because the module system owns those `PATH`
and library entries and unwinds them properly. Everything else in the
inherited environment is session state or a site putting tools on `PATH`
outside the module system; the latter is a site hygiene problem and the
answer is "put it in a module", not a reset list in hpc-runner. The docs
state this contract and what it does not cover.

- `inherit_env = true` (`-V`) stays the default. `module purge` stays on by
  default; it is the mechanism, not an optimisation.
- `inherit_env = false` remains available per layer as the strict mode,
  with `env_passthrough = [...]` naming submit-host variables to pass
  explicitly. Together with a wrapper script that sets up what the job
  needs (D6 deferred `pre_commands`) this is the "set up your own
  environment" escape hatch. Opt-in, not a goal.
- `env_unset = [...]` (renamed from `unset_vars`, so all environment keys
  share the `env_` prefix: `env_vars`, `env_prepend`, `env_append`,
  `env_passthrough`, `env_unset`) stays as the small escape hatch for
  things like proxies. Moves to job layers per D14.
- Project-declared paths (`env_prepend.PATH = ["${CONFIG_DIR}/path-exports"]`
  with resolver-provided `${CONFIG_DIR}`, D5) remain the recommended way to
  express a project `sourceme`'s `PATH` additions in config.
- Testbed environment test: a job sees its configured module versions
  regardless of which modules the submitting shell had loaded. That is the
  contract and nothing more.

---

## D4. Is the venv captured automatically?

**Today:** `VIRTUAL_ENV` on the submit host is captured and `source
$venv/bin/activate` is rendered, unless config/kwarg sets `venv`. The only
opt-out is the undocumented `venv = ""`.

**Options:**

- (a) Keep auto-capture, add an explicit `venv = false` / `--no-venv`.
- (b) Opt-in only: `venv = "$VIRTUAL_ENV"` in config when wanted.
- (c) Auto-capture only for tools that are Python (`[tools.python]`,
  `[tools.pytest]`), never otherwise.

**What users expect:** someone running `hpc run python train.py` from a venv
expects that venv. Someone running `hpc run -t xcelium make sim` from a shell
that happens to have a venv active does not expect it, and activating it can
shadow a module python. Auto-capture also interacts with D3: with `-V` the
venv is on `PATH` anyway.

**Recommendation:** (a) now because it is small, with (c) worth considering
when D3 is settled. Either way the value must be explicit in `--dry-run`.

**Decision (2026-10-28):** (a), made explicit because under D3 the venv is
the only route by which Python tools reach the job, so it is load-bearing.
(c) is dropped: a `make` flow calling venv tools needs it as much as
`python` does.

`venv` is a job-layer scalar with three states:

- **unset**: capture `VIRTUAL_ENV` from the submit host at resolve time
  (default, the common case).
- **path**: use exactly that venv; `$VAR` and `${CONFIG_DIR}` expand per
  D5, so a project can pin `venv = "${CONFIG_DIR}/.venv"`.
- **`false`**: no venv regardless of the submitting shell. Python:
  `venv=None` (unset) vs `venv=False` (none).

Later layers win, so `[types.lint] venv = false` beats capture. Provenance
reports "captured from VIRTUAL_ENV" / "tools.python" / "types.lint
(disabled)"; `--dry-run` prints either the activate line or an explicit
"venv: none". The resolver checks `<venv>/bin/activate` exists on the
submit host (catches a stale variable). Shared filesystem is assumed:
HPC clusters use NFS or equivalent and nothing else works without it, so
a venv visible on the submit host is taken to be visible on the node.

---

## D5. Which environment variable semantics do we offer, and are they ordered?

**Today:** `env_vars` (set), `env_prepend`, `env_append` (colon-join), rendered
in the order prepend, append, set, after modules and venv, before the command.
Values are expanded on the submit host. Dict keys merge across layers.

**Questions:**

- Is submit-host expansion right? `env_vars = { OUT = "$SCRATCH/out" }`
  expands `$SCRATCH` on the login node, not the compute node. Both are
  plausible; the module list is deliberately submit-host. Option: expand
  `${VAR}` on the submit host and leave `$VAR` for the job, or a `literal`
  prefix. Needs a rule that fits in one sentence.
- Is the order fixed? A `PATH` prepend must come *after* module loads to win.
  It does today. Should `env_vars` be able to reference a module-set variable
  (`XCELIUM_HOME`)? Only if expanded in-job.
- Is the separator always `:`? `PYTHONPATH` yes, `MAKEFLAGS` no.

**Recommendation:** expand `${VAR}` on the submit host, leave `$VAR` to the
job shell, document in one line. Keep three verbs, fixed order, `:`
separator only, and say so. Anything fancier goes through D6 hooks.

**Decision (2026-10-28):** the braced/bare split is rejected as a footgun
(people will not read the spec). Remove the choice instead:

1. **Every `$VAR` / `${VAR}` in config is resolved on the submit host at
   resolve time.** Unresolved is an error, as it already is for module
   lists. No way to leak a literal `$` by accident. Same rule for every
   expanded string: `env_*`, `modules`, `modules_path`, `venv`, `stdout`,
   `workdir`.
2. **Scheduler runtime values use a portable namespace rendered by the
   scheduler:** `${hpc.cpus}`, `${hpc.job_id}`, `${hpc.task_id}`,
   `${hpc.tmpdir}`, `${hpc.job_name}`, `${hpc.host}`. SGE emits `$NSLOTS`,
   Slurm `$SLURM_CPUS_PER_TASK`, etc. This replaces `expand_makeflags`
   (`MAKEFLAGS = "-j${hpc.cpus}"` is the general form) and is the same
   mechanism D9 uses for output names. An unresolved `$NSLOTS` errors with
   the hint "did you mean `${hpc.cpus}`?".
3. **Values that depend on a module** (`XCELIUM_HOME=$CDS_INST_DIR` after
   load) are shell logic and belong in the recipe or script (D6).
4. Resolver-provided submit-side variables: `${CONFIG_DIR}` (directory of
   the config file that set the value), `${GIT_ROOT}`, `${CWD}`, `${USER}`.
5. `$$` is a literal `$` (as in Make and docker compose).
6. Order fixed: prepend, append, set; after modules and venv, before the
   command. Separator is `:`. `env_vars` and `env_prepend`/`env_append` on
   the same key in the same layer is an error.

One sentence for the docs: *every `$VAR` in config is resolved where you
submit from; runtime values come from `${hpc.*}`; anything else is
shell in your script or Makefile.*

---

## D6. How much arbitrary shell may config inject into a job?

**Today:** none, except `module_init_script` per scheduler. Users who need
`ulimit -s unlimited`, `source /tools/setup.sh`, `cd $TMPDIR`, or a post-job
`cp results /archive` cannot express it.

**Options:**

- (a) `pre_commands` / `post_commands` lists of shell lines on defaults, tool,
  type, and `Job`. Rendered verbatim. Post runs in the EXIT trap so it sees
  failures and `$_exit_code`. Appends across layers per D1.
- (b) `setup_script = "path"` sourced before the command, nothing after.
  Keeps shell out of TOML.
- (c) Site-overridable Jinja template per scheduler. Maximum power, zero
  guardrails, hard to support.
- (d) None. Users wrap their command in a script. This is a legitimate
  answer: the tool's job is scheduler and modules, not job orchestration.

**What users expect:** (a) is what every comparable tool (Snakemake profiles,
Nextflow `beforeScript`) ends up offering, and users will ask for it. (d) is
defensible but then the docs must say "wrap it" prominently.

**Recommendation:** (a) with both pre and post, plus (c) kept as an escape
hatch under `[schedulers.x] template =` for sites, explicitly unsupported.
Note that (a) makes config files executable code from a cloned repo; that is
already true of `modules` (a modulefile runs Tcl) so it is not a new trust
boundary, but write it down.

**Decision (2026-10-28):** (d), deferred. Not built now. For every case
listed, a Makefile recipe, a wrapper script, or a module is at least as
good and usually better (testable, diffable, dependency-tracked, and
already maintained by the flow owner). Flow logic in TOML is flow logic in
a worse place. The story stays: *modules for tools, `env_*` for variables,
Make or a script for logic.*

- The only unique capability is cross-cutting setup applied by config with
  no flow edited (site-wide `umask`, `ulimit`). No concrete case is known
  that cannot be a module or `/etc/profile.d`.
- **Revisit if** a requirement appears that cannot be a module, cannot be
  `env_*`, and would otherwise need every flow's Makefile edited. The
  design above (pre after env block, post in the EXIT trap with `set +e`,
  D1 lists, verbatim, provenance) is ready if so.
- The template override is dropped, not deferred; `module_init_script`
  covers the one real site variation.
- Consequences: D3's escape hatch is "`inherit_env = false` and run a
  script that sets up what you need"; D5's module-dependent value is the
  recipe's job.

---

## D7. What is the CLI's default profile, and does the API share it?

**Today:** CLI defaults to interactive (`qrsh`); API `submit()` defaults to
batch. `[types.interactive]` exists in the default config but is not what the
CLI applies by default; the CLI simply calls `qrsh` with the merged config.

**Questions:** should `hpc run` without `--batch` implicitly apply
`[types.interactive]` (or a configurable `defaults.interactive_type`)? Should
the API's `interactive=True` do the same? Is it acceptable that the same
`Job` submits differently depending on the entry point?

**Recommendation:** add `[defaults] interactive_type = "interactive"` applied
by *both* the CLI and `Job.submit(interactive=True)` when set. Then D3's
"interactive follows my environment" lives in config, not in code.

**Decision (2026-10-28):** the recommendation is rejected; no
`interactive_type`, no automatic profile. **Interactive means attached and
waiting, nothing more.** It does not change resolution. GUI tools get what
they need from their tool entry (`[tools.xterm] queue = "interactive.q"`),
and with D3 keeping `-V`, `DISPLAY` arrives anyway. Much of the existing
mess dates from batch having been the original default; it is cleaned up:

1. `interactive` becomes an attribute on `JobSpec`. CLI sets it true unless
   `--batch`/`--array`; API default false; config layers may set it (a GUI
   type can declare `interactive = true`); spec beats config.
2. `scheduler.submit(job)` reads `job.interactive`; the `interactive=`
   parameter on `submit` is deleted.
3. `QRSH_COMPATIBLE` is replaced by a per-renderer "valid for interactive"
   property, declared next to the flag it belongs to.
4. `--dry-run` prints the mode.
5. The `"interactive"` sentinel job id is deleted; under D12 a `JobResult`
   handle represents a completed interactive run with an exit code and no
   scheduler id.
6. `[types.interactive]` is removed from the shipped `defaults/config.toml`
   (it implied a special meaning it does not have); `[tools.xterm]` keeps
   its queue line.

---

## D8. What does `afterok` mean on a scheduler that cannot express it?

**Today:** SGE `-hold_jid` is afterany; hpc-runner emulates afterok with a
sentinel file per job and a guard at the top of the dependent's script,
toggled by `enforce_afterok`. Programmatic `.after()` gets the guard; the CLI
`--depend afterok:ID` does not. `.after()` applies one type to all parents.
`after` (start-based) and `afternotok` are in the enum but unimplemented.

**Questions:**

- Is emulation the right call, or should SGE honestly report "afterok not
  supported, treating as afterany" and leave it to the user? Emulation adds a
  shared-filesystem requirement and a 15 s NFS wait loop to every dependent.
- If emulating: when the guard aborts, what should the dependent's own status
  be? Today it exits 1, so it looks like *it* failed. Slurm leaves the
  dependent pending forever in `DependencyNeverSatisfied`. Options: exit with
  a distinct code (e.g. 75), write a sentinel saying "skipped", cancel self.
- Do we want per-parent types (`after(a, type=ok).after(b, type=any)`)?
- Should the CLI string form accept multiple (`afterok:1,2;afterany:3`) or
  only `--depend ID` plus `--depend-type`?

**Recommendation:** keep emulation (it is the behaviour users of the Python
API rely on and the sentinel doubles as the accounting fallback), make the CLI
string go through the same path, give the guard a distinct exit code and a
`JobStatus.SKIPPED`-style mapping, support per-parent types by accumulating
pairs. `after`/`afternotok`: implement on Slurm, raise `NotSupported` on SGE.

**Decision (2026-10-28):** keep the sentinel emulation; make it the single
dependency path; report skipped dependents distinctly.

1. **One path.** The CLI string is parsed into the same `(parent, type)`
   pairs the API builds; renderers never see a raw string. Grammar follows
   Slurm's: `afterok:123:124,afterany:125`. `--depend` is repeatable.
2. **Per-parent types.** `JobSpec` carries a list of `(job_id, type)`;
   `after()` accumulates. The guard is rendered per parent with that
   parent's type.
3. **`afternotok` is emulated** by the same guard with the test inverted.
   **`after` raises `NotSupportedError` on SGE** at submit; stays in the
   enum for Slurm. No dependency kind ever silently degrades.
4. **Array sentinels are per task:** `${JOB_ID}${SGE_TASK_ID:+.$SGE_TASK_ID}.rc`.
   A dependent on an array result is rendered with the explicit list of
   task sentinels and requires all of them. Elementwise array-to-array
   (`-hold_jid_ad`) deferred.
5. **Emulation stays on by default** as `[schedulers.sge] emulate_afterok`
   (an SGE fact, so it stays in the scheduler section). The bounded NFS
   wait stays.
6. **A guard-aborted dependent reports `JobStatus.SKIPPED`** ("did not
   run because a dependency was not satisfied"). The guard exits with a
   reserved code, `HPC_EXIT_DEP_FAILED = 113`, which `get_status` maps to
   SKIPPED on SGE (from `qacct` or the sentinel). On Slurm the same status
   maps from `PENDING` with reason `DependencyNeverSatisfied`. The cascade
   is unchanged (113 is non-zero, so downstream guards still abort).
   `returncode` returns 113 and documents it; `status` is the API for
   "did my command run". `is_terminal` includes SKIPPED.

   Why 113: exit codes are 0–255; 1–2 are generic, 64–78 are `sysexits.h`,
   126–127 are not-executable/not-found, 128+N are signals, 255 is out of
   range, and **SGE re-queues a job whose script exits 100** (unless
   `FORBID_APPERROR`) and treats 99 as reschedule for prolog/epilog. The
   free band is ~79–125 minus 99–100. Record the reasoning next to the
   constant.

---

## D9. Where do a job's output files go?

**Today:** nothing is set unless the user gives `stdout`/`stderr`; SGE then
writes `<name>.o<id>` in cwd. hpc-runner cannot tell the user where that is.
`%N`/`%J` patterns in the docs are not translated.

**Options:**

- (a) Always set `-o` explicitly, to a hpc-runner-chosen default
  (`<cwd>/<name>.<id>.out`), so the path is known at submit and identical
  across schedulers. Record it on `JobResult`.
- (b) Leave scheduler defaults; implement `get_output_path` per scheduler by
  reconstructing the default. Fragile across sites that set `-o` in `sge_request`.
- (c) (a) plus a portable pattern language (`{name}`, `{id}`, `{task}`) that
  each scheduler translates to its own (`$JOB_ID` / `%j`).

**Recommendation:** (c). The portable pattern is small and it removes the last
scheduler-specific thing a user has to know for the common case.

**Decision (2026-10-28):** (c), on the D5 `${hpc.*}` namespace.

1. hpc-runner always sets the output path explicitly; never the scheduler
   default. Package default `stdout = "${hpc.job_name}.${hpc.job_id}.out"`
   (hpc-runner's own form, identical on every scheduler; a site wanting
   native `*.o*` names sets the pattern). Renderers translate: SGE
   `$JOB_NAME.$JOB_ID.out` (dollars preserved for SGE to expand), Slurm
   `%x.%j.out`, PBS substitutes at submit and `qalter -o` with the real
   id after.
2. Arrays: if the pattern lacks `${hpc.task_id}`, the renderer appends
   `.${hpc.task_id}` so tasks never collide.
3. `JobResult.stdout_path` is recorded at submit time by substituting the
   known values in Python; `ArrayJobResult.task_stdout_path(n)` per task.
   No reconstruction from scheduler defaults. This is what makes
   `read_stdout()` work on SGE.
4. Relative paths are relative to the job's working directory; a directory
   component is created on the submit host before submission (SGE silently
   discards output otherwise).
5. stderr stays merged by default; a `stderr` pattern turns merging off.
   The unread `[schedulers.sge] merge_output` key is deleted.
6. Interactive jobs: `stdout_path` is `None`; `read_stdout()` raises or
   returns `None`, not `""`.

---

## D10. Are `mem` and `time` strings or values?

**Today:** strings passed through verbatim. `16G` and `4:00:00` happen to work
for both SGE and Slurm.

**Question:** does hpc-runner own a format (`Memory`, `Duration` parsed once,
rendered per scheduler, so `1d`, `90m`, `16GiB` all work and PBS gets `16gb`),
or does it document "use your scheduler's syntax" and pass through?

**Recommendation:** own it. The cost is two small parsers; the benefit is that
config files become portable across clusters, which is the headline promise.
Pass-through remains available via `raw_args`.

**Decision (2026-10-28):** own them. Two value types parsed once in
`resolve`, rendered per scheduler.

1. **`Memory`**: accepts `16G`, `16GB`, `16GiB`, `4096M`, `1.5G`, bare
   integer = bytes. Stored as bytes. SGE renders `16G`, Slurm integer MB,
   PBS `16gb`. Fractions rounded at render.
2. **`Duration`**: accepts `HH:MM:SS`, `MM:SS`, `D-HH:MM:SS`, `90s`, `30m`,
   `4h`, `2d`, `1h30m`. Stored as seconds. **A bare integer is an error**
   ("ambiguous: use 90s or 90m") because SGE reads it as seconds and Slurm
   as minutes. SGE renders `H:MM:SS`, Slurm `D-HH:MM:SS`, PBS `HH:MM:SS`.
3. D5 expansion runs first, then parse. `JobSpec` accepts the string or
   the value type.
4. Raw passthrough (`--`, `scheduler_args`) is untouched for odd site
   resources.
5. **`mem` means per job**, regardless of `cpu`. That is how the user
   thinks ("this simulation needs 16G") and the only definition that
   survives changing `cpu`; anything more complex is using other submit
   tooling. The site says how to express it: `[schedulers.sge]
   memory_resource = "mem_free"`, `memory_per = "slot" | "job"`, and the
   renderer divides by `cpu` when the site convention is per slot. Slurm
   renders `--mem`; `--mem-per-cpu` only if a site config asks.

---

## D11. Is unknown config an error, a warning, or ignored?

**Today:** ignored silently.

**Recommendation:** error for unknown keys under `[defaults]`, `[tools.*]`,
`[types.*]`; unknown keys under `[schedulers.<name>]` are allowed only if the
scheduler declares them. Report file and line. Provide `hpc config check`.
The only argument for "warn" is forward compatibility across versions, which
a `config_version` key handles better.

**Decision (2026-10-28):** error. Demonstrated 2026-10-28: `cpu = "4"`,
`modules = "gcc/12"` (iterated as a string into six `module load <char>`
lines), `mem = 16` (16 bytes) and a `module`/`modules` typo that silently
dropped a type's whole module list all loaded without a word.

1. Typed schema per layer, derived from the `JobSpec` dataclass (D12), so
   field names, types and value parsing (D10, D1) are defined once and
   cannot drift.
2. Unknown key or wrong type is an error naming file, line, section and
   key, with "did you mean"; all problems in a file reported together.
3. `[schedulers.<name>]` validated by that backend's declared keys. A
   section for a scheduler the package lacks is a warning, not an error,
   so site configs can lead support.
4. Structural errors: type referencing a missing tool, `extends` pointing
   at a missing file (currently tolerated, which hides typos), `extends`
   cycles.
5. `config_version = 1` required at the top of every file; unknown version
   is an error. `hpc config init` writes it; the missing-key error gives
   the one-line fix.
6. `hpc config check [path]` prints every problem, exit 1 on any;
   `hpc config resolve <cmd> [-t type]` prints the merge with provenance.
   Validation runs on every load, so `hpc run` refuses a broken config
   before touching the scheduler.

---

## D12. Is the resolved job a value or a service?

**Today:** `Job.__init__` resolves against the global config cache; `Job` is
both the user's request and the resolved result, and it is mutable.

**Options:**

- (a) Keep one class; make `config` an explicit kwarg defaulting to
  `get_config()`.
- (b) Split: `JobSpec` (request) → `resolve(spec, config)` → `Job` (resolved,
  frozen). `Pipeline.add` and the CLI build specs.
- (c) (b) plus a `JobResult.from_id(scheduler, id)` so results are not tied to
  an in-memory `Job`.

**Recommendation:** (b)+(c). This is the structural change that most of the
other decisions land on top of, so decide it before implementing D1, D2, D6.

**Decision (2026-10-28):** (b)+(c), as a clean sweep with no backwards
compatibility. The package has one user and is in development, so:

- `JobSpec` is a plain dataclass holding exactly what the caller wrote;
  `None` means "not specified". No config access, no env access.
- `resolve(spec, config, host) -> Job` is the only place merge rules live.
  `config` is always an explicit argument; `host` carries submit-side facts
  (`USER`, `VIRTUAL_ENV`, env lookup, cwd) so tests inject them. It records
  provenance (which layer supplied each value) for `hpc config resolve`.
- `Job` is frozen. Schedulers render it and never mutate it. `Pipeline` and
  the CLI build specs and call `resolve`; they do not poke resolved objects.
- `JobResult` becomes a handle: `scheduler.result(job_id)` reconstructs one
  without an in-memory `Job`.
- No `Job(command, cpu=4)` convenience constructor is kept. README and docs
  are rewritten to the new API in the same change. The global config cache
  and the `_isolate_config_cache` test fixture are deleted.

---

## D13. What does the API raise, and what does the CLI print?

**Today:** `RuntimeError`, `ValueError`, raw `CalledProcessError`, plus a
defined-but-unused hierarchy. The CLI catches only `ConfigError`.

**Recommendation:** every path that shells out raises `SchedulerError` or a
subclass with the argv and stderr attached; `SubmissionError` carries the
scheduler's own message verbatim; `JobNotFoundError` for status of an unknown
id. The CLI catches `HPCToolsError`, prints one line, exits 1; `--verbose`
prints the argv and stderr. Nothing else escapes to a traceback.

**Decision (2026-10-28):** as recommended, with one subprocess seam.
Observed 2026-10-28: a `qsub` queue rejection shows a 12-line
`CalledProcessError` traceback whose message is the deleted script path,
while the scheduler's own reason sits unprinted on `e.stderr`.

1. One helper `scheduler._run(argv, *, timeout)` is the only place
   `subprocess` is touched. It logs argv at DEBUG, applies a timeout, and
   converts: missing binary → `SchedulerUnavailableError`; non-zero exit →
   `SchedulerCommandError(argv, returncode, stdout, stderr)`; timeout →
   `SchedulerTimeoutError`. Nothing from `subprocess` escapes the
   scheduler layer. This is also the single seam the testbed shims and
   unit tests use, replacing the global `patch("subprocess.run")`.
2. `SubmissionError` carries the scheduler's stderr as its message and the
   rendered script; unparseable `qsub` output is also `SubmissionError`.
3. Hierarchy used as named: `JobNotFoundError` everywhere an id is
   unknown; `NotSupportedError` (D8); `ConfigError` with file/line (D11),
   including unknown scheduler names; `PipelineError` subclasses from the
   API review. `ValidationError` deleted.
4. `JobResult.result()` raises `JobFailedError(status, job_id, exit_code)`
   (`concurrent.futures` style).
5. CLI catches `HPCToolsError` at the top: one line to stderr, exit 1;
   `--verbose` adds argv and full stderr. A traceback means a bug.
6. `--verbose` sets the `hpc_runner` logger to DEBUG so every scheduler
   command is visible.
7. Keep the hierarchy to ~8 classes, each justified by a caller that
   would `except` it differently; nothing that would only ever be caught
   as `HPCToolsError` gets its own class.

---

## D14. How are scheduler-specific settings expressed in config?

**Today:** `[schedulers.sge]` holds site names (PE, resource names) and
behaviour (`purge_modules`, `expand_makeflags`) mixed together. There is no
per-tool scheduler-specific block; raw flags are CLI-only.

**Question:** should a tool or type be able to say
`[tools.xcelium.sge] args = ["-l", "xcelium_lic=1"]` and
`[tools.xcelium.slurm] args = ["--licenses=xcelium:1"]`? That is the realistic
way a site keeps one config for two clusters.

**Recommendation:** yes, as `scheduler_args.sge = [...]` on any job layer,
appended per D1, replacing the `sge_args`/`slurm_args` kwargs with one dict.
Separately, move behaviour flags (`purge_modules`, `silent_modules`,
`expand_makeflags`, `unset_vars`) out of `[schedulers.*]` into `[defaults]`,
since they describe the job script, not the scheduler.

**Decision (2026-10-28):** scoped down. Anything with a clean equivalent on
every scheduler is modelled, not passed raw; `resources` already covers
consumables and licences, and the resolver renders them per scheduler.

1. Add `project` as a first-class job attribute (`-P` / `--account` / `-A`).
   Mail and exclusive-node are deferred until a real need.
2. Per-run raw passthrough stays: `--` on the CLI and
   `JobSpec(scheduler_args={"sge": [...]})` in the API. This is the dev/test
   pattern.
3. `scheduler_args.<sched> = [...]` is allowed on any config layer, appended
   per D1, only the active scheduler's list kept at resolve time. It is an
   escape hatch for flags with no abstraction (`-binding`, `-masterq`,
   `--constraint`, `--signal`, `-pty`), documented as such: not every
   scheduler flag maps cleanly and it is wrong to try. The `sge_args` /
   `slurm_args` / `pbs_args` kwargs are replaced by the one dict.
4. `purge_modules`, `silent_modules`, `expand_makeflags`, `unset_vars` move
   out of `[schedulers.*]` into `[defaults]`, overridable per tool/type.
   `[schedulers.<name>]` keeps only site facts: PE name, resource names,
   module init script, template override. How those four script-behaviour
   flags should be used is a separate discussion: see D15.

---

## D15. How should the script-behaviour flags be used?

Follows from D14(4). `purge_modules`, `silent_modules`, `expand_makeflags`,
`unset_vars` now live on job layers. Open questions: should `purge_modules`
ever be false given the clean-environment goal (or is it just an escape for
sites whose module init is broken)? Is `expand_makeflags` a general "expand
`$NSLOTS`-style scheduler variables in named env vars" feature in disguise?
`env_unset` (renamed from `unset_vars`) is settled by D3 as a kept escape hatch. Should `silent_modules` be a
`--verbose` concern rather than config at all?

**Decision (2026-10-28):** three of the four keys are deleted.

- **`purge_modules`: deleted; `module purge` is unconditional.** It is
  the mechanism of the D3 contract, not an optimisation. A site that
  cannot purge has `inherit_env = false` plus its own script.
- **`silent_modules`: deleted.** The problem it solved is real: Modules
  prints "Loading foo" to stderr, which pollutes merged job output. But
  Modules' `--silent` also suppresses *error* messages, so a failed load
  under `set -e` kills the job with nothing on stderr. Replacement:
  quiet-on-success, loud-on-failure, with no key. Each module command's
  stderr is redirected to a file and printed only if the command fails:
  `module load X 2>"$_hpc_modlog" || { cat "$_hpc_modlog" >&2; echo
  "hpc-runner: module load X failed" >&2; exit 1; }`. Works on every
  module system/version. When the site declares `[schedulers.x]
  module_system = "modules"`, also export `MODULES_VERBOSITY=concise`
  (Modules ≥4.3). `hpc run --verbose` lifts the redirect.
- **`expand_makeflags`: deleted.** D5's `${hpc.cpus}` is the general
  mechanism; `[tools.make] env_vars = { MAKEFLAGS = "-j${hpc.cpus}" }` is
  the specific case and works on every scheduler without `envsubst`.
- **`env_unset`: kept** (D3), rendered after module loads and venv and
  before `env_vars`, so it can remove something a module set as well as
  something inherited.

---

## D16. Pipeline lifecycle after submission

From the independent API review (QA_REVIEW.md §7). `Pipeline` today is a
name registry plus a loop over `submit()`; it has no verbs for the
post-submit half of a workflow's life.

**Today:** no cancel-as-a-unit (and order matters: on SGE cancelling a
parent *releases* its afterany dependents); retry only of steps that failed
to *submit*, not ones that ran and failed; nothing persisted, so a pipeline
cannot be resumed from another process; `wait()` spins forever when a job
goes `UNKNOWN`; every poll is several subprocesses per job; `__exit__`
submits unconditionally so calling `submit()` inside the `with` raises from
`__exit__`; arrays cannot be steps; parents outside the pipeline cannot be
edges.

**Proposed shape (reviewer's):**

- `JobStatus.is_terminal`; `JobResult.wait(timeout=, poll_interval=,
  unknown_after=)` returns `UNKNOWN` instead of spinning;
  `BaseScheduler.get_statuses(ids)` batched, one `qstat` per tick;
  `JobResult.result()` raising `JobFailed`.
- `Pipeline.add(spec | ArraySpec | str, name=, after=[...])` with per-edge
  `Edge(parent, kind)`, parents being step names, `Step`s, or external
  `JobResult`s.
- `Pipeline.submit(dry_run=) -> PipelineRun` with `status()`, `wait()`,
  `failed()`, `cancel()` (reverse topological order), `retry(steps=)`
  (failed steps plus descendants, edges re-wired), `save(path)` /
  `load(path)` JSON manifest.
- Context-manager auto-submit removed outright.

**Questions to settle:** is `save`/`load` in scope now or deferred until a
multi-day pipeline exists? Does `retry` belong in the library or is it the
caller's loop? Is `return_when="FIRST_FAILURE"` wanted? Raise the Python
floor to 3.11 (3.10 EOL October 2026) for `StrEnum` and friends?

**Decision (2026-10-28): PARKED.** Likely needs wholesale changes rather
than the incremental shape above; needs a better understanding of real use
cases and how a user wants to define a job list before any API is chosen.
To be taken up in a dedicated session. Discussion notes so far are in
QA_REVIEW.md §7.f. Two items in it are bug fixes independent of the design
and can proceed: `wait()` spinning on `UNKNOWN`, and batched
`get_statuses` on the scheduler.

---

## Suggested order

D12 first (everything else is implemented on top of it). Then D1, D2, D14
together (they are one merge model). Then D3, D4, D7 together (one environment
story). D5, D6 after that. D8, D9, D10, D11, D13 are independent and can be
taken as they come.
