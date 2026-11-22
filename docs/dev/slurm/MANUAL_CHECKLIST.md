# First run on a real Slurm cluster: manual checklist

**Nothing captured from a real cluster is committed, scrubbed or not: no
command output, no job script, no trace, no host, user, partition, account
or path name.** Write down pass or fail and what was different, in words,
in the project note. A difference that matters becomes a testbed
reproduction or an item in `../FOLLOW_UPS.md`.

The Docker testbed cannot show the things below. Each item is a command and
what to look for. `<part>` is a partition you may use; take the smallest
and shortest one the site has.

Before starting:

- `hpc config check` passes on the site config, which names
  `scheduler = "slurm"`.
- Use `--keep-script` and `--verbose` freely: `hpc --verbose run
  --keep-script ...` prints each Slurm command and where the script is.

## 1. Site wrappers around `sbatch` and `srun`

Some sites replace `sbatch` or `srun` with a wrapper script.

- [ ] `type sbatch srun sacct squeue scontrol scancel`
  - Look for: a path outside Slurm's own `bin`, or a shell function or
    alias. Note which commands are wrapped.
- [ ] `hpc run --batch -q <part> echo hello`
  - Look for: `Submitted job <id>` with a numeric id. An error "could not
    parse job id from sbatch output" means the wrapper prints something
    other than `Submitted batch job <id>`.
- [ ] `hpc run --batch --wait -q <part> sh -c 'sleep 20; exit 3'`
  - Look for: `Submitted job <id>` printed at once, not after 20 s (the
    waiting `sbatch` writes to a pseudo-terminal; a wrapper may behave
    differently there); then `Job completed with status: FAILED`; then
    `echo $?` prints 3.
- [ ] `hpc run -q <part> sh -c 'echo hi; exit 3'`
  - Look for: `hi` on the terminal, `echo $?` prints 3.

## 2. Real modules

- [ ] `hpc run --batch --wait -t <a type with modules> sh -c 'module list 2>&1; which <the tool>'`
  - Look for: in the output file, exactly the configured module versions
    and the tool's path from that module, whatever is loaded in the
    submitting shell.
- [ ] The same with a module name that does not exist.
  - Look for: the job ends FAILED and its output has `hpc-runner: module
    load <name> failed` with the module system's own message.
- [ ] If the first item fails with "module: command not found": set the
  top-level `module_init_script` to the site's init file and run it again.

## 3. Real GPUs and licences

- [ ] `hpc run --batch --wait --device gpu=1 -q <gpu part> nvidia-smi -L`
  - Look for: one GPU listed; `scontrol show job <id>` (while Slurm
    remembers the job) shows the request under `TresPerNode` or `Gres`.
- [ ] `hpc run --batch --wait --device gpu=2 -q <gpu part> nvidia-smi -L`
  - Look for: two GPUs listed.
- [ ] A GPU of a named type: `hpc run --batch -S '--gres=gpu:<type>:1' -q <gpu part> nvidia-smi -L`
  - Look for: a GPU of that type. This is the passthrough, not `--device`.
- [ ] `hpc run --batch --license <name>=1 -q <part> sleep 60`, then
  `scontrol show licenses`
  - Look for: `Used` for that licence goes up by one while the job runs.
- [ ] If the site names a licence `name@server`: pass it with
  `-S '-L name@server:1'` and check the same.
- [ ] Two different licences, and two different devices or features, in
  one job (`--license a=1 --license b=1`).
  - Look for: the job is accepted and `scontrol show job` lists both. The
    testbed has one of each, so this has never been run.

## 4. A site `TMPDIR`

- [ ] `hpc run --batch --wait -q <part> sh -c 'echo "$TMPDIR"; ls -ld "$TMPDIR"; df -h "$TMPDIR"'`
  with `env_vars = { SCRATCH = "${hpc.tmpdir}" }` in the type, and `echo "$SCRATCH"` added to the command.
  - Look for: what `TMPDIR` is on a node. Is it per job (a job id in the
    path) or shared (`/tmp`)? Does `SCRATCH` hold the same value?
- [ ] Run it twice and compare; then look at the first job's directory
  after the job has ended.
  - Look for: whether the site removes it. hpc-runner makes and removes
    nothing.
