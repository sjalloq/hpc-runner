# Slurm flavours

Each Slurm release is a *flavour*: a directory with its own image and
compose file. The configuration (`common/conf/`), the entrypoint and
`ready.sh`/`clean.sh` are shared (`common/`). The fixture
(`tests/integration/cluster.py`, `SlurmCluster`) and the test files are
shared by the flavours; `--scheduler slurm --flavour <name>` selects the
stack that is up.

| Flavour | Source | Status |
|---|---|---|
| `24.11` | Debian trixie `slurm-wlm` 24.11.5 | stack runs; results in "Support matrix" below |
| `22.05` | Debian bookworm `slurm-wlm` 22.05.8 | stack runs; results in "Support matrix" below |

The integration suite runs on both (`hpc-testbed --scheduler slurm --flavour
<name> pytest`): the generic tests and Slurm's own in
`tests/integration/slurm/`. The stacks are also used to run and record Slurm
behaviour (`docs/dev/slurm/runs/`) and to take captures ("Captures" below).

## Bringing a flavour up

```bash
hpc-testbed --scheduler slurm up                    # 24.11, the default
hpc-testbed --scheduler slurm --flavour 22.05 up
hpc-testbed --scheduler slurm --flavour 22.05 shell # bash in login as tester
hpc-testbed --scheduler slurm --flavour 22.05 pytest # the integration suite
hpc-testbed --scheduler slurm --flavour 22.05 capture
hpc-testbed --scheduler slurm --flavour 22.05 down
```

Both stacks can be up together: containers carry the compose project as a
prefix (`hpc-testbed-slurm-22-05-master`) and the subnets differ.

## Captures

`hpc-testbed --scheduler slurm --flavour <name> capture` runs
`tests/integration/capture_slurm.py` in `login` and replaces
`tests/unit/fixtures/slurm/<name>/` with what it wrote. A run takes about
four minutes, kills and restarts `exec1`, and leaves the stack clean. Job
ids, timestamps and host names change with every run.

Every job is submitted with plain `sbatch`. For each scenario the module
keeps what a backend reads:

| File | Command |
|---|---|
| `sbatch_<scenario>.json` | the submission: argv, exit status, stdout, stderr |
| `sacct_<scenario>[_<stage>].txt` | `sacct -P -n -j <id> --format=<SACCT_FORMAT>` (no `-X`) |
| `sacct_..._duplicates.txt` | the same with `-D` |
| `squeue_<scenario>[_<stage>].txt` | `squeue -h -j <id> -o <SQUEUE_FORMAT>`, while the job is live |
| `squeue_..._tasks.txt` | the same with `-r`, one line per array task |
| `scontrol_<scenario>[_<stage>].txt` | `scontrol show job <id>` |
| `scancel_<scenario>.json` | `scancel <id>`, where the scenario cancels |
| `sbatch_wait_<scenario>.json` | `sbatch --wait` of the job `wait_<scenario>`: argv, exit status, stdout, stderr |

The two field lists are the constants `SACCT_FIELDS` and `SQUEUE_FIELDS` of
the module, the job name last in both. Both Slurm versions accept every
name in them. `manifest.json` records the two lists, the job id of every
scenario, and for each file what it is and its argv.

| Scenario | What it is | Stages |
|---|---|---|
| `ok`, `exit1` | exit 0, exit 1 | |
| `signal` | the batch shell kills itself with SIGKILL | |
| `exit137` | the script exits 137 | |
| `timeout` | `--time=1`, killed at the limit | |
| `out_of_memory` | 200 MB allocated under `--mem=50M` | |
| `out_of_memory_step` | the same inside an `srun` step, script exits 0 | |
| `cancelled_pending`, `cancelled_running` | `scancel` of a held and of a running job | |
| `held` | `--hold` | |
| `dependent_ok` | `afterok`, parent exits 0 | `before` (parent not yet run), end |
| `dependent_skipped` | `afterok --kill-on-invalid-dep=yes`, parent exits 1 | `before`, end |
| `dependent_pending` | `afterok --kill-on-invalid-dep=no`, parent exits 1 | `before`, pending for ever, `cancelled` |
| `array` | four tasks | `pending` (held), end |
| `array_partial` | three tasks, task 3 exits 1 | |
| `array_throttled` | `--array=1-4%2` | two running and two pending, `cancelled` |
| `array_cancelled_pending` | a held array cancelled before any task ran | |
| `suspended` | `scontrol suspend` as root | |
| `requeued` | `scontrol requeue` of a running job | pending again, `running` a second time |
| `node_fail` | `--no-requeue`, `exec1` killed | |
| `node_fail_requeued` | the same without `--no-requeue`: the controller requeues it | |
| `running` | a running job | |
| `pending_resources` | two CPUs asked while no node has two free | |
| `bad_partition`, `bad_account` | rejected by `sbatch` and by `srun` (`srun_<scenario>.json`) | |
| `array_list`, `array_stepped`, `array_single` | held arrays, `--array=1,3,7-9:2`, `1-100:3` and `5` | |
| `missing` | `sacct`, `squeue`, `scontrol show job`, `scancel` of an id that never existed (JSON) | |
| `not_owner` | `scancel` of another user's job (JSON) | |
| `wait_ok`, `wait_exit1`, `wait_exit137`, `wait_out_of_memory`, `wait_array_partial`, `wait_cancelled_pending`, `wait_cancelled_running`, `wait_dependent_skipped` | the jobs of the same names, submitted with `sbatch --wait` | |

