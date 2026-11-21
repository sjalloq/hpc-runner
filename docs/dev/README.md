# Working notes: index

The one place that says where each piece of work stands. Read this first;
update it whenever a status or a next action changes. User documentation is
`docs/source/` (Sphinx); how the code is put together is
`docs/source/development.rst`; how to work here is `CLAUDE.md`.

## Next actions, in order

1. `slurm/PLAN.md`, "Order of work", from step 1 (Slurm testbed support,
   starting with the cgroups research). The list is all the remaining Slurm work. It has no
   open questions.

Anything missing, unverified or undecided that belongs to no plan is in
`FOLLOW_UPS.md`. Add to it whenever work is left unported or unrun.

## Work

| Work | Status | Read | What is left |
|---|---|---|---|
| Slurm backend | Research closed; plan consolidated 2026-11-17; `hpc run` exit code, option removals, the option table and `hpc cancel` built (SGE, local), no Slurm code yet | `slurm/PLAN.md` | Its "Order of work" (9 steps) |
| Restructure of the core (D1..D19) | Done, released in v0.14.0 to v0.16.0 | `restructure/DECISIONS.md` | D16 (pipeline lifecycle after submission) is parked. One question hangs off it: what "pipeline retry" means. |
| SGE testbed | Done for two flavours (`soge`, `ocs`), in CI | `testbed/TESTBED_REVIEW.md` (decisions D-T1.., bugs BUG-T1..); how-to in `testbed/README.md` | See `FOLLOW_UPS.md` ("Testbed stacks") |
| Test review | Done | `test-review/LIFECYCLE_MATRIX.md` (every SGE state against every reader) | Nothing |
| Scheduler trace | Done: recorder extended, `hpc trace` commands removed (2026-11-15); integration tests pass on `soge` (2026-11-16) | `trace/DESIGN.md` | None |
| `hpc run` argument parsing | Closed by D-T43 | `cli/RUN_ARG_PARSING.md` | Nothing |
| `hpc monitor` TUI | Built; a rework is intended and not planned | `tui/TEXTUAL_STYLING_COOKBOOK.md` | No plan exists |

## Every file here

| File | What it is |
|---|---|
| `FOLLOW_UPS.md` | Known gaps with no plan: tests and testbed setup that exist for one scheduler only, things not run, things not decided |
| `restructure/DECISIONS.md` | Decision log D1..D19: config layering, environment, units, errors, scheduler choice, statuses |
| `testbed/TESTBED_REVIEW.md` | Decision log D-T1..D-T50 and bug list BUG-T1.., for the testbed and the integration suite |
| `testbed/TESTBED_PLAN.md` | The plan the testbed was built from. Kept only because code comments cite its sections; where it and `TESTBED_REVIEW.md` differ, the review is right |
| `test-review/LIFECYCLE_MATRIX.md` | Reference: SGE job states against the code that reads them, with the test covering each cell |
| `trace/DESIGN.md` | What `hpc --trace` and `[trace]` record, and why |
| `cli/RUN_ARG_PARSING.md` | Record of the `--` passthrough design and why `-S/--scheduler-args` replaced it |
| `sge-xml-defects.md` | Reference: four defects in gridengine XML output and how the parser repairs each |
| `SCHEDULER_OPTIONS.md` | The spec for options: what hpc-runner does with each option on each scheduler (SGE, Slurm, local), its own rules with reasons, the flag for each option, and what the interactive commands do differently. The suite checks the code against it |
| `tui/TEXTUAL_STYLING_COOKBOOK.md` | Rules and checklist for styling `hpc monitor` |
| `tui/TUI_HORIZONTAL_SCROLLBAR.md` | How the job table avoids a horizontal scrollbar |
| `slurm/PLAN.md` | The Slurm plan: what is decided, all remaining work in order, open questions |
| `slurm/SGE_SLURM_DIFFERENCES.md` | Fact base: how Slurm differs from SGE, with sources |
| `slurm/RESOURCES_COMPARISON.md` | Fact base: 17 resource requests on SGE, Slurm, LSF and PBS, with sources; Appendix A is the earlier LSF and PBS research |

Decision numbers in the older files (code comments cite them): `D<n>`
restructure, `D-T<n>` testbed, `D-TR<n>` trace. The Slurm plan has none.

## Rules for this folder

- A decision is recorded only on the user's explicit answer.
- A plan, review or research note is deleted once it has been carried out
  or folded into a decision log; git keeps it.
- No tickets and no second copy of a decision: work is taken from the
  plan, one item at a time.
- Plans are bullet points in plain words. No numbered decision labels, no
  terms the file does not define.
- Chat transcripts and exports are not kept in the repo.

## Removed

Removed in the reset of 2026-11-11 because the work they planned or reviewed
was finished, or because their content moved into a file above.

Read one with `git show 26aec97:docs/dev/<path>`:

- `restructure/QA_REVIEW.md`, `restructure/SESSION_REVIEW.md`,
  `restructure/QUESTIONS.md`
- `testbed/TESTBED_DESIGN.md`, `testbed/TESTBED_RESEARCH.md`
- `test-review/TEST_QA_REVIEW.md`, `test-review/GAP_PLAN.md`
- `tui/HPC_MONITOR_TUI_PLAN.md`

Never committed, so kept only on the maintainer's local branch
`backup/tree-2026-11-11`: the 22 Slurm tickets with their review and spec,
the resources research brief, the open-threads and LSF/PBS notes as separate
files, the agent docs for the ticket tracker, and two chat exports.
