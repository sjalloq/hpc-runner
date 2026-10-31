# `hpc run` argument parsing: the `--` design and why it was replaced

Status: **closed by D-T43** (2026-10-31). Sections 1-6 are the record of the
`--` design as shipped up to commit 9d5c882 and the three faults found in it;
§7 is the decision and what replaced it. Every example was run with
`hpc --scheduler sge run --dry-run ...` against a config holding only
`config_version = 1` and a `[types.xcelium]` entry.

## 1. The model that was shipped

`hpc run` took three kinds of token in one argv:

1. **Its own options**: long-form only (`--cpu`, `--batch`, `--name` ...)
   plus the single claimed short flag `-t/--type`.
2. **Scheduler passthrough**: any other single-dash flag (`-q`, `-l`, `-V`,
   `-h` ...), forwarded verbatim to qsub/sbatch.
3. **The command**: the program to run and its arguments.

Rule as documented: without `--`, everything that is not one of our options
is the command; with `--`, everything before it is ours or passthrough and
everything after it is the command.

## 2. Why `--` was needed even though click ignores unknown options

`ignore_unknown_options=True` stops click from raising on `-q` and hands it
back as a plain token. It does not say where the scheduler flags end and the
command begins:

    hpc run -q batch.q python train.py

Only qsub's option grammar knows whether `batch.q` is `-q`'s value; sbatch's
grammar differs. `--` let the user state the boundary.

## 3. How click walks the tokens

click's `_OptionParser._process_args_for_options` (click 8.x), simplified:

```python
while state.rargs:
    arg = state.rargs.pop(0)
    if arg == "--":
        return                      # (a) separator consumed and DROPPED
    elif arg[:1] in self._opt_prefixes and len(arg) > 1:
        self._process_opts(arg, state)   # (b) known: consume; unknown: append to largs
    elif self.allow_interspersed_args:
        state.largs.append(arg)
    else:
        state.rargs.insert(0, arg)
        return                      # (c) first plain word: STOP, leave the rest as-is
```

and `_match_short_opt` walks a single-dash token **one character at a time**
(GNU clustering: `-abc` is `-a -b -c`), consuming any character that is a
declared short option and recombining the rest.

## 4. Fault 1 (fixed in 9d5c882): a bare flag before `--` lost the separator

`hpc run -h -- sleep 30`: `-h` unknown → largs (b); `--` → branch (a),
dropped; we received `["-h", "sleep", "30"]` and rejected it. Fixed by
splitting on `--` in `_RunCommand.parse_args` before click saw argv.

## 5. Fault 2: our options after a valued scheduler flag were lost

`hpc run -q batch.q -t xcelium -- make sim`: `-q` → largs; `batch.q` is a
plain word so branch (c) **stops option parsing**; `-t xcelium` was never
examined and went to qsub as an array range. The type was silently dropped.
`hpc run -q batch.q --cpu 4 -- make sim` was rejected as a typo. The
undocumented rule was "our options must precede the first scheduler flag
that takes a value".

## 6. Fault 3: short-flag clustering matched `-t` inside qsub words

Found 2026-10-31 while reviewing the proposed fix for fault 2. Because
click clusters single-dash tokens, claiming `-t` matched every qsub word
containing a `t`:

| Typed | Result |
|---|---|
| `-terse -- sleep 1` | `unknown job type 'erse'` |
| `-tc 4 -- sleep 1` | `unknown job type 'c'` |
| `-pty y -- xterm` | `unknown job type 'y'` |
| `-soft -l a=1 -- sleep 1` | `unknown job type '-l'` (`-t` swallowed the next flag) |
| `-notify`, `-ckpt x`, `-masterq q` | all mis-parsed the same way |

This is independent of ordering, so the §5 fix (interspersed parsing before
`--`) would not have touched it. It is also not click's bug: click's
tokeniser models GNU long options plus clusterable single-letter flags, and
SGE/PBS/LSF use single-dash multi-letter words. The parser class is private
(`_OptionParser`, since 8.2); the maintainers' answer to the symptom is
"use `--`" (pallets/click discussion #2531).

Two routes were prototyped:

- **A. Own tokeniser in `parse_args`.** Match our options by exact spelling,
  pass everything else before `--` through verbatim. ~40 lines, passed every
  case above. Bespoke: no other scheduler front end interleaves two option
  grammars in one argv.
- **B. Raw flags as a quoted string.** `--scheduler-args '-q batch.q -l gpu=2'`,
  `shlex.split`, appended to `scheduler_args`. Zero custom parsing. The shape
  Snakemake (`slurm_extra`, `--cluster '...'`), dask-jobqueue
  (`job_extra_directives`) and submitit (`additional_parameters`) use.

## 7. Decision D-T43: `-S/--scheduler-args`, `--` dropped, short flags ours

User's call, 2026-10-31: take the well-trodden path (B). Nothing had been
released with `--`, so there was no compatibility to keep.

- `hpc run -S 'FLAGS'` / `--scheduler-args 'FLAGS'`, repeatable; each value
  is `shlex.split` and the pieces are concatenated in typed order onto
  `JobSpec.scheduler_args[<active scheduler>]`. Slurm long options
  (`-S '--gres=gpu:2'`) work without a special case, which closes the old
  open question 1.
- `hpc run` is now a plain click command: no `ignore_unknown_options`, no
  `_RunCommand`, no `_parse_args`, no typo guards. `allow_interspersed_args`
  stays off so the first plain word starts the command and its flags are
  never interpreted. A command that itself starts with a dash takes click's
  ordinary `--` first (`hpc run -- -h sleep 30`); that is the only `--` left.
- No scheduler word ever reaches click's tokeniser, so every short letter is
  free. D-T42's single-claimed-flag rule is void: `-h` is help again and the
  options have short spellings (`-t` type, `-b` batch, `-N` name, `-c` cpu,
  `-m` mem, `-T` time, `-q` queue, `-n` nodes, `-d` directory, `-o` stdout,
  `-e` stderr, `-a` array, `-E` env, `-R` resource, `-D` depend, `-w` wait,
  `-S` scheduler-args). `--ntasks`, `--dry-run` and `--keep-script` stay
  long-only.
- A flag `hpc run` does not define is a usage error with click's
  did-you-mean (`--cpus` → "Did you mean '--cpu'?"). Nothing is ever
  forwarded by accident.
- Tests: `tests/unit/test_cli/test_run.py::TestSchedulerArgs` covers the
  split, repetition, order independence, every clustering word from §6, the
  sbatch long options and the dashed-command case;
  `tests/integration/test_cli.py::test_hold_flag_forwards` now submits
  `-S '-h'`.