Also `squeue_empty.txt` (nothing queued), `squeue_all.txt` (running,
suspended, held and pending-for-resources jobs together),
`sacct_several.txt` (`-j` with three ids) and `sacct_history.txt` (`sacct
-u tester -S <when the capture began>`, taken while the snapshot's jobs
are live; the time is `history_since` in the manifest).

Both flavours give the same files, with one difference. On 22.05 the first
`sacct` row of a new job can be an early one, with the name `allocation`
and no partition. The module hunts for it and keeps it as
`sacct_ok_stub.txt` (a finished job) and `sacct_running_stub.txt` (a running
job, whose own row says `PENDING`). 24.11 has never shown such a row, so
those two files exist for 22.05 only. Every other `sacct` capture waits
for the whole row.

`node_fail` depends on `SlurmdTimeout=20` in `common/conf/slurm.conf`: the
controller gives a silent node up after 20 s. `ReturnToService=2` puts
`exec1` back in service when its slurmd registers again.

## What a stack contains

- `master` (slurmctld, slurmdbd, munged), `db` (MariaDB), `exec1`, `exec2`
  (slurmd), `login` (the clients, the venvs, the repository at `/src`).
- `login` exports `HPC_RUNNER_CONFIG=/src/testbed/slurm/<flavour>/hpc-runner-testbed.toml`,
  which names `scheduler = "slurm"` and the `[tools.sort]` entry the generic
  alias test reads.
- Two partitions over the same two nodes: `all` (default, no time limit) and
  `short` (ten minutes). Each node has two CPUs and 2000 MB, so the cluster
  has four one-CPU slots whichever partition they are asked from.
- `exec1` has the feature `fast` and one GPU that is only a count
  (`gres.conf`: `File=/dev/null`). Two licences (`lic`). Temp disk on both
  nodes.
- Accounting is always on and accounts are enforced. Every user is in
  `proj`; `other` has no user.
- Users `alice`, `bob`, `tester`, as in every testbed.

### Cgroups

Jobs run in cgroups on both flavours, so `--mem` is a limit the kernel
enforces: `ProctrackType=proctrack/cgroup` and `TaskPlugin=task/cgroup` in
`slurm.conf`; `CgroupPlugin=cgroup/v2`, `ConstrainRAMSpace`,
`ConstrainSwapSpace` with `AllowedSwapSpace=0`, and `ConstrainCores` in
`cgroup.conf`.

- `IgnoreSystemd=yes`: the containers have no systemd and no dbus, so slurmd
  makes its own cgroup directories.
- `ConstrainDevices` is not set: the fake GPU's device file is `/dev/null`.
- `exec1` and `exec2` are `privileged`, so slurmd may write the cgroup tree,
  and run with `init: true` (tini is PID 1). `master`, `db` and `login` are
  not privileged.
- Before slurmd starts, the entrypoint (`prepare_cgroups` in
  `common/entrypoint.sh`) makes the `system.slice` and `init` cgroups, moves
  every process out of the container's root cgroup into `init`, and turns on
  `cpuset cpu memory pids` for the children of the root and of
  `system.slice`. The same steps run on both flavours.

What this gives, on both flavours:

- A job that uses more than `--mem` ends `OUT_OF_MEMORY` with `ExitCode
  0:125` in `sacct`, on its own row and on the `.batch` row.
- `MaxRSS` is filled on the `.batch` row.
- A `--export=NONE` job runs, and a `--gres=gpu:1` job runs on `exec1`.