- [ ] The same with `inherit_env = false`.
  - Look for: whether `TMPDIR` is still set, and to what.

## 5. `SBATCH_*` variables in a profile

The user docs say not to combine them with hpc-runner. This shows what
happens when they are.

- [ ] `env | grep -E '^(SBATCH|SLURM|SQUEUE|SACCT)_'` in a fresh login
  shell.
  - Look for: anything the site or the user sets by default. Note the
    names only.
- [ ] `SBATCH_PARTITION=<other part> hpc run --batch -q <part> sleep 30`,
  then `scontrol show job <id>`
  - Look for: which partition the job is in. If it is `<other part>`, the
    variable won over the `#SBATCH --partition` line.
- [ ] `SBATCH_EXPORT=NONE hpc run --batch --wait -q <part> sh -c 'echo "${DISPLAY-unset}"'`
  with `DISPLAY` set in the submitting shell and `inherit_env` left true.
  - Look for: `unset` means the variable won over `--export=ALL`.
- [ ] `SQUEUE_FORMAT='%i %j' hpc status` and `SACCT_FORMAT=JobID hpc status --history`
  - Look for: the tables are the same as without the variables. hpc-runner
    passes its own format on the command line.

## 6. The `--pty` rule at a real terminal

`srun` gets `--pty` only when stdin and stdout are both terminals. Run each
from an ordinary terminal with `hpc --verbose run`, which prints the `srun`
command line.

| | Command | `--pty` sent? | Look for |
|---|---|---|---|
| [ ] | `hpc --verbose run -q <part> sh -c 'tty; echo done'` | yes | `tty` prints a `/dev/pts/...` name |
| [ ] | `hpc --verbose run -q <part> sh -c 'tty; echo done' \| cat` | no | `tty` prints `not a tty`; `done` arrives through the pipe |
| [ ] | `hpc --verbose run -q <part> sh -c 'tty; echo done' > out.txt` | no | `out.txt` holds both lines and nothing else |
| [ ] | `echo input \| hpc --verbose run -q <part> cat` | no | `input` is printed: stdin reaches the job |
| [ ] | `hpc --verbose run -q <part> cat < /dev/null` | no | returns at once, exit 0 |
| [ ] | `echo input \| hpc --verbose run -q <part> cat > out.txt` | no | `out.txt` holds `input` |

- [ ] `hpc run -q <part> bash`, then type `echo $SLURM_JOB_ID`, press the
  up arrow, press Ctrl-D.
  - Look for: a prompt, line editing that works, a job id, and a clean
    return to the submitting shell with the terminal in a sane state.
- [ ] `hpc run -q <part> vim` (or `less /etc/hosts`, or `top`)
  - Look for: the full-screen program draws correctly and resizing the
    window is followed.
- [ ] A GUI tool: `hpc run -q <part> xterm` with `DISPLAY` set.
  - Look for: the window opens. With `inherit_env = false` it needs
    `DISPLAY` in `env_passthrough`. `srun --x11` is not used and not
    supported.
- [ ] `hpc run -q <part> sh -c 'echo "${HOME-unset} ${USER-unset} ${LOGNAME-unset}"; echo "$PATH"'`
  with `inherit_env = false` and no `env_passthrough`.
  - Look for: whether the three are empty and what `PATH` is. Then add
    them to `env_passthrough` and check they arrive.

## 7. Ctrl-C

- [ ] `hpc run -q <part> sleep 300`, wait until it runs, press Ctrl-C
  once; if nothing happens, twice quickly.
  - Look for: what `srun` prints, that `hpc run` returns, what `echo $?`
    prints, and that `squeue -u $USER` no longer lists the job. Check the
    terminal still echoes what is typed. Check no `hpc_interactive_*.sh` is
    left in the script directory.
- [ ] `hpc run -q <part> sleep 300` and press Ctrl-C **while the job is
  still pending** (pick a busy partition or add `-S '--begin=now+120'`).
  - Look for: `hpc run` returns, exits non-zero, and the pending job is
    gone from `squeue`.
