# Resource requests for one-shot jobs: SGE, Slurm, LSF, PBS

A fact base for D-S21 and open question Q2 (`DECISIONS.md`), researched on
2026-11-09 from primary sources by one sub-agent. It makes no decisions; open
choices are questions in section 8.

## 1. Sources and conventions

Each source was downloaded in this run; quoted text was read from the saved file.

| Scheduler | Version | Read from | Cited as |
|---|---|---|---|
| SGE | Son of Grid Engine 8.1.9, Debian trixie | manpages.debian.org `trixie/gridengine-client/sge_submit.1`; `gridengine-common/sge_complex.5`, `sge_queue_conf.5`, `sge_pe.5`, `sge_request.5`, `sge_conf.5`, `sge_types.5`, `load_parameters.5` | submit(1), complex(5), queue_conf(5), sge_pe(5), sge_types(5), load_parameters(5) |
| Slurm | 22.05.11 and 25.11.8 | SchedMD/slurm tags `slurm-22-05-11-1`, `slurm-25-11-8-1`: `sbatch.1`, `srun.1`, `slurm.conf.5`, `gres.conf.5`, `cgroup.conf.5`; `NEWS` (22.05); `CHANGELOG/` (25.11); `src/slurmctld/job_mgr.c`, `licenses.c`, `src/common/slurm_errno.c` | sbatch(1) = same text at both tags, else sbatch(1) 22.05 / 25.11; CL 23.11; src: `file:function` at the 25.11 tag |
| LSF | IBM Spectrum LSF 10.1.0 | ibm.com/docs `SSWRJV_10.1.0` content pages: `bsub` option pages (`-R -n -M -W -We -c -v -hl -q -P -sp -m -x -gpu -J`), `select_string`, `usage_string`, `span_string`, `res_req_strings_about`, `load_indices`, `lsf.shared`, `lsb.resources`, `LSF_UNIT_FOR_LIMITS`, `LSF_ENABLE_TMP_UNIT`, `LSF_STRICT_RESREQ`, `job_arrays_lsf` | page name, e.g. bsub -R, usage_string |
| PBS | PBS Professional 2021.1.2; OpenPBS master (man pages last changed 2021-01-18, commit 44d25d9) | Altair Reference Guide, Administrator's Guide and User's Guide PDFs via `pdftotext`; OpenPBS `doc/man1/qsub.1B`, `pbs_resources.7B` | RG-n, AG-n, UG-n (page labels); qsub.1B; pbs_resources.7B |
| hpc-runner | working tree at 1332179 | repo files | `path:line` |

Added to the brief's list: the PBS User's Guide, `cgroup.conf.5`,
`sge_types.5`, `load_parameters.5` and three Slurm source files.

**[DIFF §n]** is `SGE_SLURM_DIFFERENCES.md`; **[LPR n]** is item n of
Appendix A. A claim carrying only such a tag was not re-read
against the primary source in this run.

Not fetched (claims that depend on them are UNCONFIRMED, section 7):

- LSF `lsf.conf.lsb_gpu_new_syntax.5.html` and `lsf_admin/static_resources.html`:
  HTTP 503, twice.
- Ten LSF `lsf_admin` topic pages guessed by name (run-time, memory and CPU
  limits, exclusive jobs, reservation method): HTTP 404; the bsub option
  pages were read instead.
- Slurm `NEWS` at the 25.11 tag: HTTP 404 (that tag carries `CHANGELOG/`).

Examples are sketches of documented syntax; none is captured output.

## 2. Vocabulary

| Column | Terms |
|---|---|
| Value kind | **count** (whole number), **size** (bytes with a unit suffix), **duration**, **name** (identifier the site or scheduler defines), **boolean**, **string** (text matched against an attribute), **host list** |
| Scope | **per job** (one amount for the job), **per node** (on each node, or PBS chunk, the job uses), **per slot / per task** (multiplied by that count), **cluster-wide** (one pool for all nodes) |
| Semantics | **consumable** (subtracted from a total while the job runs), **limit** (usage above the value is stopped), **filter** (non-matching nodes are not used; nothing is subtracted), **hint** (ordering only; the job still runs when unmet) |
| Built in or site-defined | **built in**: the name exists on every installation; **site-defined**: the administrator creates it |

## 3. Resources

### R1. CPUs for one multi-threaded process

| | Option (example) | Value kind | Scope | Semantics | Built in or site-defined | Source |
|---|---|---|---|---|---|---|
| SGE | `-pe smp 8` | name + count (or range `n-m`) | per job, placed by the PE | consumable (slots) | PE name and its `allocation_rule` (`$pe_slots` = one host) site-defined | submit(1) `-pe`; sge_pe(5) `allocation_rule` |
| Slurm | `-c 8` (`--cpus-per-task`) | count | per task, "on the same node" | consumable | built in | sbatch(1) `-c` |
| LSF | `-n 8 -R "span[hosts=1]"` | count (or `min,max`) | `span[hosts=1]`: "all the processors allocated to this job must be on the same host" | consumable (slots) | built in | bsub -n; span_string |
| PBS | `-l select=1:ncpus=8` | count | per chunk; "All of a chunk must be taken from a single vnode" | consumable | built in | pbs_resources.7B; RG-272 |

Slurm 22.05 versus 25.11: only the 22.05 page says "srun will not inherit
the --cpus-per-task value requested by salloc or sbatch". CL 22.05: srun
"now will only inherit SRUN_CPUS_PER_TASK". CL 23.11: "Replace
SRUN_CPUS_PER_TASK with SLURM_CPUS_PER_TASK and get back the previous
behavior before Slurm 22.05".

**Consequence for a front end.** Three schedulers take a bare count; SGE
also needs a site PE name (`sge/args.py:151-169`). The `SRUN_CPUS_PER_TASK`
export in D-S19 matches 22.05; the changelog shows the need ends at 23.11.

### R2. Several tasks, possibly across nodes; node count

| | Option (example) | Value kind | Scope | Semantics | Built in or site-defined | Source |
|---|---|---|---|---|---|---|
| SGE | `-pe mpi 32` | name + count | per job; hosts by the PE's `allocation_rule` | consumable | PE site-defined; no node-count option in submit(1) | submit(1) `-pe`; sge_pe(5) |
| Slurm | `-n 32`, `-N 2`, `--ntasks-per-node=16` | counts (`-N min[-max]`) | per job; per node | consumable; "sbatch does not launch tasks" | built in | sbatch(1) `-n`, `-N`, `--ntasks-per-node` |
| LSF | `-n 32 -R "span[ptile=16]"` | count | per job; `ptile` = processors per host | consumable | built in; no node-count option on the pages read | bsub -n; span_string |
| PBS | `-l select=2:ncpus=16:mpiprocs=16` | count of chunks × per-chunk counts | per chunk | consumable (`ncpus`) | built in; `-l place=scatter` puts chunks on separate hosts | qsub.1B; pbs_resources.7B `mpiprocs` |