### Known output lines on 22.05

On 22.05 every batch job's output file ends with three lines the job did
not write:

```
slurmstepd-exec1: error: common_file_write_uint32s: write pid 664 to /sys/fs/cgroup/cgroup.procs failed: Device or resource busy
slurmstepd-exec1: error: Unable to move pid 664 to init root cgroup /sys/fs/cgroup
slurmstepd-exec1: error: error unlocking cgroup '/sys/fs/cgroup/system.slice/exec1_slurmstepd.scope' : Bad file descriptor
```

When a step ends, 22.05's slurmstepd moves itself to the cgroup mount
point. In a container that is not the real root of the cgroup tree, so the
kernel refuses. The job's state and exit code are not affected. Each job
also leaves a `job_<id>` cgroup directory under
`/sys/fs/cgroup/system.slice/<node>_slurmstepd.scope/` on its node. Both
are accepted as they are. 24.11 writes no such lines and leaves no
directory.

`SlurmCluster.extra_output_lines` gives the three lines as patterns for the
22.05 flavour and nothing for 24.11. A generic test that compares a job's
whole output compares what `job_output` (`tests/integration/helpers.py`)
returns: the text less those lines, dropped only when the text ends with all
of them in order. `slurm/test_output_lines.py` checks the patterns against a
real job on the flavour that has them.

## What the cluster class cannot answer

- `accounting_truncate`: Slurm's accounting is rows in slurmdbd's MariaDB
  database, not a file that can be emptied from `login`. The test that
  needs it (`test_aged_out`) is SGE's own.

## Support matrix

One row per integration test, grouped by file. The files in
`tests/integration/` are the generic tests: they check what hpc-runner does
by its outcome on the cluster, name no scheduler command, and run on every
scheduler. The files in `tests/integration/slurm/` are Slurm's own: they
check that hpc-runner drives Slurm correctly and that Slurm does what
hpc-runner says, and run only with `--scheduler slurm`.

Each column is the result of `hpc-testbed --scheduler slurm --flavour <name>
pytest` on 2026-11-23 (78 passed and 1 skipped on `24.11`, 79 passed on
`22.05`). A test deselected for a flavour with
`@pytest.mark.flavour("slurm/<name>")` would show as such (none so far). One
test skips at run time, by the cluster class's answer: the check of the
output lines only 22.05 adds.

### Generic tests (`tests/integration/`)

| File | Test | `24.11` | `22.05` |
|---|---|---|---|
| test_smoke.py | test_batch_echo_hello | pass | pass |
| test_submit.py | test_batch_echo | pass | pass |
| | test_rejected_queue | pass | pass |
| | test_interactive | pass | pass |
| | test_interactive_job_has_its_name (`name` on an interactive job) | pass | pass |
| | test_interactive_no_slot (all four slots filled) | pass | pass |
| test_status.py | test_queued_running_done | pass | pass |
| | test_suspended_job_is_waited_through | pass | pass |
| test_dependencies.py | test_afterok_cascade | pass | pass |
| | test_afternotok | pass | pass |
| | test_afterany | pass | pass |
| | test_array_parent | pass | pass |
| | test_cli_depend | pass | pass |
| test_arrays.py | test_array_1_to_5 | pass | pass |
| | test_array_concurrency_limit_is_accepted | pass | pass |
| test_resources.py | test_queue_selection | pass | pass |
| test_environment.py | test_modules_on_node | pass | pass |
| | test_regardless_of_submitter | pass | pass |
| | test_hpc_cpus | pass | pass |
| | test_hpc_cpus_in_a_list_command (`${hpc.cpus}` in a word of the command) | pass | pass |
| | test_hpc_cpus_in_a_string_command | pass | pass |
| | test_env_order | pass | pass |
| | test_venv | pass | pass |
| | test_inherit_env_false | pass | pass |
| | test_cwd | pass | pass |
| | test_cwd_with_space | pass | pass |
| test_listing.py | test_two_users | pass | pass |
| | test_completed_listing_by_user | pass | pass |
| test_cli.py | test_status_active_history_and_details | pass | pass |
| | test_cancel_by_filter_by_id_and_a_job_that_is_gone | pass | pass |
| | test_run_exits_with_the_jobs_exit_code | pass | pass |
| | test_run_renders_hpc_cpus_in_the_command (`hpc run ... echo '-j${hpc.cpus}'`) | pass | pass |
| | test_tool_alias_and_config_resolve | pass | pass |
| test_trace.py | test_flag_records_a_session | pass | pass |
| | test_config_enabled_trace | pass | pass |

