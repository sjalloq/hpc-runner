# SGE flavours

Each vendor implementation of the SGE lineage is a *flavour*: a self-contained
directory with its own image, compose file, cell configuration and testbed
config, tested on its own and never grouped with another (docs/dev/testbed/TESTBED_PLAN.md
section 1). The fixture (`tests/integration/cluster.py`) and the test files
are shared; `--flavour <name>` selects the stack that is up.

| Flavour | Lineage | Source | Status |
|---|---|---|---|
| `soge` | Son of Grid Engine 8.1.9 | Debian trixie `gridengine-*` 8.1.9+dfsg-13.1 | stack up; 31 integration tests (phase 3), 3 strict xfails pinning BUG-T1..T3 |

Candidates for later phases (plan section 6, phase 5): Open Cluster Scheduler
(Univa lineage), `daimh/sge` (maintained SoGE fork, built from source).

## Support matrix

One row per integration test, grouped by file (tests/integration/). The
`soge` column is the result of the integration suite (`hpc-testbed pytest`) on 2026-10-29:
`pass`; `xfail(BUG-Tn)` for a strict expected failure pinning a bug listed
in docs/dev/testbed/TESTBED_REVIEW.md ("Phase 3 findings"), which flips to a hard pass when
fixed. A test deselected for a flavour with `@pytest.mark.flavour` would also
appear here (none so far); nothing skips at run time.

| File | Test | `soge` |
|---|---|---|
| test_submit.py | test_batch_echo | pass |
| | test_passthrough | pass |
| | test_rejected_queue | pass |
| | test_rejected_resource (`-l nosuch=1`; `gpu=2` is accepted and queues, D-T27) | pass |
| | test_interactive | pass |
| | test_interactive_no_slot (all eight slots filled, D-T28) | pass |
| test_status.py | test_queued_running_done | pass |
| | test_slow_accounting_window | pass on `hpc-testbed pytest --slow-accounting` against that stack (D-T22); deselected otherwise |
| | test_aged_out | pass |
| test_dependencies.py | test_afterok_cascade | pass |
| | test_afternotok | pass |
| | test_afterany | pass |
| | test_array_parent | pass |
| | test_cli_depend | pass |
| test_arrays.py | test_array_1_to_5 | pass |
| test_resources.py | test_pe_mem_time | pass |
| | test_memory_per_consumable[slot], [job] (h_vmem consumable, 8G per host; reads `qc:h_vmem` back) | pass |
| | test_h_rt_kill (TIMEOUT from `failed 37`, D-T30) | pass |
| | test_signal_exit_code (exit 137 of a signal death, BUG-T5 fixed) | pass |
| | test_h_vmem | pass |
| | test_queue_selection | pass |
| test_environment.py | test_modules_on_node | pass |
| | test_regardless_of_submitter | pass |
| | test_hpc_cpus | pass |
| | test_env_order | pass |
| | test_venv | pass |
| | test_inherit_env_false | pass |
| | test_cwd | pass |
| | test_cwd_with_space | xfail(BUG-T1) |
| test_listing.py | test_two_users | pass |
| | test_completed_listing_by_user | xfail(BUG-T2) |
| test_cli.py | test_status_active_history_and_details (`hpc status`, `--history`, `<id>`, `--json`) | pass |
| | test_status_shows_the_schedulers_failure_reason (qdel: exit 137, `100: assumedly after job`) | pass |
| | test_tool_alias_and_config_resolve (`[tools.sort] commands`, `hpc config resolve`) | pass |
| test_goldens.py | test_qsub_verifies_the_golden[4 SGE batch goldens] (`qsub -w v`; two verify, two get the expected site rejection) | pass |
| test_failures.py | test_master_stopped (BUG-T6 fixed; fails if the Docker socket is not mounted) | pass |
| | test_node_killed (BUG-T3 fixed; fails if the Docker socket is not mounted) | pass |
| | shared FS missing | not automated (D-T23); see "Manual checks" |

### Manual checks

- **Shared filesystem missing on an exec node** (plan section 5, "shared FS
  missing"; docs/dev/testbed/TESTBED_REVIEW.md D-T23). Not automated: the `/work` volume is
  shared by every container and cannot be hidden on one node from inside
  `login`. By hand: start the stack with a compose override that omits the
  `work` volume from `exec1` (for example a `compose.override.yml` giving
  `exec1` a `volumes:` list without `/work`, passed with a second `-f`), pin
  a job there (`JobSpec("echo hi", resources=[{"name": "hostname", "value":
  "exec1"}])` or `qsub -l hostname=exec1`), and observe the symptom: the
  job's `#$ -o /work/...` cannot be opened, SGE puts the job in `Eqw`
  ("can't open output file") or it fails at once, `get_status` reports
  FAILED (`Eqw`) or the job never leaves the queue, and `read_stdout()`
  returns None because the output file was never created on the shared
  volume. Remove the override and `hpc-testbed up` to restore the stack.

Flags and output formats that differ between flavours are noted under the
flavour's row once a second flavour exists.
