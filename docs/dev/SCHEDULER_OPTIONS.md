# Scheduler options

What hpc-runner does with each option, on each scheduler. This is the spec:
the code is built and tested against it.

- Each rule for a built scheduler is proven by
  `tests/unit/test_schedulers/test_option_table.py`, which reads the rules
  table below and states the exact flag for every cell.
- The Slurm columns are the spec for the Slurm backend. The same test
  covers them once it exists.
- The scheduler behaviour here was found by running each flag on the Docker
  testbeds (`testbed/`) on 2026-11-17.

## The rules

- **passes on**: the option reaches the scheduler, or the job.
- **refuses**: the job is not submitted; the error names the option.
- **ignores**: the job runs as if the option were not set.

| Option | SGE batch | SGE interactive | Slurm batch | Slurm interactive | local batch | local interactive |
|---|---|---|---|---|---|---|
| `name` | passes on | passes on | passes on | passes on | passes on | ignores |
| `cpu` | passes on | passes on | passes on | passes on | passes on | passes on |
| `mem` | passes on | passes on | passes on | passes on | ignores | ignores |
| `time` | passes on | passes on | passes on | passes on | ignores | ignores |
| `queue` | passes on | passes on | passes on | passes on | ignores | ignores |
| `project` | passes on | passes on | passes on | passes on | ignores | ignores |
| `stdout` | passes on | ignores | passes on | ignores | passes on | ignores |
| `stderr` | passes on | ignores | passes on | ignores | passes on | ignores |
| `inherit_env` | passes on | passes on | passes on | passes on | passes on | passes on |
| `env_passthrough` | passes on | passes on | passes on | passes on | passes on | passes on |
| `workdir` | passes on | passes on | passes on | passes on | passes on | passes on |
| `licenses` | passes on | passes on | passes on | passes on | ignores | ignores |
| `devices` | passes on | passes on | passes on | passes on | ignores | ignores |
| `features` | passes on | passes on | passes on | passes on | ignores | ignores |
| `array` | passes on | refuses | passes on | refuses | refuses | refuses |
| `dependencies` | passes on | refuses | passes on | refuses | refuses | refuses |

## hpc-runner's own rules

Where hpc-runner refuses or ignores an option by choice. Each is stated,
with its reason, in the user docs (`docs/source/configuration.rst`,
"Rules for every job").

| Rule | Why |
|---|---|
| An interactive job's `stdout` and `stderr` are ignored, on every scheduler | Its output is the terminal; naming a file for it is a user error |
| An interactive job cannot be an array | No scheduler's interactive command runs one |
| An interactive job cannot have dependencies | It runs now, at the terminal; a job that waits for another belongs in the queue. The interactive commands would accept one, and `srun` then waits for ever if the parent failed |
| local: `cpu` is given to the job as `${hpc.cpus}` and nothing holds the job to it | It is for the user's tools and flows to use the count they asked for |
| local: `mem`, `time`, `queue`, `project`, `licenses`, `devices`, `features` are ignored | local exists to give the modules and environment without a scheduler; nothing here reserves or limits |
| local: no arrays, no dependencies | There is no scheduler to fan out or hold a job |

## Flags

The same flag is used for a batch job (a directive line in the script) and
an interactive one (an argument to the command).

| Option | SGE (`qsub`, `qrsh`) | Slurm (`sbatch`, `srun`) |
|---|---|---|
| `name` | `-N` | `--job-name` |
| `cpu` | `-pe <pe> N` | `--cpus-per-task=N --ntasks=1` |
| `mem` | `-l <mem>=X` | `--mem` or `--mem-per-cpu` |
| `time` | `-l h_rt=` | `--time` |
| `queue` | `-q` | `--partition` |
| `project` | `-P` | `--account` |
| `stdout` | `-o` | `--output` |
| `stderr` | `-e`; without it `-j y` | `--error` |
| `inherit_env` | `-V` when true | `--export=ALL` or `--export=NONE` |
| `env_passthrough` | `-v NAME=value` | `--export=NAME=value,...` |
| `workdir` | `-cwd`, started in the directory | `--chdir` |
| `licenses` | `-l name=N` | `-L name:N` |
| `devices` | `-l name=N` | `--gres=name:N` |
| `features` | `-l name=1` | `-C name` |
| `array` | `-t`, limit `-tc` | `--array=...%N` |
| `dependencies` | `-hold_jid` | `--dependency` |

`<pe>` and `<mem>` are the site's names, from `[schedulers.sge]`.

local has no flags. `name`, `stdout` and `stderr` set where a batch job's
output is written; `cpu` is `${hpc.cpus}`; `inherit_env`, `env_passthrough`
and `workdir` are the environment and directory the process is started with.

## What the interactive command does differently

Every other flag above behaves the same on the batch and the interactive
command.

| Command | Flag | What happens |
|---|---|---|
| `qrsh` | `-o`, `-e`, `-j` | Accepted; no file is written |
| `qrsh` | `-t`, `-tc` | Rejected |
| `qrsh` | `-S` | Rejected |
| `qrsh` | `-hold_jid` | Honoured |
| `srun` | `--output`, `--error` | Fatal with `--pty`; without it the file is written on the submit host and the output leaves the terminal |
| `srun` | `--array` | Rejected |
| `srun` | `--kill-on-invalid-dep` | Rejected |
| `srun` | `--dependency` | Honoured; if the parent failed, `srun` waits for ever |
| `srun` | `--export=NONE`, `--export=NAME=...` | No login environment is built (`sbatch` builds one) |
