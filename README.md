# hpc-runner

**Submit HPC jobs by tool name, not by scheduler flag.**

`hpc run xrun -f top.f` turns into the right `qsub`/`qrsh` call for your
site, with the queue, slots, memory, licences and Environment Modules that
the config file says an Xcelium run needs. Everyone gets the same module
versions, so a flow that works for one user works for all of them.

Full documentation: <https://sjalloq.github.io/hpc-runner/>

## Features

- **One CLI and one Python API** in front of the scheduler's own commands.
- **Config by tool or job type**: queue, cpu, mem, time, modules and
  resources keyed on the command's first word, or on a `-t` type for
  makefiles and scripts.
- **Reproducible environments**: every job script runs `module purge` and
  loads the configured versions.
- **Dependencies, arrays and pipelines**, with `afterok`/`afternotok`
  emulated on SGE.
- **Validated config**: unknown keys and wrong types are errors with file,
  line and a "did you mean".
- **A terminal monitor**, `hpc monitor`, for watching your jobs.

## Scheduler compatibility

| Scheduler | Status | Notes |
|-----------|--------|-------|
| Sun Grid Engine / Open Grid Scheduler | Supported | qsub, qrsh, qstat, qacct, qdel. Integration suite runs against gridengine 8.1.9 in CI. |
| Local | Supported | Runs the job on this machine with the same module environment; no scheduler needed. |
| Slurm | Planned | |
| PBS / Torque | Planned | |
| LSF | Planned | |

A scheduler moves to *Supported* when the integration suite passes against
it. The scheduler is auto-detected; `hpc --scheduler NAME` or
`HPC_SCHEDULER=NAME` forces one.

## Installation

```bash
pip install hpc-runner
hpc config init          # starter hpc-runner.toml in the current directory
```

Python 3.10 or later.

## Quick start

```bash
# Interactive on the cluster (the default): output streams here, hpc waits
hpc run xterm

# Batch job, return immediately; -t picks a job type from the config
hpc run --batch -t xcelium make sim

# Resources for one run, and block until it finishes
hpc run --batch --wait --cpu 4 --mem 16G --time 4:00:00 make sim

# Preview the generated script without submitting
hpc run --dry-run --cpu 8 make -j8

# Raw scheduler flags go in one quoted string
hpc run -S '-l gpu=1 -P proj' python train.py
```

The same three steps from Python: a `JobSpec` says what you want,
`resolve()` applies the config, a scheduler submits the frozen `Job`.

```python
# doctest: local
from hpc_runner import Host, JobSpec, get_scheduler, load_config, resolve

config = load_config()
scheduler = get_scheduler(config=config)
job = resolve(JobSpec("python train.py", cpu=4, mem="16G"), config, Host.capture())
result = scheduler.submit(job)
print(result.wait(), result.returncode)
print(result.read_stdout())
```

Dependencies, array jobs, pipelines and reconnecting to a job by id are in
the [Python API guide](https://sjalloq.github.io/hpc-runner/programmatic_api.html).

## Configuration

`hpc-runner.toml` at the git root is the project config; one in the current
directory overrides it. Settings layer from `[defaults]` through the tool
matched by the command (or the tools a `-t` type pulls in) to the command
line, and `hpc config resolve CMD` shows which layer set each value.

```toml
config_version = 1

[defaults]
cpu = 1
mem = "4G"
time = "1:00:00"
modules_path = ["/site/modulefiles"]

[schedulers.sge]
parallel_environment = "smp"
memory_resource = "mem_free"

[tools.xcelium]
commands = ["xrun", "xmvlog", "xmelab", "simvision"]   # one entry, several executables
modules = ["xcelium/23.09"]
resources = [{ name = "xcelium_lic", value = 1 }]

[tools.slang]
modules = ["slang/0.9"]

[types.lint]
tools = ["xcelium", "slang"]   # pulled in, in this order, then the keys below
cpu = 2
```

Every key, the merge rules, variable expansion and the SGE mapping are in
the [configuration reference](https://sjalloq.github.io/hpc-runner/configuration.html)
and the [SGE guide](https://sjalloq.github.io/hpc-runner/sge.html).

## Documentation

- [Getting started](https://sjalloq.github.io/hpc-runner/getting_started.html)
- [Configuration reference](https://sjalloq.github.io/hpc-runner/configuration.html)
- [CLI reference](https://sjalloq.github.io/hpc-runner/cli.html)
- [SGE guide](https://sjalloq.github.io/hpc-runner/sge.html)
- [Python API](https://sjalloq.github.io/hpc-runner/programmatic_api.html)
- [Job monitor](https://sjalloq.github.io/hpc-runner/monitor.html)
- [Development](https://sjalloq.github.io/hpc-runner/development.html)

## Development

```bash
source sourceme                   # .venv with the dev extras
pytest                            # unit suite
hpc-testbed pytest --up           # integration suite against SGE in Docker
```

See the [development page](https://sjalloq.github.io/hpc-runner/development.html)
for the layout, the testbed, golden scripts, docs and releases.

## License

MIT License - see LICENSE file for details.
