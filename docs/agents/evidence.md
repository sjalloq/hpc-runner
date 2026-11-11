# Evidence

Every statement about this code, a scheduler, a library or a tool carries its
**provenance**: where it was read or run in the session that wrote it. The
code uses the same word for the layer a resolved value came from, and a claim
with no provenance is as suspect as a config value nobody can trace.

## What counts

| Claim about | Evidence |
|---|---|
| this code: a name, a path, a signature, a behaviour | the file and line read this session, or the command run and its output |
| a scheduler or a library | its source at a named tag, its man page, or a capture under `tests/unit/fixtures/` |
| a command: an acceptance step, a build, a test | the command run this session and what it printed |
| a decision | its number in a `DECISIONS.md` |

Recall of the architecture, of a man page, of how a flag usually works or of
what a test file probably contains is the hypothesis that sends you to the
file. It becomes a fact when the file agrees.

## Procedure

Before a plan, a decision, a ticket, a brief or a review leaves the session:

1. **List the claims.** Every identifier (file, function, option, key, test),
   every "X does Y", every command.
2. **Chase each one.** `grep` the identifier, read the function, run the
   command. A plan that changes existing code gets a pass over that code
   (what the base class reads, what the CLI does today, what the schema
   rejects) as careful as the pass over the external system.
3. **Write the evidence beside the claim** where a reader would want it:
   `base.py:85-94`; "run 2026-11-04, Compose 5.6.1"; "src: slurm_opt.c at
   slurm-24-11-7-1".
4. **Mark what you could not chase** as `unverified`, or drop it. Work
   handed to a sub-agent carries no `unverified` claim: every identifier it
   names was found and every acceptance command it lists was run.
5. **A gap is a question.** When chasing shows the code cannot do what a
   decision assumes (a missing parameter, an output nobody captures), raise it
   to the user as a decision with the evidence and the options. Filling the
   gap with a plausible mechanism is the failure this page exists to end.

## Why this page exists

The 2026-11-04 Slurm ticket pass wrote 22 tickets from the decision log and
the architecture summary. An independent review changed 21 of them. The
tickets had invented a config key that was a helper function, a short flag,
unit tests that did not exist, an API shape, a seam parameter, a Compose
project name Compose rejects, a per-item `-L` that Slurm overwrites on repeat,
and acceptance commands that could never pass. The decisions themselves were
silent wherever a Slurm choice met existing code (the output pattern, the
shared module keys, the interactive exit-code path), because the planning
research covered Slurm and left hpc-runner's own code to recall. The tickets
were deleted in the reset of 2026-11-11; what the review found is in
`docs/dev/slurm/DECISIONS.md` ("Open questions", "Implementation notes").
