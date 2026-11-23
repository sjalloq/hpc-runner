# hpc-runner

[![PyPI](https://img.shields.io/pypi/v/hpc-runner)](https://pypi.org/project/hpc-runner/)
[![Python](https://img.shields.io/pypi/pyversions/hpc-runner)](https://pypi.org/project/hpc-runner/)
[![CI](https://github.com/sjalloq/hpc-runner/actions/workflows/ci.yml/badge.svg)](https://github.com/sjalloq/hpc-runner/actions/workflows/ci.yml)
[![Docs](https://github.com/sjalloq/hpc-runner/actions/workflows/docs.yml/badge.svg)](https://sjalloq.github.io/hpc-runner/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

> Run cluster jobs by tool flow; define its resources and environment once.

```bash
hpc run xrun -f top.f
```
hpc-runner turns this into the matching scheduler call, with the queue, CPUs,
memory, licences and Environment Modules that your project's config says an
Xcelium run needs.

## Why

- **No more "Works for me!&trade;".** Job settings and module versions live in a
  config file under version control. Every job runs `module purge` and loads
  only the modules that config names, so a flow that works for one user
  works for all of them.
- **No scheduler flags to learn.** Ask for `--cpu 8 --mem 16G`; hpc-runner
  writes the SGE or Slurm arguments for your site.
- **One interface for several schedulers.** The same command, config and
  Python API on SGE and Slurm.

## Who it is for

- People running *one-shot* jobs: one tool or flow on one host, across N CPUs, with dependencies.
- Teams that load their tools with [Environment Modules](https://modules.readthedocs.io/).
- Projects that want repeatable job environments across every user.

## Who it is not for

- Complex MPI or multi-node jobs.
- Sites that don't use Environment Modules.
- Users who want every scheduler feature as a first-class option.

## Highlights

- **Config by tool or job type.** Settings are keyed on the command's first
  word (`xrun`), or on a job type (`-t lint`) for makefiles and scripts.
- **Project config, local overrides.** `hpc-runner.toml` at the git root
  sets up the project; a `hpc-runner.toml` in any sub-directory allows for
  escape hatch overrides.
- **Raw scheduler flags when you need them:** `-S '-l gpu=1'`.
- **More than submission:** `hpc status` (active jobs, `--history`, one
  job), `hpc cancel`, and `hpc monitor`, a terminal UI for your jobs.
- **See before you submit:** `--dry-run` prints the job script;
  `hpc config resolve CMD` shows which config layer set each value.
- **Checked config:** unknown keys and wrong types are errors with the file,
  line and a "did you mean".
- **CLI or Python:** the same jobs from scripts, with dependencies, arrays
  and pipelines.
- **Local mode:** `HPC_SCHEDULER=local` runs a job on this machine with the
  same module environment.

## Install

```bash
pipx install hpc-runner     # or: uv tool install hpc-runner
hpc config init             # writes a starter hpc-runner.toml
```

Python 3.10 or later.

## Quick start

```bash
hpc run xterm                                  # interactive (default): hpc waits, output streams here
hpc run --batch -t lint make lint              # batch, using the "lint" job type
hpc run --batch --cpu 4 --mem 16G make sim     # resources for this run only
hpc run --dry-run make sim                     # print the job script, don't submit
hpc run -S '-l gpu=1' python train.py          # raw scheduler flags
hpc status --history --since 2h                # what ran recently
```

## Configuration

The project config, `hpc-runner.toml` at the git root, kept under version
control:

```toml
config_version = 1
scheduler = "sge"                       # or "slurm"; never guessed

[defaults]
cpu = 1
mem = "4G"
time = "1:00:00"
modules_path = ["/site/modulefiles"]

[schedulers.sge]
parallel_environment = "smp"
memory_resource = "mem_free"

[tools.xcelium]
commands = ["xrun", "xmvlog", "xmelab", "simvision"]
modules = ["xcelium/23.09"]
licenses = { xcelium_lic = 1 }
cpu = 4
mem = "16G"

[tools.slang]
modules = ["slang/0.9"]

[types.lint]
tools = ["xcelium", "slang"]            # both tools' modules and licences
cpu = 2
```

A `hpc-runner.toml` in a sub-directory changes tools and types for jobs run
from there:

```toml
config_version = 1

[tools.xcelium]
mem = "64G"                             # the big testbench in this directory
```

Settings layer from `[defaults]`, through the tool or type, the local file,
to the command line. See the
[configuration reference](https://sjalloq.github.io/hpc-runner/configuration.html).

## Schedulers

| Scheduler | Status |
|---|---|
| SGE / Open Grid Scheduler 8.1.9 | ✅ Supported |
| Slurm 22.05, 24.11 | ✅ Supported |
| Local (no scheduler) | ✅ Supported |
| PBS / Torque, LSF | Planned |

## Documentation

<https://sjalloq.github.io/hpc-runner/>

- [Getting started](https://sjalloq.github.io/hpc-runner/getting_started.html)
- [Configuration](https://sjalloq.github.io/hpc-runner/configuration.html)
- [CLI reference](https://sjalloq.github.io/hpc-runner/cli.html)
- [Python API](https://sjalloq.github.io/hpc-runner/programmatic_api.html)
- [SGE guide](https://sjalloq.github.io/hpc-runner/sge.html) ·
  [Slurm guide](https://sjalloq.github.io/hpc-runner/slurm.html)
- [Job monitor](https://sjalloq.github.io/hpc-runner/monitor.html)

## Contributing

Bug reports and ideas are welcome in
[Issues](https://github.com/sjalloq/hpc-runner/issues). Help testing on PBS
or LSF sites is especially useful. To build and test, see the
[development guide](https://sjalloq.github.io/hpc-runner/development.html).

## License

MIT. See [LICENSE](LICENSE).
