# SGE flavours

Each vendor implementation of the SGE lineage is a *flavour*: a self-contained
directory with its own image, compose file, cell configuration and testbed
config, tested on its own and never grouped with another (docs/dev/testbed/TESTBED_PLAN.md
section 1). The fixture (`tests/integration/cluster.py`) and the test files
are shared; `--flavour <name>` selects the stack that is up.

| Flavour | Lineage | Source | Status |
|---|---|---|---|
| `soge` | Son of Grid Engine 8.1.9 | Debian trixie `gridengine-*` 8.1.9+dfsg-13.1 | 49 integration tests pass |
| `ocs` | Open Cluster Scheduler 9.1.7 (Univa lineage) | upstream binary tarballs (`ocs-9.1.7-bin-lx-amd64`, `-common`) on Debian trixie | 49 integration tests pass |

Candidate for a later flavour: `daimh/sge` (maintained SoGE fork, built from
source).

Only one flavour's stack can run at a time: both use the container names
`master`, `exec1`, `exec2` and `login`.

## Support matrix

One row per integration test, grouped by file (tests/integration/). Each
column is the result of `hpc-testbed --flavour <name> pytest` on
2026-11-02. A test deselected for a flavour with `@pytest.mark.flavour`
would show as such (none so far); nothing skips at run time.

| File | Test | `soge` | `ocs` |
|---|---|---|---|
| test_smoke.py | test_batch_echo_hello | pass | pass |
| test_submit.py | test_batch_echo | pass | pass |
| | test_passthrough | pass | pass |
| | test_rejected_queue | pass | pass |
| | test_rejected_resource (`-l nosuch=1`; `gpu=2` is accepted and queues, D-T27) | pass | pass |
| | test_interactive | pass | pass |
| | test_interactive_no_slot (all eight slots filled, D-T28) | pass | pass |
| test_status.py | test_queued_running_done | pass | pass |
| | test_slow_accounting_window | pass on `hpc-testbed pytest --slow-accounting` (D-T22); deselected otherwise | not run |
| | test_aged_out | pass | pass |
| | test_suspended_job_is_waited_through | pass | pass |
| | test_requeued_job_ends_with_its_final_record | pass | pass |
| | test_deleted_while_pending_waits_out_to_unknown | pass | pass |
| test_dependencies.py | test_afterok_cascade | pass | pass |
| | test_afternotok | pass | pass |
| | test_afterany | pass | pass |
| | test_array_parent | pass | pass |
| | test_cli_depend | pass | pass |
| test_arrays.py | test_array_1_to_5 | pass | pass |
| | test_array_concurrency_limit_is_accepted | pass | pass |
| test_resources.py | test_pe_mem_time | pass | pass (requests read from `JB_request_set_list`) |
| | test_memory_per_consumable[slot], [job] (h_vmem consumable, 8G per host; reads `qc:h_vmem` back) | pass | pass |
| | test_h_rt_kill (KILLED, exit 137) | pass (`failed 37`) | pass (`failed 100`, `deleted_by execd@`) |
| | test_signal_exit_code (exit 137 of a signal death, BUG-T5 fixed) | pass | pass |
| | test_h_vmem | pass | pass |
| | test_queue_selection | pass | pass |
| test_environment.py | test_modules_on_node | pass | pass |
| | test_regardless_of_submitter | pass | pass |
| | test_hpc_cpus | pass | pass |
| | test_env_order | pass | pass |
| | test_venv | pass | pass |
| | test_inherit_env_false | pass | pass |
| | test_cwd | pass | pass |
| | test_cwd_with_space | pass | pass |
| test_listing.py | test_two_users | pass | pass (ISO timestamps) |
| | test_completed_listing_by_user | pass | pass |
| test_cli.py | test_status_active_history_and_details (`hpc status`, `--history`, `<id>`, `--json`, times) | pass | pass (`<>` tags repaired) |
| | test_status_shows_the_schedulers_failure_reason (qdel: exit 137, `100: assumedly after job`) | pass | pass |
| | test_tool_alias_and_config_resolve (`[tools.sort] commands`, `hpc config resolve`) | pass | pass |
| | test_hold_flag_forwards | pass | pass |
| test_goldens.py | test_every_sge_batch_golden_is_covered | pass | pass |
| | test_qsub_verifies_the_golden[4 SGE batch goldens] (`qsub -w v`; two verify, two get the expected site rejection) | pass | pass (`-binding linear:1` is rejected: OCS 9.1 changed the syntax) |
| test_failures.py | test_node_killed (BUG-T3 fixed; fails if the Docker socket is not mounted) | pass | pass |
| | qmaster stopped | not automated (D-T40): the `master_down` capture scenario and a unit test pin the error | not automated |
| test_trace.py | test_flag_records_and_compares_clean (`hpc --trace`, `hpc trace compare --fixtures`) | pass | pass |
| | test_config_enabled_trace (`[trace] enabled`, dedupe across invocations) | pass | pass |
| | test_bundle_is_scrubbed_and_replays (`hpc trace bundle`) | pass | pass |
| | shared FS missing | not automated (D-T23); see "Manual checks" | not automated |

### Where OCS output differs from SoGE

hpc-runner reads both without being told which it is talking to; each
difference is handled by the shape of the output (TESTBED_REVIEW D-T49).

- `qstat -j <unknown id> -xml` writes list elements as `<>` ... `</>`
  (fixed upstream as CS-1991, in no release yet); repaired to `<element>`.
- `qstat -j -xml` keeps resource requests per `-scope` under
  `JB_request_set_list`; the global set is read.
- Times are ISO 8601 with microseconds everywhere (`qstat -xml`,
  `qstat -j -xml`, `qacct`, plain `qstat`); a pending job's submit time is
  `JAT_submission_time`.
- An `h_rt`/`h_cpu`/`h_rss`/`h_vmem` kill is enforced by execd:
  `failed 100` with `deleted_by execd@<host>` (KILLED).
- Accounting is `$SGE_ROOT/default/common/accounting.jsonl`.
- `qsub -binding linear:1` is rejected (9.1 binding syntax).

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