- [ ] `hpc run --batch --wait -q <part> sleep 300`, press Ctrl-C once the
  job runs.
  - Look for: that `hpc run` returns and what `echo $?` prints (bare
    `sbatch --wait` exits 130). Then `squeue -u $USER`: is the job still
    running? On the testbed bare `sbatch --wait` leaves it running. If it
    is, `hpc cancel <id>` removes it. Check no `hpc_batch_*.sh` is left
    behind. This has never been run through `hpc run`.
- [ ] The same, Ctrl-C while the job is still pending.

## 8. `hpc monitor` on Slurm

Only its listing is tested. Check the styling rules in
`../tui/TEXTUAL_STYLING_COOKBOOK.md` while here.

- [ ] Submit a few jobs first: one running, one held (`-S '--hold'`), one
  array (`--array 1-6%2`), one that fails. Then `hpc monitor`.
  - Look for: the Active tab lists the running and held jobs with the
    right status, and the array as one row under its id.
- [ ] Press `u`.
  - Look for: other users' jobs appear, and the list is not slow on a busy
    cluster (it is one `squeue` of everything).
- [ ] Select a running job and press `Enter`.
  - Look for: details with the output path, the node and Slurm's own
    state; no traceback.
- [ ] Select the array and press `Enter`.
  - Look for: what is shown for an array whose tasks are partly pending.
- [ ] Switch to the Completed tab.
  - Look for: the finished and failed jobs, one row per array task, with
    exit codes. Jobs of other users appear only with `u`.
- [ ] Cancel the held job from the detail panel.
  - Look for: it leaves the Active tab on the next refresh.
- [ ] Leave it open for ten minutes.
  - Look for: it keeps refreshing, and the site's rate limits on `squeue`
    are not hit (no error line, no complaint from the admins' side).

## 9. Accounting retention and the controller's memory

The controller forgets a finished job after a while (the site's
`MinJobAge`); `sacct` is asked after that. The testbed never keeps a job
long enough to show either limit.

- [ ] `hpc run --batch -q <part> sh -c 'exit 137'`; note the id. Straight
  after it ends: `hpc status <id>` and `hpc status --history`.
  - Look for: exit code 137 in the first and 9 in the second, as the user
    docs say.
- [ ] `scontrol show job <id>` every minute until it says "Invalid job id
  specified"; note how long that took. Then `hpc status <id>`.
  - Look for: the job is still found, status FAILED, exit code now 9, and
    no output path.
- [ ] `hpc status <id>` for a job of your own from weeks or months ago
  (find one with `sacct -S <date> -X`).
  - Look for: either its details, or "job <id> not found". Note how far
    back the site's accounting goes.
- [ ] `hpc status --history --since 30d`
  - Look for: how long it takes and that it returns. One `sacct` call
    reads the whole window.
- [ ] A dependency on an old job: `hpc run --batch -D afterok:<an id the
  controller has forgotten> -q <part> true`
  - Look for: what Slurm does. On the testbed 24.11 refuses the
    submission and 22.05 accepts it and runs the job at once. This is not
    supported; the point is to know which the site does.

## 10. Site limits the testbed does not have

- [ ] `hpc run --batch -T <longer than the partition allows> -q <part> true`
  - Look for: is it refused at submission, or accepted and left pending
    with reason `PartitionTimeLimit`? If pending, `hpc cancel <id>`.
- [ ] If the site sets `memory_per = "cpu"`: `hpc run --batch -c 4 -m 16G -q <part> sleep 30`,
  then `scontrol show job <id>`.
  - Look for: `MinMemoryCPU=4G`, and that the site's `MaxMemPerCPU` did
    not raise the CPU count.
- [ ] `hpc run --batch -q <part> true` with no `--mem` and no `--time`.
  - Look for: the site's defaults apply and the job is accepted. Some
    sites refuse a job with no time limit or no account; the fix is a
    `[defaults]` entry.
- [ ] An MPI job, if the site runs them: `hpc run --batch -S '-N 2 -n 4' -q <part> srun hostname`
  - Look for: four lines from two hosts. hpc-runner also sends
    `--ntasks=1` earlier in the script; this shows whether the later flag
    wins, which no test proves.

## Afterwards

- Each item that differed from the user docs: say so in the project note
  and add it to `../FOLLOW_UPS.md`.
- Each behaviour that can be reproduced in Docker: a testbed case and a
  capture, so a test can keep it.
- Delete the job scripts, output files and traces made on the cluster.
