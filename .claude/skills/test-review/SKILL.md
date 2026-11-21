---
name: test-review
description: Review tests by breaking the code and seeing whether they fail. Takes test files, a git ref to diff against, or a spec document.
disable-model-invocation: true
---

# Test review

A test is worth having only if it goes red when the behaviour it covers is
broken. This review proves that for each test by planting **mutants**: small,
plausible, wrong changes to the production code. A mutant the suite catches
is **killed**; one the suite passes with **survives**, and a survivor is a
finding.

The review reports. It changes no test and no production code, and decides
nothing: every fix is the user's call.

## Input

One of: test file paths; a git ref (review the tests added or changed since
it); a spec document (review whatever tests cover it). With no argument, ask
which.

## Steps

1. **Baseline.** Run `git status --short` and `git diff --stat` and keep the
   output. Run the tests in scope once; they must pass. Done when both are
   recorded.

2. **Promises.** Find what the code under test is meant to do, from the spec
   and not from the tests: `docs/dev/SCHEDULER_OPTIONS.md` (the rules for
   options), the plan in `docs/dev/` for the work, `docs/source/` for user
   behaviour. Write each promise as one sentence with its source, concrete
   enough to break: "`cpu=3` on an SGE batch job puts `#$ -pe smp 3` in the
   script". Done when every rule, table cell and bullet in scope is a
   promise or is listed as out of scope with the reason.

3. **Tests.** For every test function in scope write one line: the bug it
   exists to catch, and its **oracle** (where its expected answer comes
   from), one of:
   - *literal*: the expected value is written in the test;
   - *derived*: computed by the code under test, or read from a declaration
     in that code;
   - *change-only*: asserts that output differs, is non-empty, or that
     nothing raised;
   - *none*: no assertion on behaviour.
   Done when every test function has its line.

4. **Mutants.** For every promise, plant one mutant in the production code:
   the wrong flag, the option dropped, the wrong branch taken, a boundary
   moved by one. Prefer the mistake a tired developer would make. Then:
   - run only the test files in scope, one pytest process at a time:
     `PYTHONPYCACHEPREFIX=$(mktemp -d) pytest -q <files>` (a fresh bytecode
     cache each run; a same-second edit can otherwise run stale code);
   - record killed or survived, and which test killed it;
   - undo the mutant and confirm `git diff --stat` equals the baseline.
   Done when every promise has a mutant with a recorded result and the tree
   matches the baseline.

5. **Report**, in this order:
   - **Survivors**: promise, the mutant (file, line, the change), and why
     no test caught it.
   - **Promises with no test.**
   - **Weak oracles**: every *derived*, *change-only* and *none* test, with
     the literal expectation it should carry.
   - **Code that exists for the tests**: production names read only by
     tests.
   - One closing line: promises, mutants planted, killed, survived.

## What a weak test looks like

- It passes for any change, not the right one (`assert out != baseline`).
- Its expected answer is produced by the code it tests, so the two agree by
  construction.
- It asserts a mock was called, not what the user would see.
- It walks a table with one generic check, so no row states its own answer.
- Its name promises more than its assertions check.
- It reaches into private methods to get something to compare.

## Bounds

- Unit tests (`tests/unit/`) by default. Integration tests need the Docker
  testbed (`docs/source/development.rst`); plant mutants for them only when
  asked, and say so in the report otherwise.
- Mutants go in `src/` only. A survivor is reported, never fixed here.
