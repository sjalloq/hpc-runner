# Slurm flavours

Each Slurm release is a *flavour*: a directory with its own image and
compose file. The configuration (`common/conf/`), the entrypoint and
`ready.sh`/`clean.sh` are shared (`common/`). The fixture
(`tests/integration/cluster.py`, `SlurmCluster`) and the test files are
shared by the flavours; `--scheduler slurm --flavour <name>` selects the
stack that is up.

| Flavour | Source | Status |
|---|---|---|
| `24.11` | Debian trixie `slurm-wlm` 24.11.5 | stack runs; no integration test run yet |
| `22.05` | Debian bookworm `slurm-wlm` 22.05.8 | stack runs; no integration test run yet |

hpc-runner has no Slurm backend yet (`docs/dev/slurm/PLAN.md`, "Order of
work"), so the integration suite cannot run here. The stacks are used to
run and record Slurm behaviour (`docs/dev/slurm/runs/`) and to take
captures.

## Bringing a flavour up

```bash
hpc-testbed --scheduler slurm up                    # 24.11, the default
hpc-testbed --scheduler slurm --flavour 22.05 up
hpc-testbed --scheduler slurm --flavour 22.05 shell # bash in login as tester
hpc-testbed --scheduler slurm --flavour 22.05 capture
hpc-testbed --scheduler slurm --flavour 22.05 down
```

Both stacks can be up together: containers carry the compose project as a
prefix (`hpc-testbed-slurm-22-05-master`) and the subnets differ.

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
22.05 flavour and nothing for 24.11.

## What the cluster class cannot answer

- `accounting_truncate`: Slurm's accounting is rows in slurmdbd's MariaDB
  database, not a file that can be emptied from `login`. The test that
  needs it (`test_aged_out`) is SGE's own.

## Support matrix

One row per integration test. The files in `tests/integration/` are the
generic tests and run on every scheduler; Slurm's own tests will be in
`tests/integration/slurm/`, which does not exist yet.

No integration test has been run on Slurm: every one needs the Slurm
backend. A test deselected for a flavour with
`@pytest.mark.flavour("slurm/<name>")` would show as such; nothing skips at
run time.

### Generic tests (`tests/integration/`)

| File | Test | `24.11` | `22.05` |
|---|---|---|---|
| test_smoke.py | test_batch_echo_hello | not run | not run |
| test_submit.py | test_batch_echo | not run | not run |
| | test_rejected_queue | not run | not run |
| | test_interactive | not run | not run |
| | test_interactive_no_slot | not run | not run |
| test_status.py | test_queued_running_done | not run | not run |
| | test_suspended_job_is_waited_through | not run | not run |
| test_dependencies.py | test_afterok_cascade | not run | not run |
| | test_afternotok | not run | not run |
| | test_afterany | not run | not run |
| | test_array_parent | not run | not run |
| | test_cli_depend | not run | not run |
| test_arrays.py | test_array_1_to_5 | not run | not run |
| | test_array_concurrency_limit_is_accepted | not run | not run |
| test_resources.py | test_queue_selection | not run | not run |
| test_environment.py | test_modules_on_node | not run | not run |
| | test_regardless_of_submitter | not run | not run |
| | test_hpc_cpus | not run | not run |
| | test_env_order | not run | not run |
| | test_venv | not run | not run |
| | test_inherit_env_false | not run | not run |
| | test_cwd | not run | not run |
| | test_cwd_with_space | not run | not run |
| test_listing.py | test_two_users | not run | not run |
| | test_completed_listing_by_user | not run | not run |
| test_cli.py | test_status_active_history_and_details | not run | not run |
| | test_cancel_by_filter_by_id_and_a_job_that_is_gone | not run | not run |
| | test_run_exits_with_the_jobs_exit_code | not run | not run |
| | test_tool_alias_and_config_resolve | not run | not run |
| test_trace.py | test_flag_records_a_session | not run | not run |
| | test_config_enabled_trace | not run | not run |

### Slurm's own tests (`tests/integration/slurm/`)

None written yet.