### Slurm's own tests (`tests/integration/slurm/`)

| File | Test | `24.11` | `22.05` |
|---|---|---|---|
| slurm/test_goldens.py | test_every_slurm_batch_golden_is_covered | pass | pass |
| | test_sbatch_reads_the_golden[4 Slurm batch goldens] (`sbatch --test-only`; one would start, two are too big for the testbed, one carries SGE flags in `scheduler_args`) | pass | pass |
| slurm/test_resources.py | test_cpu_mem_time_queue_and_project (`scontrol` and `sacct` record each; `nproc` in the job is 2) | pass | pass |
| | test_memory_per_cpu_divides_mem_by_the_cpus (`--mem-per-cpu=150`, 300M booked) | pass | pass |
| | test_licences_devices_and_features (a job asking for what another holds waits; one asking for none runs on `exec2`) | pass | pass |
| | test_time_limit_kill (TIMEOUT, exit 143; two `sacct` rows) | pass | pass |
| | test_cancelled_while_running (CANCELLED, exit 143; two `sacct` rows) | pass | pass |
| | test_over_its_memory_is_killed (KILLED, `OUT_OF_MEMORY`) | pass | pass |
| | test_within_its_memory_runs | pass | pass |
| slurm/test_status.py | test_cancelled_while_pending_is_cancelled_with_no_exit_code | pass | pass |
| | test_requeued_job_is_waited_through_to_its_second_run (`scontrol requeue`; about two and a half minutes) | pass | pass |
| | test_a_job_just_submitted_has_a_status_at_once (`get_status` on the line after `submit`: PENDING, not UNKNOWN) | pass | pass |
| slurm/test_dependencies.py | test_every_skipped_job_is_cancelled_by_slurm_with_no_exit_code | pass | pass |
| | test_without_kill_invalid_dependents_the_child_stays_pending | pass | pass |
| slurm/test_waited.py | test_hpc_run_wait_exits_as_the_job_did[exit 3, exit 137, segfault, over its memory] | pass | pass |
| | test_submit_wait_gives_the_same[the same four] | pass | pass |
| | test_a_time_limit_exits_1_with_timeout | pass | pass |
| | test_a_skipped_job_exits_1 | pass | pass |
| slurm/test_cli.py | test_history_has_sacct_exit_code_and_details_the_controllers (exit 137: 9 in `--history`, 137 in `hpc status <id>`) | pass | pass |
| | test_status_shows_slurms_own_state (`CANCELLED by <uid>`, `OUT_OF_MEMORY`) | pass | pass |
| | test_hold_flag_forwards | pass | pass |
| slurm/test_listing.py | test_details_of_another_users_job_have_state_project_and_command | pass | pass |
| slurm/test_submit.py | test_passthrough | pass | pass |
| | test_rejected_queue_carries_sbatchs_words | pass | pass |
| | test_rejected_request[8 requests the testbed cannot meet] | pass | pass |
| slurm/test_trace.py | test_trace_holds_the_slurm_script_and_squeue_rows | pass | pass |
| slurm/test_failures.py | test_node_killed (`--no-requeue`; FAILED, `NODE_FAIL`; fails if the Docker socket is not mounted) | pass | pass |
| slurm/test_output_lines.py | test_a_batch_jobs_error_stream_ends_with_exactly_the_listed_lines | skips: no such lines | pass |

### SGE's own tests with no Slurm test

| SGE test | Why Slurm has none |
|---|---|
| sge/test_status.py test_slow_accounting_window | `sacct` has a job's row while it is still pending; there is no gap to wait out |
| sge/test_status.py test_aged_out | the accounting store cannot be emptied from `login` ("What the cluster class cannot answer") |
| sge/test_resources.py test_memory_per_consumable | measures SGE's per-slot booking of a consumable; `memory_per = "cpu"` is tested Slurm's way in test_memory_per_cpu_divides_mem_by_the_cpus |
| sge/test_dependencies.py test_every_skipped_job_exits_113 | 113 is the SGE guard's code; Slurm's own way is tested in test_every_skipped_job_is_cancelled_by_slurm_with_no_exit_code |
| sge/test_cli.py test_history_reports_failed_0_for_a_job_that_ran | SGE's `failed` field; Slurm has none, which the first slurm/test_cli.py test states |