Slurm 22.05 versus 25.11: 25.11 `-N` also accepts a `size_string`
(`--nodes=1-15:4`).

**Consequence for a front end.** A node count is an option on Slurm, a chunk
multiplier on PBS, a per-host figure on LSF and a property of the PE on SGE.
hpc-runner's SGE backend does not map `nodes` or `tasks`
(`sge/scheduler.py:243-244`).

### R3. Exclusive use of a node

| | Option (example) | Value kind | Scope | Semantics | Built in or site-defined | Source |
|---|---|---|---|---|---|---|
| SGE | `-l exclusive=TRUE` (site's name) | boolean | per host or queue the complex is attached to | consumable boolean with relop `EXCL` | site-defined | complex(5) `relop` |
| Slurm | `--exclusive` | flag (`=user`, `=mcs`; 25.11 adds `=topo`) | per node of the allocation | all CPUs and GRES of each node; memory only as requested | built in; the partition's `OverSubscribe` "takes precedence" | sbatch(1) `--exclusive` |
| LSF | `-x` | flag | per host | host takes no other job; "the queue must be configured to allow exclusive jobs" | built in, queue-gated | bsub -x |
| PBS | `-l place=excl` or `place=exclhost` | keyword | `excl`: "Only this job uses the vnodes chosen"; `exclhost`: "The entire host is allocated to the job" | placement | built in; vnode `sharing` attributes can override | qsub.1B |

**Consequence for a front end.** Three schedulers have a built-in spelling
with no value; on SGE it is a site resource.

### R4. Memory request

| | Option (example) | Value kind | Scope | Semantics | Built in or site-defined | Source |
|---|---|---|---|---|---|---|
| SGE | `-l mem_free=8G` or `-l h_vmem=8G` | size; bare number = bytes; `K M G T` ×1024, `k m g t` ×1000 | `consumable YES`: per slot; `JOB`: per job; otherwise compared, not booked | `mem_free` is a load value ("amount of free real memory"); `h_vmem` is a limit (R5) | attribute choice and consumable flag are site configuration | complex(5) `consumable`; sge_types(5) `memory_specifier`; load_parameters(5) |
| Slurm | `--mem=8G`; `--mem-per-cpu=1G`; `--mem-per-gpu=4G` | size; "Default units are megabytes"; suffix `[K\|M\|G\|T]` | per node; per "usable allocated CPU"; per GPU; mutually exclusive | consumable when `SelectTypeParameters` includes memory [DIFF §4.2]; `--mem=0` = all memory of each node | built in | sbatch(1) `--mem`, `--mem-per-cpu` |
| LSF | `-R "rusage[mem=8G]"` | number, MB by default; suffixes `KB`..`ZB` | `/job`, `/host`, `/task` suffix, else `ReservationUsage`, else "by job if it is a shared resource or by host if it is a host-based resource" | reservation ("By default, no resources are reserved") | `mem` built in | bsub -R; usage_string; lsb.resources `ReservationUsage` |
| PBS | `-l select=1:ncpus=8:mem=8gb` | size; suffix `b kb mb gb tb pb` (and word forms); default bytes | per chunk; RG lists `mem` as "Either job-wide or host-level" | consumable | built in | RG-270; RG-262; pbs_resources.7B |

Slurm 22.05 versus 25.11: 25.11 adds that slurmstepd's own memory counts
toward the job, and rewrites the enforcement note (R5).

**Consequence for a front end.** The same number means per job, per node,
per slot or per chunk depending on scheduler and site. A bare number is
bytes (SGE, PBS) or megabytes (Slurm, LSF `rusage`).
hpc-runner parses a bare integer as bytes and suffixes as binary
(`core/units.py:42-67`). Slurm's `--mem-per-cpu` counts allocated CPUs, which
can exceed the CPUs asked for (sbatch(1), hyperthread example).

### R5. Memory limit as distinct from request

| | Option (example) | Value kind | Scope | Semantics | Built in or site-defined | Source |
|---|---|---|---|---|---|---|
| SGE | `-l h_vmem=8G` (`s_vmem` soft) | size | "For parallel processes, the limit is applied per slot" | limit on combined virtual memory; "aborted via a SIGKILL signal"; "only available on systems supporting RLIMIT_VMEM" | built in; consumable flag site-defined | queue_conf(5) RESOURCE LIMITS; complex(5) default host attributes |
| Slurm | none separate: `--mem` is both | size | as R4 | limit only if configured: `ConstrainRAMSpace` (cgroup OOM kills processes, step keeps running) or `OverMemoryKill` (whole step killed) | built in; enforcement site-defined | sbatch(1) 25.11 `--mem` NOTE; cgroup.conf(5); slurm.conf(5) `OverMemoryKill` |
| LSF | `-M 8G` (`-M 8G!` hard); `-v` for virtual memory | size; "By default, the limit is specified in KB" | per process by default; per job with `LSB_JOB_MEMLIMIT=y`; per host under cgroups with `-hl` | limit; separate from `rusage[mem]` | built in; enforcement mode site-defined | bsub -M; bsub -v; bsub -hl; LSB_JOB_MEMLIMIT |
| PBS | `mem` in the chunk is also the limit; `-l pmem=`, `-l pvmem=` job-wide; `vmem` in a chunk | size | `mem`, `vmem`: per chunk, summed per host; `pmem`, `pvmem`: per process | "By default, memory limits are not enforced"; `mem` needs `$enforce mem` (MoM polling); `pmem`, `pvmem` by `setrlimit()` | built in; enforcement site-defined | AG-308, AG-309; UG-62; RG-274..276 |

Slurm 22.05 versus 25.11: 22.05 says enforcement "relies upon the
task/cgroup plugin or enabling of accounting"; 25.11 names
`ConstrainRAMSpace` and `OverMemoryKill`.

**Consequence for a front end.** LSF has two options (reserve, limit); SGE
has two attributes behind one option; Slurm and PBS have one request that is
also the limit when the site enables enforcement. The repo's SGE capture
records `h_vmem` as a `setrlimit` (`failed 0`;
`tests/unit/test_schedulers/test_sge_fixtures.py:236-238`); queue_conf(5)
describes a SIGKILL.

### R6. Wall-clock limit

| | Option (example) | Value kind | Scope | Semantics | Built in or site-defined | Source |
|---|---|---|---|---|---|---|
| SGE | `-l h_rt=1:00:00` (`s_rt` soft) | duration: integer = seconds, or `h:m:s` | per job | limit; "aborted via the SIGKILL signal"; `s_rt` sends SIGUSR1 first | built in (default queue attribute) | queue_conf(5) RESOURCE LIMITS; sge_types(5) `time_specifier`; complex(5) |
| Slurm | `-t 1:00:00` | duration: "minutes", "minutes:seconds", "hours:minutes:seconds", and three "days-hours[:minutes[:seconds]]" forms | per job | limit; SIGTERM then SIGKILL after `KillWait` (default 30 s); `OverTimeLimit` can extend; above the partition limit the job stays PENDING | built in | sbatch(1) `-t`; slurm.conf(5) `KillWait`, `OverTimeLimit` |
| LSF | `-W 1:00` | duration: `[hour:]minute`; bare number = minutes | per job; normalised by host CPU factor unless `ABS_RUNLIMIT=Y` | limit; SIGUSR2, then killed "if it does not terminate within ten minutes"; `-We` is an estimate only | built in | bsub -W; bsub -We |
| PBS | `-l walltime=1:00:00` | duration: integer = seconds, or `[[HH:]MM:]SS[.ms]` | job-wide, outside `select` | limit; "If a running job exceeds its limit for walltime, the job is terminated"; default 5 years | built in | RG-261; RG-277; UG-62 |

**Consequence for a front end.** A bare integer is seconds on SGE and PBS
and minutes on Slurm and LSF; `1:30` is minutes:seconds on Slurm and PBS and
hours:minutes on LSF. hpc-runner refuses a bare integer
(`core/units.py:115-131`).

### R7. CPU-time limit

| | Option (example) | Value kind | Scope | Semantics | Built in or site-defined | Source |
|---|---|---|---|---|---|---|
| SGE | `-l h_cpu=2:00:00` (`s_cpu` soft) | duration | combined CPU time of the job; "the limit is applied per slot" | limit; SIGKILL (`s_cpu`: SIGXCPU) | built in | queue_conf(5) RESOURCE LIMITS |
| Slurm | none in sbatch(1); `--propagate=CPU` carries the submit shell's rlimit | n/a | per process (rlimit) | limit inherited, not requested | n/a | sbatch(1) `--propagate` |
| LSF | `-c 2:00` | duration `[hour:]minute`, normalised | whole job | limit; SIGXCPU, then SIGINT, SIGTERM, SIGKILL | built in | bsub -c |
| PBS | `-l cput=2:00:00`; `-l pcput=` | duration | `cput` job-wide (all processes); `pcput` per process | limit; job terminated | built in | RG-269; RG-274; AG-308 |

**Consequence for a front end.** Three schedulers accept a CPU-time limit at
submit; sbatch(1) has no such option.

### R8. Local scratch space

| | Option (example) | Value kind | Scope | Semantics | Built in or site-defined | Source |
|---|---|---|---|---|---|---|
| SGE | `-l tmp_free=20G` (the man page's example name) | size, if the complex is typed MEMORY | per host | load-sensor value, or a consumable if so defined | site-defined ("set up a load sensor to report the value"); the queue's `tmpdir` is built in but has no size | complex(5) "Working with resource attributes"; queue_conf(5) `tmpdir` |
| Slurm | `--tmp=20G` | size; "Default units are megabytes"; `[K\|M\|G\|T]` | per node | "minimum amount of temporary disk space per node"; `TmpDisk` is the "total file system size", not free space. Subtracted or not: UNCONFIRMED | built in | sbatch(1) `--tmp`; slurm.conf(5) `TmpDisk`, `TmpFS` |
| LSF | `-R "rusage[tmp=20000]"`; filter form `select[tmp>20000]` | number in MB; unit suffixes for `tmp` tied to `LSF_ENABLE_TMP_UNIT` | as R4 (`/job`, `/host`, `/task`, else configured) | reservation of the `tmp` load index (space on `/tmp`) | built in | load_indices; usage_string; LSF_ENABLE_TMP_UNIT; lsb.resources |
| PBS | `-l select=1:ncpus=1:dynscratch=10MB` (host-level) or `-l globalscratch=N` (job-wide) | size or long, as the site types it | per chunk or job-wide | consumable (static) or script-reported (dynamic) | site-defined custom resource | AG-272; AG-276; pbs_resources.7B "Custom Resources" |

**Consequence for a front end.** Scratch is a named option on Slurm and a
built-in index on LSF; on SGE and PBS the site names it. All four take a
size, which an integer-only map cannot carry.

### R9. GPUs

| | Option (example) | Value kind | Scope | Semantics | Built in or site-defined | Source |
|---|---|---|---|---|---|---|
| SGE | `-l gpu=2` | count | per slot (`YES`) or per job (`JOB`), by the complex's definition | consumable if defined so | site-defined; "gpu" does not occur in the SGE pages read | complex(5) `consumable` |
| Slurm | `--gres=gpu:2`, `--gres=gpu:a100:2`; `--gpus=2`; `--gpus-per-node=2`; `--gpus-per-task=1` | name[:type]:count | `--gres`, `--gpus-per-node`: per node; `--gpus`: per job; `--gpus-per-task`: per task | consumable | options built in; the GRES name must be in `GresTypes` ("No generic resources are managed by default") | sbatch(1); slurm.conf(5) `GresTypes`; gres.conf(5) `Name` |
| LSF | `-gpu "num=2"`, `-gpu "num=1/task:gmodel=K80"` | count plus keywords | "By default, the number is per host"; `/task` per task | consumable; "num=2 is converted to rusage[ngpus_physical=2]" | built in | bsub -gpu |
| PBS | `-l select=1:ncpus=4:ngpus=2` | count | per chunk | consumable (`flag=nh`) | site-defined custom resource (AG example: `create resource ngpus type=long, flag=nh`) | AG-286, AG-287 |

Slurm 22.05 versus 25.11: `--gres` reads "allocated to the job on each node"
in 22.05 and "requested per node" in 25.11; 25.11 `--gpus` adds "one of each
GPU type per node if types are used".

**Consequence for a front end.** A count is per node on Slurm `--gres`, per
host on LSF by default, per chunk on PBS and per slot or per job on SGE;
these agree for a single-node, single-chunk job with a per-job SGE complex.
A GPU type is part of the value on Slurm and LSF only.

### R10. Floating licences

| | Option (example) | Value kind | Scope | Semantics | Built in or site-defined | Source |
|---|---|---|---|---|---|---|
| SGE | `-l xcelium_lic=1` | count | cluster-wide when set in the `global` host's `complex_values` | consumable; per slot unless defined `JOB` | site-defined | complex(5) `consumable` |
| Slurm | `-L xcelium:1` | name[@db][:count], comma list | cluster-wide | consumable; "Slurm prevents jobs from being scheduled if their required license specification is not available" | option built in; names from `Licenses=` or slurmdbd | sbatch(1) `-L`; slurm.conf(5) `Licenses` |
| LSF | `-R "rusage[xcelium_lic=1]"` | count, optional `:duration=` | per job for a shared resource unless configured | consumable reservation | site-defined in `lsf.shared`; shared mapping in `lsf.cluster` [LPR 2] | bsub -R (verilog_lic example); lsb.resources |
| PBS | `-l xcelium_lic=1` outside `select` | count | job-wide | consumable: "A job requesting a floating license must specify it outside of a selection statement" | site-defined custom resource | pbs_resources.7B "Custom Resources" |

Slurm 22.05 versus 25.11: 25.11 adds OR between
licences (`foo:4|bar`); CL 25.05: "Implement OR ('|') for job license
requests".

**Consequence for a front end.** All four count licences against a
site-configured total under a site-chosen name. Only Slurm has a dedicated
option; elsewhere the name shares a namespace with other resources.

### R11. Queue or partition; project or account; priority

| | Option (example) | Value kind | Scope | Semantics | Built in or site-defined | Source |
|---|---|---|---|---|---|---|
| SGE | `-q short.q`; `-P proj`; `-p -100` | queue list; name; integer -1023..1024 | per job | `-q` "has all the properties of a resource request"; `-P` needs admin permission; "Users may only decrease the priority" | options built in; names site-defined | submit(1) `-q`, `-P`, `-p` |
| Slurm | `-p short`; `-A proj`; `--nice=100` | name list; name; integer | per job | earliest-starting partition of a list; nice default 100, range ±2147483645, "Only privileged users can specify a negative adjustment" | options built in | sbatch(1) `-p`, `-A`, `--nice` |
| LSF | `-q short`; `-P proj`; `-sp 50` | name list; name; integer 1..`MAX_USER_PRIORITY` | per job | first usable queue of the list; "The project does not have to exist before submitting the job"; out-of-range priorities "are rejected" | options built in | bsub -q, bsub -P, bsub -sp |
| PBS | `-q short`; `-P proj`; `-p 100` | name; name; integer -1024..+1023 | per job | default project `_pbs_project_default`; `-A` is a separate accounting string | options built in | qsub.1B `-q`, `-P`, `-p`, `-A` |

**Consequence for a front end.** Every scheduler has all three. Priority
ranges and who may raise it differ. A larger Slurm nice lowers priority; on
SGE a higher value is "dispatched earlier"; the LSF and PBS pages read do
not state the direction. LSF accepts an unknown project at submit.

### R12. Node features

| | Option (example) | Value kind | Scope | Semantics | Built in or site-defined | Source |
|---|---|---|---|---|---|---|
| SGE | `-l bigmem=TRUE`; `-l arch="lx-amd64"` | boolean ("the strings TRUE and FALSE are allowed"); string with wildcards and `! & \|` | per host or queue | filter | site-defined complex; some (`arch`, `hostname`, `qname`) built in | complex(5) `type`; sge_types(5) MATCHING TYPES |
| Slurm | `-C bigmem`; `-C "intel&gpu"` | feature name; expression `& \| [] *n` | per node | filter; "a node either has a feature or it does not" | option built in; names from node `Features` | sbatch(1) `-C`; slurm.conf(5) `Features` |
| LSF | `-R "select[bigmem]"`; `-R "select[type==X86_64]"` | boolean name; string or numeric comparison | per host | filter; with an "exclusive resource" the host takes only jobs that ask for it | site-defined in `lsf.shared` (default TYPE is Boolean); `type`, `model` and others built in | select_string; bsub -R; lsf.shared |
| PBS | `-l select=1:ncpus=1:bigmem=True`; `:arch=linux` | boolean (`TRUE True true T t Y y 1`); string | per chunk | filter; an unset Boolean "behaves as if that resource is set to false" | site-defined custom host-level resource; `arch` built in | RG-261; pbs_resources.7B "Boolean Resources" |

**Consequence for a front end.** On SGE and PBS a feature is `name=value`
through the same option as a consumable; on LSF and Slurm it is a bare name.
complex(5) lists `TRUE` and `FALSE` for BOOL; whether `-l feat=1` (D-S21's
table) is accepted is UNCONFIRMED.

### R13. Specific nodes; excluding nodes

| | Option (example) | Value kind | Scope | Semantics | Built in or site-defined | Source |
|---|---|---|---|---|---|---|
| SGE | `-l hostname=exec1`; `-q all.q@exec1`; exclusion `-l hostname="!exec1"` (inferred from the `arch="!lx-x86&!sol-amd64"` example) | host expression | per job | filter | built in (`hostname` is a default queue attribute; type HOST) | complex(5); sge_types(5) `wc_queue`, MATCHING TYPES |
| Slurm | `-w exec[1-2]`; `-x exec3` | host list, ranges, or file | per job | `-w`: the job "will contain all of these hosts and possibly additional hosts"; `-x`: excluded | built in | sbatch(1) `-w`, `-x` |
| LSF | `-m "exec1 exec2"`; exclusion `-R "select[hname!='exec3']"` | host or host-group list | per job | `-m`: candidates, one is chosen ("run on one of the candidate hosts") | built in | bsub -m; bsub -R |
| PBS | `-l select=1:ncpus=2:host=exec1` (or `vnode=`) | string | per chunk | filter | built in; no exclusion syntax found (searched "exclude") | UG-69; pbs_resources.7B `host`, `vnode` |

**Consequence for a front end.** A host name is a string value in the
general resource option on SGE and PBS and a dedicated option on Slurm and
LSF. A Slurm list means all of the hosts; an LSF list, any of them.

### R14. Soft versus hard requests

| | Option (example) | Value kind | Scope | Semantics | Built in or site-defined | Source |
|---|---|---|---|---|---|---|
| SGE | `-soft -l hostname=exec1`, `-hard` to switch back | toggle over following `-q` and `-l` | per request | hint: "nice-to-have, but not essential"; "requests for consumable resources cannot be soft" | built in | submit(1) `-soft`, `-hard` |
| Slurm | `--prefer=fast` | feature expression | per job | hint for features only: tried first, then `--constraint` | built in; present on the 22.05 page | sbatch(1) `--prefer` |
| LSF | `-m "exec1+2 others"`; `-R "order[...]"`; `rusage[a=1\|\|b=1]` | host with level; sort key; alternatives | per job | ordering of candidate hosts; alternatives tried in turn | built in | bsub -m; bsub -R |
| PBS | none found for one job (searched "soft") | n/a | n/a | n/a; UG-108 has "runone" sets of alternative jobs (beta) | n/a | UG-108; qsub.1B; pbs_resources.7B |

**Consequence for a front end.** A soft form exists for any non-consumable
`-l` request on SGE, for features on Slurm and for host order on LSF; none
was found for PBS.

### R15. Unknown resource name at submit

| | Option (example) | Value kind | Scope | Semantics | Built in or site-defined | Source |
|---|---|---|---|---|---|---|
| SGE | `-l nosuch=1` | n/a | n/a | rejected by qsub (integration test asserts stderr names it). A defined consumable with no `complex_values` is accepted and never scheduled. `-w` defaults to `n` | n/a | `tests/integration/test_submit.py:76-86`; `docs/dev/testbed/TESTBED_REVIEW.md:193-196, 340-342`; submit(1) `-w` |
| Slurm | `-L nosuch:1` | n/a | n/a | licence: rejected, "Invalid license specification" (name not found, or count above the total). GRES, feature: strings "Invalid generic resource (gres) specification", "Invalid feature specification" exist; path from an unknown name UNCONFIRMED | n/a | src: `job_mgr.c:_job_create`, `licenses.c:license_validate`, `slurm_errno.c` (same strings at the 22.05 tag) |
| LSF | `-R "rusage[nosuch=1]"` | n/a | n/a | UNCONFIRMED. Read: `defined()` accepts only names in `lsf.shared`; with `LSF_STRICT_RESREQ=Y` a non-consumable in `rusage` is rejected | n/a | select_string; LSF_STRICT_RESREQ; lsf.shared |
| PBS | `-l nosuch=1` | n/a | n/a | error codes `PBSE_UNKRESC 15035 Unknown resource`, `PBSE_INVALSELECTRESC 15108 resource invalid in select spec`; submit-time behaviour from forum threads only [LPR 10, secondary] | n/a | RG-392; RG-394 |

**Consequence for a front end.** SGE and Slurm (licences) reject an unknown
name at submit; on SGE a known name with no capacity is accepted and waits.

### R16. Arrays: per task or per array

| | Option (example) | Value kind | Scope | Semantics | Built in or site-defined | Source |
|---|---|---|---|---|---|---|
| SGE | `-t 1-10` | range | per task | "All array job tasks inherit the same resource requests and attribute definitions"; tasks "scheduled independently" | built in | submit(1) `-t` |
| Slurm | `-a 0-9%4` | index list | per task | "multiple jobs to be executed with identical parameters" | built in | sbatch(1) `-a` |
| LSF | `-J "arr[1-10]"` | index list | per element | "groups of jobs with the same executable and resource requirements" | built in | job_arrays_lsf |
| PBS | `-J 1-10` | range `X-Y[:Z]`, indices ≥ 0 | per subjob: UNCONFIRMED | subjobs "share a single job script, including the PBS directives" and are "scheduled and treated just like normal jobs"; no explicit resource statement found | built in | qsub.1B `-J`; UG-149 |

**Consequence for a front end.** Where the text is explicit (SGE, Slurm,
LSF) a request applies to each task; no page read describes a per-array
total.

### R17. Value grammar

| | Option (example) | Value kind | Scope | Semantics | Built in or site-defined | Source |
|---|---|---|---|---|---|---|
| SGE | `8G`, `1:0:0`, `TRUE`, `"lx*\|sol*"` | size: `K M G T` 1024-based, lower case 1000-based, bare = bytes. Duration: seconds or `h:m:s`. Boolean: `TRUE`/`FALSE`. String: wildcard expression | n/a | typed by the complex (`INT DOUBLE TIME MEMORY BOOL STRING CSTRING HOST`) | types built in | complex(5) `type`; sge_types(5) |
| Slurm | `8G`, `1-00:00:00`, `gpu:a100:2` | size: `[K\|M\|G\|T]`, bare = MB; base 1024 stated for GRES counts, not stated on `--mem` (UNCONFIRMED). Duration: R6. Count: integer. Lists: commas; feature operators `& \| [ ] *` | n/a | typed by option | built in | sbatch(1) `--mem`, `--gres`, `-t`, `-C` |
| LSF | `8G`, `1:30`, `select[hname!='host06-x12']` | size: bare = MB in `rusage`/`select`, KB for `-M`, rescaled by `LSF_UNIT_FOR_LIMITS`; suffixes `KB`..`ZB`; example converts 1TB to 1024 GB. Duration: `[hour:]minute`. Boolean: bare name. Non-alphabetic strings in single quotes | n/a | typed by section; sections separated by a space | built in | bsub -R; LSF_UNIT_FOR_LIMITS; bsub -W |
| PBS | `8gb`, `1:00:00`, `True`, `host=exec1` | size: `b kb mb gb tb pb` 1024-based, bare = bytes. Duration: seconds or `[[HH:]MM:]SS`. Boolean: `True`, `1` and others. String: "Any character, including the space character". Chunks joined by `:` and `+` | n/a | typed by the resource's definition (`long size float string string_array boolean`) | types built in | RG-261, RG-262; pbs_resources.7B |

**Consequence for a front end.** `20G` is valid as typed on SGE and Slurm;
PBS wants `20gb`; LSF takes `20G` or `20GB` where units are enabled. Every
scheduler has resource values that are not integers (sizes, strings,
booleans). hpc-runner refuses whitespace in a rendered SGE value
(`sge/scheduler.py:431-459`).

## 4. Summary matrix

| Resource | SGE | Slurm | LSF | PBS | hpc-runner today |
|---|---|---|---|---|---|
| R1 CPUs, one process | `-pe <pe> N` | `-c N` | `-n N -R "span[hosts=1]"` | `select=1:ncpus=N` | `cpu` |
| R2 tasks / nodes | `-pe <pe> N` / none | `-n` / `-N` | `-n` / `span[ptile=]` | `select=N:...:mpiprocs=` | `tasks`, `nodes` (unused on SGE) |
| R3 exclusive | site `-l` | `--exclusive` | `-x` | `place=excl` | not modelled |
| R4 memory request | `-l <site attr>=` | `--mem`, `--mem-per-cpu` | `rusage[mem=]` | `select=...:mem=` | `mem` |
| R5 memory limit | `-l h_vmem=` | none separate | `-M`, `-v` | `mem`, `pmem`, `pvmem`, `vmem` | `mem` when `memory_resource = "h_vmem"` (`sge/args.py:176-205`) |
| R6 wall clock | `-l h_rt=` | `-t` | `-W` | `-l walltime=` | `time` |
| R7 CPU time | `-l h_cpu=` | none | `-c` | `-l cput=` | not modelled |
| R8 scratch | site `-l` | `--tmp` | `rusage[tmp=]` | site custom | `resources` |
| R9 GPUs | site `-l` | `--gres`, `--gpus*` | `-gpu` | site `select=...:ngpus=` | `resources` |
| R10 licences | site `-l` | `-L` | `rusage[name=]` | `-l name=` | `resources` |
| R11 queue | `-q` | `-p` | `-q` | `-q` | `queue` |
| R11 project | `-P` | `-A` | `-P` | `-P` | `project` |
| R11 priority | `-p` | `--nice` | `-sp` | `-p` | `priority` |
| R12 features | `-l name=TRUE` | `-C` | `select[name]` | `select=...:name=True` | `resources` |
| R13 nodes | `-l hostname=` | `-w`, `-x` | `-m` | `select=...:host=` | `resources` |
| R14 soft | `-soft` | `--prefer` | `-m host+`, `order[]` | none | not modelled |
| R15 unknown name | rejected | licence rejected; others UNCONFIRMED | UNCONFIRMED | error codes only | n/a |
| R16 arrays | per task | per task | per element | UNCONFIRMED | n/a |
| R17 grammar | typed by complex | typed by option | typed by section | typed by resource | `mem`, `time` parsed; `resources` values passed through |

Keys: `src/hpc_runner/core/spec.py:40-48` (`cpu` to `tasks`), `:71`
(`resources`), `:75` (`scheduler_args`); `resources` rendering:
`schedulers/sge/scheduler.py:419-421`.

## 5. Classification

**Same meaning on all four** (spelling differs):

- R1 CPUs for one process (SGE also needs a site PE).
- R6 wall-clock limit (grammar differs).
- R10 floating licences (counted, cluster-wide, site-named).
- R11 queue, project, priority (priority ranges differ).
- R12 node features (a filter on a site-defined attribute).
- R16 arrays (per task; PBS UNCONFIRMED).

**Present on some, or differing in scope, with the gaps named:**

- R2 tasks and nodes: no node-count option on SGE or LSF.
- R3 exclusive node: site-defined on SGE.
- R4 memory request: per node (Slurm), per chunk (PBS), per job or host
  (LSF), per slot or job (SGE).
- R5 memory limit: no separate option on Slurm; enforcement depends on
  site configuration on Slurm, LSF and PBS.
- R7 CPU-time limit: no sbatch option.
- R8 scratch: built in on Slurm and LSF; site-defined on SGE and PBS.
- R9 GPUs: built in on Slurm and LSF; site-defined on SGE and PBS; type in
  the request on Slurm and LSF only.
- R13 nodes: no exclusion syntax found for PBS.
- R14 soft requests: none found for PBS; features only on Slurm.
- R15 unknown names: outcome differs or is UNCONFIRMED.
- R17 grammar: no two schedulers share a size and duration grammar.

**One scheduler only:** none of R1 to R17. Sub-features seen on one
scheduler only: Slurm `--mem-per-gpu`; LSF `rusage` `duration`/`decay`; SGE
`-soft` on any non-consumable request; PBS word-size suffixes (`kw`).

## 6. Today's uses mapped

Where each entry appears: `xcelium_lic=1` (`tests/unit/test_golden.py:100`,
`tests/unit/test_cli/test_run.py:569`, `docs/source/cli.rst:105`); `gpu=1`
(`tests/conftest.py:136`, `src/hpc_runner/cli/config.py:180`,
`docs/source/sge.rst:139-141`); `scratch=20G` (`tests/unit/test_golden.py:100`);
`tmpfs=8G` (`docs/source/configuration.rst:860`); `h_vmem=50M`
(`tests/integration/test_resources.py:155`); `hostname=exec1`
(`tests/integration/test_failures.py:52`, `test_resources.py:122`).

| Entry | SGE | Slurm | LSF | PBS |
|---|---|---|---|---|
| `xcelium_lic=1` | `-l xcelium_lic=1` | `-L xcelium_lic:1` | `-R "rusage[xcelium_lic=1]"` | `-l xcelium_lic=1` (outside `select`) |
| `gpu=1` | `-l gpu=1` | `--gres=gpu:1` (per node) or `--gpus=1` (per job) | `-gpu "num=1"` (per host) | `-l select=1:ngpus=1` (site name) |
| `scratch=20G` | `-l scratch=20G` (site complex) | `--tmp=20G` | `-R "rusage[tmp=20480]"` (MB; suffix form depends on `LSF_ENABLE_TMP_UNIT`) | `-l select=1:<site name>=20gb` |
| `tmpfs=8G` | `-l tmpfs=8G` (site complex) | no equivalent found | no equivalent found | site custom resource |
| `h_vmem=50M` | `-l h_vmem=50M` | no equivalent option; `--mem` enforcement is RSS-based (sbatch(1) 22.05) | `-v 51200` (virtual, whole job, KB) or `-M 50M` | `-l select=1:vmem=50mb` or `-l pvmem=50mb` |
| `hostname=exec1` | `-l hostname=exec1` | `-w exec1` | `-m exec1` | `-l select=1:host=exec1` |

The repo does not say what `tmpfs` measures; that row maps the name only.

## 7. UNCONFIRMED

1. **Slurm, unknown GRES or feature name at submit** (R15):
   the error strings are in `slurm_errno.c`; the path from an unknown
   name to a submit-time rejection was not followed through `gres.c` and
   `node_scheduler.c`.
2. **LSF, unknown resource name in `rusage[]` or `select[]`** (R15): the
   saved LSF pages were searched for "unknown resource", "undefined
   resource", "Bad resource"; no hit.
3. **PBS, what qsub prints for an unknown or misplaced resource** (R15):
   only the error-code table (RG-392, RG-394) was found; AG and UG were
   searched for "Unknown resource".
4. **PBS, per-subjob application of a resource request** (R16): UG-149 and
   RG-9 were read; neither states it outright.
5. **Slurm `--tmp`: filter or consumable** (R8): sbatch(1) and slurm.conf(5)
   `TmpDisk` give the comparison only.
6. **Slurm `--mem` suffix base** (R17): the page gives `[K|M|G|T]` without
   a base.
7. **SGE BOOL request spelled `=1`** (R12): complex(5) names `TRUE`, `FALSE`.
8. **SGE host exclusion `-l hostname="!exec1"`** (R13): inferred from the
   `arch` example; no hostname example was found.
9. **LSF `tmp` with a unit suffix when `LSF_ENABLE_TMP_UNIT=N`** (R8, R17):
   bsub -R allows units for `tmp` and also says `tmp` is outside
   `LSF_UNIT_FOR_LIMITS`.
10. **LSF `LSB_GPU_NEW_SYNTAX` default and the `-R` versus `-gpu`
    precedence** (R9): page returned HTTP 503; the statements in [LPR 3]
    were not re-read.
11. **LSF and PBS priority direction** (R11): bsub -sp and qsub.1B `-p` do
    not say whether a larger value runs earlier.
12. **LSF `-M` default unit**: bsub -M says KB; the `LSF_UNIT_FOR_LIMITS`
    page lists KB and its example says "the default 100 MB".
13. **PBS `mem` outside `select`**: RG-270 says both "Either job-wide or
    host-level" and "Can be requested only inside of a select statement";
    pbs_resources.7B converts `qsub -lmem=4gb` to a chunk.
14. **Live licence feeds** (R10): not researched; see [DIFF §4.3], [LPR 7].
15. **OCS**: not read (Son of Grid Engine only).

## 8. Questions for the main session

1. D-S21 states devices are "per job (LSF)". bsub -gpu reads "By default,
   the number is per host", with `/task` as the alternative. Is the D-S21
   sentence to be corrected?
2. Q2: `scratch=20G`, `tmpfs=8G`, `h_vmem=50M` are sizes and
   `hostname=exec1` is a host name; Slurm has an option for three of them
   (`--tmp`, `--mem`, `-w`). Are they carried by the D-S21 maps with
   non-integer values, by new keys, or by `scheduler_args`?
3. R8: scratch is built in on Slurm and LSF and site-defined on SGE and
   PBS. Is it a fourth kind beside licence, device and feature, a device
   with a size value, or out of scope?
4. R13: is a specific-host request part of the model (two integration
   tests use one), or a raw argument?
5. R12: D-S21 renders an SGE feature as `-l feat=1`; complex(5) documents
   `TRUE`/`FALSE`. Which is rendered, and is a capture of `-l <bool>=1`
   wanted first?
6. R9: Slurm offers `--gres=gpu:N` (per node) and `--gpus=N` (per job).
   D-S21 names `--gres`. Is that the intended one for multi-node jobs?
7. R9, R10: a GPU type (`gpu:a100:2`, `gmodel=`) and a licence server
   (`name@db`) are part of the value. In scope for the maps?
8. R15: is a testbed capture wanted for an unknown GRES, feature and
   licence name on Slurm, to settle UNCONFIRMED item 1?
9. R5: is a capture of a job exceeding `--mem` under `ConstrainRAMSpace`
   wanted before Slurm status mapping is fixed?
10. R4: `--mem-per-cpu` counts allocated CPUs, which can exceed `cpu`. Does
    D-S20's `memory_per = "cpu"` division need a note or a guard?
11. R1: CL 23.11 restores srun's use of `SLURM_CPUS_PER_TASK`. Is the
    D-S19 export of `SRUN_CPUS_PER_TASK` kept for all versions?
12. R3, R7, R14: exclusive nodes, CPU-time limits and soft requests are not
    modelled today. In scope, or left to `scheduler_args`?
13. Do the LSF and PBS items in section 7 (2, 3, 4, 9 to 13) need closing
    now, or are they parked?

## Appendix A. LSF and PBS: the earlier research (2026-11-04)

A factual record of how IBM Spectrum LSF and PBS Professional / OpenPBS take
resource requests (licences, devices, node features, memory, cores), gathered
on 2026-11-04 by a research sub-agent for D-S21 in `DECISIONS.md`, before the
sections above were written and not revised since. Like
`SGE_SLURM_DIFFERENCES.md` it records behaviour and its source and makes no
decisions. Every claim cites a documentation page or guide section; where
the primary text was not found the item says **UNCONFIRMED**.

The Summary table at the end is the four-scheduler comparison D-S21
condenses; this file keeps the "Must know kind?" column and the per-item
detail the decision log leaves out.

Sources: IBM Spectrum LSF 10.1.0 documentation (ibm.com/docs/en/SSWRJV_10.1.0,
cited by page name); Altair PBS Professional 2021.1.2 Reference Guide (RG)
and Administrator's Guide (AG), cited by section and page; OpenPBS
`doc/man1/qsub.1B` (github.com/openpbs/openpbs). Community forum threads are
marked secondary. The SGE and Slurm rows in the Summary table restate the
main session's own research; `SGE_SLURM_DIFFERENCES.md` §4 is the record for
those two.

### LSF (bsub)

**1. `-R` sections.** `select[]` = host filter ("specifies the criteria for selecting execution hosts"); `rusage[]` = "expected resource consumption of the task", used for reservation; `order`, `span` (locality: `span[hosts=1]` "all the processors allocated to this job must be on the same host", `span[ptile=n]`), `same`, `cu`, `affinity` (CPU/memory binding) [bsub -R: lsf_command_ref/bsub.__r.1.html; res_req_strings_about.html; span: ?topic=strings-span-string].
- (i) Consumable/licence: `rusage[]`. Doc examples: `bsub -R "rusage[mem=20, license=1:duration=2]" myjob`; `bsub -R "select[defined(verilog_lic)] rusage[verilog_lic=1]" myjob`; floating-licence best practice: `bsub -R "rusage[lic1=1]" job_script` [bsub.__r.1.html; best_practices/floating_software_licenses.html]. So "1 xcelium licence" = `-R "rusage[xcelium_lic=1]"`.
- (ii) Node attribute: `select[]`. Boolean resource "is 1 if defined on a host and 0 if not": `bsub -R "bigmem"` or `bsub -R "select[bigmem]"` (`defined(bigmem)` also shown) [bsub.__r.1.html; ?topic=strings-selection-string]. Strings: `select[type==X86_64 && tmp>10000]`, shared strings quoted `os_version=='4.2'`.
- (iii) GPUs: see 3.

**2. Licence definition.** Resources are declared in `lsf.shared` Resource section with TYPE Numeric/Boolean/String and CONSUMABLE; "Static and dynamic numeric resources can be specified as consumable"; by default external shared numeric resources are consumable, String/Boolean are not; "There is no distinction between shared and non-shared resources" in lsf.shared (that is lsf.cluster ResourceMap, e.g. `lic1 (10@[all])`) [lsf_admin/resource_section_configure.html; floating_software_licenses.html]. `rusage` is numeric-consumable only: "LSF rejects resource requirement strings where an rusage section contains a non-consumable resource" [?topic=strings-usage-string]. A user can see kind via `lsinfo -l <name>` (TYPE, CONSUMABLE columns) [resource_section_configure.html]. License Scheduler: features are still requested in rusage, `bsub -Lp Lp1 -R "rusage[AppZ201=2]" myjob`; `-Lp` "Assigns the job to the specified LSF License Scheduler project" [lsf_config_ref/lsf.licensescheduler.5.html; bsub.__lp.1.html]. Unknown name: UNCONFIRMED. Docs say `defined()` accepts only names configured in lsf.shared and that under `LSF_STRICT_RESREQ=Y` malformed select strings are rejected at bsub; `BSUB_CHK_RESREQ` checks "only the select section" without submitting [selection-string page; lsf_envars_ref.html]. No primary text found for the exact message or pend-vs-reject behaviour of an undefined rusage name.

**3. GPUs.** Current: `bsub -gpu "num=2:mode=exclusive_process:mps=yes"`; default `num=1:mode=shared:mps=no:j_exclusive=no`; "num=2 becomes rusage[ngpus_physical=2]"; mode/j_exclusive/mps "are not supported in the bsub -R syntax" [bsub.gpu.1.html; lsf.conf.lsb_gpu_new_syntax.5.html, default `extend`]. `-R "rusage[ngpus_physical=2:gmodel=K80]"` remains for compound/alternative requests; when `-R` names ngpus_physical "LSF ignores the -gpu option"; mixing other GPU keys across both forms rejects the job [bsub.__r.1.html; lsf_gpu/lsf_gpu_example_jobs.html]. Legacy `ngpus_shared/ngpus_excl_*` cannot be used when the new syntax is on.

**4. Memory/cores.** `-n min_tasks[,max_tasks]`; slots usually equal tasks [bsub.n.1.html]. `-M mem_limit[!]`: "By default, LSF sets a per-process (soft) memory limit for all the processes that belong to the job"; `LSB_JOB_MEMLIMIT=y` makes it a per-job limit enforced by LSF (sum over processes); `!` = hard limit [bsub.__m.1.html; lsf.conf.lsb_job_memlimit.5.html]. Units: `-M` default KB, rusage mem/swp/tmp default MB, both scaled by `LSF_UNIT_FOR_LIMITS` ("Set to MB at time of installation") [lsf.conf.lsf_unit_for_limits.5.html]. `rusage[mem=N/task|/job|/host]` sets reservation method, overriding lsb.resources ReservationUsage (PER_JOB/PER_HOST/PER_TASK; PER_SLOT obsolete) [usage-string page; ?topic=reservationusage-method]. Default method when unconfigured: UNCONFIRMED for 10.1. `affinity[core(5)]` = 5 cores per task [?topic=strings-affinity-string].

**5. One door?** Neither: one option (`-R`) but typed by section (select = attributes, rusage = numeric consumables), plus separate typed options (`-gpu`, `-M`, `-n`, `-Lp`). The section choice encodes the kind.

### PBS Pro / OpenPBS (qsub)

**6. Chunk vs job-wide.** "Resources are requested by using the -l option, either in job-wide requests using <resource name>=<value> pairs, or in chunks inside of selection statements"; `-l select=[N:]<chunk>[+...]`, `-l place=...` [qsub.1B; RG 2.59.2.6, RG-217]. Scope column: "Job-wide and can be requested only outside of a select statement" / "Host-level and can be requested only inside of a select statement" [RG 5.5, RG-266]. Built-ins: ncpus, vmem Host-level consumable; arch Host-level string; walltime, cput, pmem, pvmem, nodect Job-wide; mem "Either job-wide or host-level" [RG ch.5 tables]. Custom: `qmgr -c "create resource <name> type=<long|size|float|string|string_array|boolean> flag=<flags>"`; flags: none = server/queue, non-consumable; `h` = host-level, "Required for any resource that will be used inside a select statement"; `nh` = host-level consumable; `fh` = consumable on first vnode only; `q` = consumable at queue and server [RG 2.47.7.4, Table 2-18/2-19, RG-160..162]. `resourcedef` file is legacy; qmgr is the documented path.

**7. Licences.** No built-in licence concept; the Glossary's example of a job-wide resource is a custom `FloatingLicenses` set via `resources_available` [RG-10]. Static: `create resource sitelicense type=long, flag=q`, `set server resources_available.sitelicense=N`, add to `resources:` line in sched_config; dynamic (external licence manager): `create resource floatlicense type=long` (no flags) plus `server_dyn_res: "floatlicense !/usr/local/bin/serverdyn.pl"`; request `-l floatlicense=<n>` (plus a Boolean chunk resource for eligible hosts) [AG 5.14.3, AG-269]. "A scheduler honors all resources listed in the resources: line ... If this line is not present, a scheduler honors all resources"; listing is "required only if the resource is to be specifically requested by jobs" [AG 4.5.3; AG-175].

**8. GPUs.** `ngpus` is not in the built-in table; AG: "We recommend that the custom GPU resource is named ngpus", `create resource ngpus type=long, flag=nh`, set `resources_available.ngpus` per node, add to `resources:`; "used exactly the way you use the ncpus resource" [AG 5.14.7.1]. Request: `-l select=1:ngpus=2`.

**9. Node attributes.** Chunk-level Boolean/string resources use the same `name=value` syntax inside select: `qsub -lselect=1:arch=windows`, `-l select=runsAppF=True` (`create resource CanRun type=boolean, flag=h`) [AG-269, AG 30851; RG arch row]. Syntactically indistinguishable from consumables; only the server's flag differs.

**10. Unknown names.** Server error codes: `PBSE_UNKRESC 15035 Unknown resource`; `PBSE_INVALSELECTRESC 15108 resource invalid in select spec` [RG error table]. Forum reports show qsub rejecting with "unknown resource ngpus" and "qsub: Resource invalid in "select" specification: <name>" when the resource lacks the `h` flag (community.openpbs.org threads 2604, 3360; secondary). So the submitter must put host-level (`h`) resources inside select and job-wide ones outside; the server enforces this.

**11. Memory.** Chunk `mem` applies to "the part of the job running on that chunk"; `-l select=6:ncpus=2:mem=4gb` = 4 GB per chunk; job-wide limit is "the smaller of a job-wide resource request and the sum of that for all chunks" [AG 5.4.9.2, AG-238; AG-15022]. Size units: `b/w, kb/kw, mb/mw, gb/gw, tb/tw, pb/pw`, default bytes, rounded up to kb [RG Table 5-1, RG-261]. `pmem`/`pvmem` = per-process, job-wide; `vmem` = per-chunk host-level consumable [RG ch.5].

### Summary

| | Floating licence | Per-node device count | Node feature | Must know kind? |
|---|---|---|---|---|
| SGE | `-l lic=1` | `-l gpu=2` | `-l feat=1` | No (one door; complex(5) decides) |
| Slurm | `-L lic:1` | `--gres=gpu:2` | `-C feat` | Yes (three options) [sbatch.html] |
| LSF | `-R "rusage[lic=1]"` | `-gpu "num=2"` (or `rusage[ngpus_physical=2]`) | `-R "select[feat]"` | Yes (section/option encodes it; rusage rejects non-consumables) |
| PBS | `-l lic=1` (job-wide) | `-l select=1:ngpus=2` | `-l select=1:feat=True` | Yes (chunk vs job-wide, server rejects misplacement) |
