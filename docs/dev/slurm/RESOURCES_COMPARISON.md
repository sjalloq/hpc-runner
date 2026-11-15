# Resource requests for one-shot jobs: SGE, Slurm, LSF, PBS

A fact base for the licence, device and feature maps and the open questions on them (`PLAN.md`), researched on
2026-11-09 from primary sources by one sub-agent (**UNVERIFIED**: no source
records who did the research; the saved files' dates match) and re-checked
on 2026-11-13. It makes no decisions; open choices are questions in
section 8.

## 1. Sources and conventions

Each source was downloaded in this run; quoted text was read from the saved file.

- Re-check: every claim was checked again against primary sources on
  2026-11-13 by independent checkers (findings:
  `hpc-runner-sources/validation-2026-11-13/res-F.md` to `res-I.md`).
  Corrections are made in place; what could not be confirmed is marked
  **UNVERIFIED** in place and listed in section 7.

| Scheduler | Version | Read from | Cited as |
|---|---|---|---|
| SGE | Son of Grid Engine 8.1.9, Debian trixie (packages `8.1.9+dfsg-13.1`; the man pages' own header reads `SGE 8.1.3pre`) | manpages.debian.org `trixie/gridengine-client/sge_submit.1`; `gridengine-common/sge_complex.5`, `sge_queue_conf.5`, `sge_pe.5`, `sge_request.5`, `sge_conf.5`, `sge_types.5`, `load_parameters.5` (`sge_host_conf.5` was also saved) | submit(1), complex(5), queue_conf(5), sge_pe(5), sge_types(5), load_parameters(5) |
| Slurm | 22.05.11 and 25.11.8 | SchedMD/slurm tags `slurm-22-05-11-1`, `slurm-25-11-8-1`: `sbatch.1`, `srun.1`, `slurm.conf.5`, `gres.conf.5`, `cgroup.conf.5`; `NEWS` (22.05); `CHANGELOG/` (25.11); `src/slurmctld/job_mgr.c`, `licenses.c`, `src/common/slurm_errno.c` | sbatch(1) = same text at both tags, else sbatch(1) 22.05 / 25.11; CL 23.11; src: `file:function` at the 25.11 tag |
| LSF | IBM Spectrum LSF 10.1.0 | ibm.com/docs `SSWRJV_10.1.0` content pages: `bsub` option pages (`-R -n -M -W -We -c -v -hl -q -P -sp -m -x -gpu -J`), `select_string`, `usage_string`, `span_string`, `res_req_strings_about`, `load_indices`, `lsf.shared`, `lsb.resources`, `LSF_UNIT_FOR_LIMITS`, `LSF_ENABLE_TMP_UNIT`, `LSF_STRICT_RESREQ`, `LSB_JOB_MEMLIMIT`, `job_arrays_lsf` (`lsb.queues` was also saved) | page name, e.g. bsub -R, usage_string |
| PBS | PBS Professional 2021.1.2; OpenPBS master (`pbs_resources.7B` last changed 2021-01-18, commit 44d25d9; `qsub.1B` last changed 2022-02-15, commit 81791fd; the saved copies were byte-identical to master on 2026-11-13) | Altair Reference Guide, Administrator's Guide and User's Guide PDFs via `pdftotext`; OpenPBS `doc/man1/qsub.1B`, `pbs_resources.7B` (`pbs_job_attributes.7B` was also saved) | RG-n, AG-n, UG-n (page labels); qsub.1B; pbs_resources.7B; pbs_job_attributes.7B |
| hpc-runner | working tree at 1332179 (re-checked at cf0b1de; the cited line ranges still hold, one corrected in R15) | repo files | `path:line` |
| Added by the re-check (2026-11-13) | Son of Grid Engine 8.1.9 source; `daimh/sge` master (a fork of Son of Grid Engine); OpenPBS tags `v20.0.1` and `v23.06.06`; Slurm `src/` at both tags; further LSF 10.1.0 pages | Debian `gridengine_8.1.9+dfsg.orig.tar.gz` and sources.debian.org; raw.githubusercontent.com/daimh/sge; github.com/openpbs/openpbs; SchedMD/slurm tags as above; ibm.com/docs API `SSWRJV_10.1.0` | src with the version or fork named; "(read from source, not run)" where nothing was executed |

Added to the brief's list: the PBS User's Guide, `cgroup.conf.5`,
`sge_types.5`, `load_parameters.5` and three Slurm source files
(**UNVERIFIED**: the brief is not among the saved sources; the files are).

**[DIFF §n]** is `SGE_SLURM_DIFFERENCES.md`; **[LPR n]** is item n of
Appendix A. A claim carrying only such a tag was not re-read
against the primary source on 2026-11-09. The re-check read the primary
source for those in R4, R9, R10 and R15; it is cited beside the tag.

Not fetched on 2026-11-09:

- LSF `lsf.conf.lsb_gpu_new_syntax.5.html` and `lsf_admin/static_resources.html`:
  HTTP 503, twice (**UNVERIFIED**: a past status cannot be re-observed;
  neither page is among the files saved that day). Both were fetched and
  saved on 2026-11-13 (HTTP 200 for one checker; two others got HTTP 403
  from the same host that day).
- Ten LSF `lsf_admin` topic pages guessed by name (run-time, memory and CPU
  limits, exclusive jobs, reservation method): HTTP 404 (**UNVERIFIED**:
  the ten names were not recorded); the bsub option pages were read instead.
  The topics exist under other names, found through the product table of
  contents and fetched on 2026-11-13:
  `lsf_resource_sharing/runtime_limit.html`, `memory_limit.html`,
  `memory_limit_enforce.html`, `os_memory_limit_enforce.html`,
  `cpu_time_limit.html`, `swap_usage_limit.html`,
  `lsf_admin/exclusive_scheduling_lsf.html`, `queue_excl_config_lsf.html`,
  `job_excl_submit.html`, `resource_res_about.html`,
  `resource_res_config.html`, `resource_res_job_specify.html`,
  `res_limits_units.html`, `about_resource_usage_limits.html`,
  `cgroup_mem_enforce9111.html`, and the job-priority pages. They were
  searched for the section 7 items only; R1 to R17 were not re-read
  against them.
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
| Slurm | `-c 8` (`--cpus-per-task`) | count | per task, "on the same node" | consumable when the select plugin counts CPUs (slurm.conf(5) `CR_CPU`: "CPUs are consumable resources"); the sbatch(1) text does not use the word | built in | sbatch(1) `-c`; slurm.conf(5) `SelectTypeParameters` |
| LSF | `-n 8 -R "span[hosts=1]"` | count (or `min,max`) | `span[hosts=1]`: "all the processors allocated to this job must be on the same host" | slots are allocated ("The number of tasks is used to allocate the number of slots for a job"); no page read uses "consumable" for slots | built in | bsub -n; span_string |
| PBS | `-l select=1:ncpus=8` | count | per chunk; "All of a chunk must be taken from a single vnode" | consumable | built in | pbs_resources.7B; RG-272 |

Slurm 22.05 versus 25.11: of the two tags compared, only the 22.05 page
says "srun will not inherit the --cpus-per-task value requested by salloc or
sbatch" (the NOTE is still in sbatch.1 at `slurm-23-02-7-1` and
`slurm-23-11-0-1` and is gone at `slurm-23-11-11-1`). CL 22.05: srun
"now will only inherit SRUN_CPUS_PER_TASK". CL 23.11: "Replace
SRUN_CPUS_PER_TASK with SLURM_CPUS_PER_TASK and get back the previous
behavior before Slurm 22.05". srun's option table reads `SRUN_CPUS_PER_TASK`
at 23.02.7 and `SLURM_CPUS_PER_TASK` at 23.11.0 (src: `src/srun/opt.c`).

**Consequence for a front end.** Three schedulers take a bare count; SGE
also needs a site PE name (`sge/args.py:151-169`). The `SRUN_CPUS_PER_TASK`
export in `PLAN.md` matches 22.05; the changelog shows the need ends at 23.11.

### R2. Several tasks, possibly across nodes; node count

| | Option (example) | Value kind | Scope | Semantics | Built in or site-defined | Source |
|---|---|---|---|---|---|---|
| SGE | `-pe mpi 32` | name + count | per job; hosts by the PE's `allocation_rule` | consumable | PE site-defined; no node-count option in submit(1) | submit(1) `-pe`; sge_pe(5) |
| Slurm | `-n 32`, `-N 2`, `--ntasks-per-node=16` | counts (`-N min[-max]`) | per job; per node | consumable (as R1); "sbatch does not launch tasks" | built in | sbatch(1) `-n`, `-N`, `--ntasks-per-node` |
| LSF | `-n 32 -R "span[ptile=16]"` | count | per job; `ptile` = processors per host | slots are allocated (as R1) | built in; `-nnodes num_nodes` exists, documented for IBM Cluster Systems Manager (CSM) easy-mode jobs only ("Specifies the number of compute nodes that are required for the CSM job") | bsub -n; span_string; bsub -nnodes (fetched 2026-11-13); bsub options list |
| PBS | `-l select=2:ncpus=16:mpiprocs=16` | count of chunks × per-chunk counts | per chunk | consumable (`ncpus`) | built in; `-l place=scatter`: "Only one chunk with any MPI processes is taken from a host. A chunk with no MPI processes may be taken from the same vnode as another chunk." | qsub.1B; pbs_resources.7B `mpiprocs` |

Slurm 22.05 versus 25.11: 25.11 `-N` also accepts a `size_string`
(`--nodes=1-15:4`).

**Consequence for a front end.** A node count is an option on Slurm, a chunk
multiplier on PBS, a per-host figure on LSF (`-nnodes` for CSM jobs
only) and a property of the PE on SGE.
hpc-runner's SGE backend does not map `nodes` or `tasks`
(`sge/scheduler.py:243-244`).

### R3. Exclusive use of a node

| | Option (example) | Value kind | Scope | Semantics | Built in or site-defined | Source |
|---|---|---|---|---|---|---|
| SGE | `-l exclusive=TRUE` (site's name) | boolean | cluster global, per host or per queue, wherever the complex is given a value ("on a cluster global, per host and per queue basis") | consumable boolean with relop `EXCL` | site-defined | complex(5) `relop` |
| Slurm | `--exclusive` | flag (`=user`, `=mcs`; 25.11 adds `=topo`) | per node of the allocation | all CPUs and GRES of each node; memory only as requested | built in; the partition's `OverSubscribe` "takes precedence" | sbatch(1) `--exclusive` |
| LSF | `-x` | flag | per host | host takes no other job; "the queue must be configured to allow exclusive jobs" | built in, queue-gated | bsub -x |
| PBS | `-l place=excl` or `place=exclhost` | keyword | `excl`: "Only this job uses the vnodes chosen"; `exclhost`: "The entire host is allocated to the job" | placement | built in; vnode `sharing` attributes can override | qsub.1B |

**Consequence for a front end.** Three schedulers have a built-in spelling
with no value; on SGE it is a site resource.

### R4. Memory request

| | Option (example) | Value kind | Scope | Semantics | Built in or site-defined | Source |
|---|---|---|---|---|---|---|
| SGE | `-l mem_free=8G` or `-l h_vmem=8G` | size; bare number = bytes; `K M G T` ×1024, `k m g t` ×1000 | `consumable YES`: per slot; `JOB`: per job; otherwise compared, not booked | `mem_free` is a load value ("amount of free real memory"; load_parameters(5) lists it under "The following load information is only available under IBM AIX if Grid Engine is built with the perfstat library"); `h_vmem` is a limit (R5) | attribute choice and consumable flag are site configuration | complex(5) `consumable`; sge_types(5) `memory_specifier`; load_parameters(5) |
| Slurm | `--mem=8G`; `--mem-per-cpu=1G`; `--mem-per-gpu=4G` | size; "Default units are megabytes"; suffix `[K\|M\|G\|T]` | per node; per "usable allocated CPU"; per GPU; mutually exclusive | consumable when `SelectTypeParameters` includes memory (`CR_Memory`: "Memory is a consumable resource") [DIFF §4.2]; `--mem=0` = all memory of each node | built in | sbatch(1) `--mem`, `--mem-per-cpu`; slurm.conf(5) `SelectTypeParameters` |
| LSF | `-R "rusage[mem=8G]"` | number, MB by default; suffixes `KB`..`ZB` | `/job`, `/host`, `/task` suffix, else the `ReservationUsage` section, else `RESOURCE_RESERVE_PER_TASK` in `lsb.params` (with `=Y`, `mem` is reserved per slot/task), else "by job if it is a shared resource or by host if it is a host-based resource" | reservation ("By default, no resources are reserved") | `mem` built in | bsub -R; usage_string; lsb.resources `ReservationUsage` |
| PBS | `-l select=1:ncpus=8:mem=8gb` | size; suffix `b kb mb gb tb pb` (and word forms); default bytes | per chunk: `mem` is a chunk resource. RG-270 has both "Either job-wide or host-level" and "Can be requested only inside of a select statement"; UG: "ncpus and mem, that can be used only as chunk resources"; a job-wide `-l mem=` is the old syntax and is converted to a chunk (pbs_resources.7B: `qsub -lmem=4gb`) | consumable | built in | RG-270; RG-262; UG-52, UG-71; pbs_resources.7B |

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
| SGE | `-l h_vmem=8G` (`s_vmem` soft: SIGXCPU) | size | "For parallel processes, the limit is applied per slot" | limit on combined virtual memory; "aborted via a SIGKILL signal"; "only available on systems supporting RLIMIT_VMEM" | built in; consumable flag site-defined | queue_conf(5) RESOURCE LIMITS; complex(5) default host attributes |
| Slurm | none separate: `--mem` is both | size | as R4 | limit only if configured: `ConstrainRAMSpace` (cgroup OOM kills processes, step keeps running) or `OverMemoryKill` (whole step killed) | built in; enforcement site-defined | sbatch(1) 25.11 `--mem` NOTE; cgroup.conf(5); slurm.conf(5) `OverMemoryKill` |
| LSF | `-M 8G` (`-M 8G!` hard); `-v` for virtual memory | size; "By default, the limit is specified in KB" (when `LSF_UNIT_FOR_LIMITS` is not defined; that page says "Set to MB at time of installation") | per process by default; per job with `LSB_JOB_MEMLIMIT=y`; per host under cgroups with `-hl` | limit; separate from `rusage[mem]` | built in; enforcement mode site-defined | bsub -M; bsub -v; bsub -hl; LSB_JOB_MEMLIMIT; LSF_UNIT_FOR_LIMITS |
| PBS | `mem` in the chunk is also the limit; `-l pmem=`, `-l pvmem=` job-wide; `vmem` in a chunk | size | `mem`, `vmem`: per chunk, summed per host; `pmem`, `pvmem`: per process | `mem`: "By default, memory limits are not enforced"; needs `$enforce mem` (MoM polling). `pmem`, `pvmem` (by `setrlimit()`) and `vmem`: "Always" enforced (UG-62 table). AG Table 5-16 gives the `mem` and `vmem` limits as job-wide; RG-276 gives `vmem` as "a per-chunk resource limit" | built in; enforcement site-defined | AG-308, AG-309; UG-62; RG-274..276 |

Slurm 22.05 versus 25.11: 22.05 says enforcement "relies upon the
task/cgroup plugin or enabling of accounting"; 25.11 names
`ConstrainRAMSpace` and `OverMemoryKill`.

**Consequence for a front end.** LSF has two options (reserve, limit); SGE
has two attributes behind one option; Slurm and PBS have one request that is
also the limit when the site enables enforcement (PBS also has `pmem`,
`pvmem` and `vmem`, listed as always enforced). The repo's SGE capture
records `h_vmem` as a `setrlimit` (`failed 0`;
`tests/unit/test_schedulers/test_sge_fixtures.py:236-238`); queue_conf(5)
describes a SIGKILL.

### R6. Wall-clock limit

| | Option (example) | Value kind | Scope | Semantics | Built in or site-defined | Source |
|---|---|---|---|---|---|---|
| SGE | `-l h_rt=1:00:00` (`s_rt` soft) | duration: integer = seconds, or `h:m:s` | per job | limit; "aborted via the SIGKILL signal"; `s_rt` sends SIGUSR1 first | built in (default queue attribute) | queue_conf(5) RESOURCE LIMITS; sge_types(5) `time_specifier`; complex(5) |
| Slurm | `-t 1:00:00` | duration: "minutes", "minutes:seconds", "hours:minutes:seconds", "days-hours", "days-hours:minutes" and "days-hours:minutes:seconds" | per job | limit; SIGTERM then SIGKILL after `KillWait` (default 30 s); `OverTimeLimit` can extend; above the partition limit the job stays PENDING | built in | sbatch(1) `-t`; slurm.conf(5) `KillWait`, `OverTimeLimit` |
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
| Slurm | none in sbatch(1); `--propagate=CPU` carries the submit shell's rlimit | n/a | per process (rlimit): **UNVERIFIED** (sbatch(1) says only "The maximum amount of CPU time"; searched it for "cpu.time", "cputime", "RLIMIT_CPU"; no setrlimit(2) text is among the sources) | limit inherited, not requested | n/a | sbatch(1) `--propagate` |
| LSF | `-c 2:00` | duration `[hour:]minute`, normalised | whole job | limit; SIGXCPU, then SIGINT, SIGTERM, SIGKILL; with `LSB_JOB_CPULIMIT=n` "LSF passes the limit to the operating system", which enforces it per process | built in | bsub -c |
| PBS | `-l cput=2:00:00`; `-l pcput=` | duration | `cput` job-wide (all processes); `pcput` per process | limit; job terminated | built in | RG-269; RG-274; AG-308 |

**Consequence for a front end.** Three schedulers accept a CPU-time limit at
submit; sbatch(1) has no such option.

### R8. Local scratch space

| | Option (example) | Value kind | Scope | Semantics | Built in or site-defined | Source |
|---|---|---|---|---|---|---|
| SGE | `-l tmp_free=20G` (`tmp_free` is the man page's example name; the page shows only `qconf -aattr exechost complex_values tmp_free=10T $(qconf -sel)`, no `-l` request) | size, if the complex is typed MEMORY | per host in the man example; per slot if the complex is also consumable `YES` | compared with a load-sensor value, or booked as a consumable if so defined (table below) | site-defined ("set up a load sensor to report the value"; load_parameters(5) has no tmp value); the queue's `tmpdir` is built in but has no size | complex(5) "Working with resource attributes"; sge_types(5) `memory_specifier`; queue_conf(5) `tmpdir` |
| Slurm | `--tmp=20G` | size; "Default units are megabytes"; `[K\|M\|G\|T]` | per node | "minimum amount of temporary disk space per node", compared with the node's `TmpDisk`, the "total file system size", not free space; a job's request is not subtracted (read from source, not run) | built in | sbatch(1) `--tmp`; slurm.conf(5) `TmpDisk`, `TmpFS`; src 22.05 and 25.11: `src/slurmctld/node_scheduler.c`; src 25.11: `src/slurmd/slurmd/get_mach_stat.c`, `src/common/proc_args.c:str_to_mbytes` |
| LSF | `-R "rusage[tmp=20000]"`; filter form `select[tmp>20000]` | number in MB; with `LSF_ENABLE_TMP_UNIT=Y` the `LSF_UNIT_FOR_LIMITS` unit also applies to `tmp`; a unit suffix on `tmp` with `=N`: **UNVERIFIED** (section 7) | as R4 (`/job`, `/host`, `/task`, else configured) | `rusage[tmp=]`: reservation of the `tmp` load index (space on `/tmp`); `select[tmp>N]`: comparison only; `TMP` in an lsb.resources Limit section caps reservations | built in | load_indices; usage_string; select_string; LSF_ENABLE_TMP_UNIT; lsb.resources |
| PBS | `-l select=1:ncpus=N:dynscratch=10MB` (host-level) or `-l globalscratch=<space required>` (job-wide) | size (`type=size`) or integer (`type=long`), as the site types it | per chunk or job-wide | static: consumable. Dynamic: script-reported and checked when the job starts only ("cannot know how much will be used by another job while this job executes"); dynamic server-level amounts assigned to jobs are tracked and not overcommitted; dynamic host-level resources are "Deprecated as of 18.2.1" | site-defined custom resource; the AG's static server-level example counts scratch devices (`scratch=1`), not space | AG-272; AG-276; AG section 5.14; RG-261, RG-262; pbs_resources.7B "Custom Resources" |

Unit of a bare number, and what the request does:

| | Bare number | Filter, reservation or limit | Source |
|---|---|---|---|
| SGE | bytes, if the complex is typed MEMORY (`K M G T` ×1024, `k m g t` ×1000) | filter when the complex is not consumable (compared with the load-sensor value: "If the result of the comparison is false, the job cannot run in this queue"); booked against a total when the site defines it consumable (per slot with `YES`); not a limit | complex(5); sge_types(5) |
| Slurm | megabytes | filter: a node is dropped when the request exceeds its `TmpDisk` (the configured total, or the total size slurmd reports for `TmpFS`); nothing is subtracted; not a limit | sbatch(1) `--tmp`; slurm.conf(5) `TmpDisk`; src `node_scheduler.c` at both tags (read from source, not run) |
| LSF | megabytes (the `LSF_UNIT_FOR_LIMITS` unit when `LSF_ENABLE_TMP_UNIT=Y`) | `rusage[tmp=N]`: reservation ("By default, no resources are reserved"; "The resource value represents the initial reserved amount of the resource"); `select[tmp>N]`: filter; not a limit | load_indices; usage_string; select_string; LSF_ENABLE_TMP_UNIT |
| PBS | bytes for `type=size`; a plain integer with no suffix for `type=long` | static custom resource: booked against a total (consumable); dynamic: compared with a script's value when the job starts; not a limit | AG-272; AG-276; RG-261, RG-262 |

No source read says that any of the four enforces a scratch request as a
usage limit: the SGE, LSF and PBS pages describe scheduling only; Slurm's
code compares and does nothing else.

**Consequence for a front end.** Scratch is a named option on Slurm and a
built-in index on LSF; on SGE and PBS the site names it. Slurm takes a size
(megabytes by default); LSF a number in megabytes; SGE a size only if the
site types the complex MEMORY; PBS a size (`type=size`) or an integer
(`type=long`). A value with a unit suffix is not an integer, which an
integer-only map cannot carry.

### R9. GPUs

| | Option (example) | Value kind | Scope | Semantics | Built in or site-defined | Source |
|---|---|---|---|---|---|---|
| SGE | `-l gpu=2` | count | per slot (`YES`) or per job (`JOB`), by the complex's definition | consumable if defined so | site-defined; "gpu" does not occur in the SGE pages read | complex(5) `consumable` |
| Slurm | `--gres=gpu:2`, `--gres=gpu:a100:2`; `--gpus=2`; `--gpus-per-node=2`; `--gpus-per-task=1` | name[:type]:count | `--gres`, `--gpus-per-node`: per node; `--gpus`: per job; `--gpus-per-task`: per task | consumable | options built in; the GRES name must be in `GresTypes` ("No generic resources are managed by default") | sbatch(1); slurm.conf(5) `GresTypes`; gres.conf(5) `Name` |
| LSF | `-gpu "num=2"`, `-gpu "num=1/task:gmodel=K80"` | count plus keywords | "By default, the number is per host"; `/task` per task | consumable; "num=2 is converted to rusage[ngpus_physical=2]" | built in; `LSB_GPU_NEW_SYNTAX` default is `extend`; with `N` the legacy resources `ngpus_shared`, `ngpus_excl_t`, `ngpus_excl_p` must be used (weak evidence: page not saved; this statement came through a summarising fetch tool, the default from the page saved on 2026-11-13) | bsub -gpu; LSB_GPU_NEW_SYNTAX |
| PBS | `-l select=1:ncpus=4:ngpus=2` | count | per chunk | consumable (`flag=nh`) | site-defined custom resource (AG example: `create resource ngpus type=long, flag=nh`) | AG-286, AG-287 |

Slurm 22.05 versus 25.11: the first `--gres` sentence reads "Specifies a
comma-delimited list of generic consumable resources." in 22.05 and adds
"requested per node" in 25.11; both keep "The specified resources will be
allocated to the job on each node." 25.11 `--gpus` adds "one of each
GPU type per node if types are used".

LSF `-R` beside `-gpu` [LPR 3]: bsub -R says "If you specify GPU resource
requirements with the bsub -R command option, or the RES_REQ parameter, the
LSF ignores the -gpu option, as well as the LSB_GPU_REQ and GPU_REQ
parameters." LSB_GPU_NEW_SYNTAX says `j_exclusive`, `mode` and `mps` may be
given with `-gpu` in the same submission as `-R`, and "If you use the bsub
-R syntax with the bsub -gpu syntax for any other GPU requirement at the
job, application profile, or queue level (including gmem, gmodel, gtile, or
nvlink), LSF rejects the job submission." The two pages do not say the same
thing.

**Consequence for a front end.** A count is per node on Slurm `--gres`, per
host on LSF by default, per chunk on PBS and per slot or per job on SGE;
these agree for a single-node, single-chunk job with a per-job SGE complex.
A GPU type is part of the value on Slurm and LSF only.

### R10. Floating licences

| | Option (example) | Value kind | Scope | Semantics | Built in or site-defined | Source |
|---|---|---|---|---|---|---|
| SGE | `-l xcelium_lic=1` | count | cluster-wide when set in the `global` host's `complex_values` | consumable; per slot unless defined `JOB` | site-defined | complex(5) `consumable` |
| Slurm | `-L xcelium:1` | name[@db][:count], comma list | cluster-wide | consumable; "Slurm prevents jobs from being scheduled if their required license specification is not available" | option built in; names from `Licenses=` or slurmdbd | sbatch(1) `-L`; slurm.conf(5) `Licenses` |
| LSF | `-R "rusage[xcelium_lic=1]"` | count; `:duration=` is accepted syntax, but "Duration is not supported for static shared resources. If the shared resource is defined in an lsb.resources Limit section, then duration is not applied." | per job for a shared resource unless configured | consumable reservation | site-defined in `lsf.shared`; shared mapping in the `lsf.cluster` ResourceMap section [LPR 2] (weak evidence: page not saved) | bsub -R (verilog_lic example, "A static shared resource"); usage_string; lsb.resources; lsf.shared |
| PBS | `-l xcelium_lic=1` outside `select` | count | job-wide | consumable: "A job requesting a floating license must specify it outside of a selection statement" | site-defined custom resource | pbs_resources.7B "Custom Resources" |

Slurm 22.05 versus 25.11: 25.11 adds OR between
licences (`foo:4|bar`); CL 25.05: "Implement OR ('|') for job license
requests".

**Consequence for a front end.** All four count licences under a site-chosen
name against a site-configured total; PBS can instead query the amount
through a dynamic resource ("externally-managed application licenses"), and
Slurm remote licences are "served by the slurmdbd". Only Slurm has a
dedicated option; elsewhere the name shares a namespace with other
resources.

### R11. Queue or partition; project or account; priority

| | Option (example) | Value kind | Scope | Semantics | Built in or site-defined | Source |
|---|---|---|---|---|---|---|
| SGE | `-q short.q`; `-P proj`; `-p -100` | queue list; name; integer -1023..1024 | per job | `-q` "has all the properties of a resource request"; `-P` needs admin permission; "Users may only decrease the priority" | options built in; names site-defined | submit(1) `-q`, `-P`, `-p` |
| Slurm | `-p short`; `-A proj`; `--nice=100` | name list; name; integer | per job | earliest-starting partition of a list; nice default 100, range ±2147483645, "Only privileged users can specify a negative adjustment"; `--priority=<value>` also exists: "Only Slurm operators and administrators can set the priority of a job." | options built in | sbatch(1) `-p`, `-A`, `--nice`, `--priority` |
| LSF | `-q short`; `-P proj`; `-sp 50` | name list; name; integer 1..`MAX_USER_PRIORITY` | per job | first usable queue of the list; "The project does not have to exist before submitting the job"; out-of-range priorities "are rejected"; "Larger values represent higher priority; 1 is the lowest" | options built in | bsub -q, bsub -P, bsub -sp; `lsf_admin/job_priority_config.html` (fetched 2026-11-13) |
| PBS | `-q short`; `-P proj`; `-p 100` | `<queue name>`, `@<server name>` or `<queue name>@<server name>`; name; integer -1024..+1023 | per job | default project `_pbs_project_default`; `-A` is a separate accounting string; "Higher value indicates higher priority" | options built in | qsub.1B `-q`, `-P`, `-p`, `-A`; pbs_job_attributes.7B `Priority`; UG-120 |

**Consequence for a front end.** Every scheduler has all three. Priority
ranges and who may raise it differ. A larger Slurm nice lowers priority; on
SGE a higher value is "dispatched earlier"; on PBS "Higher value indicates
higher priority" (pbs_job_attributes.7B; UG-120: "-1024 (lowest priority)
and +1023 (highest priority)"); on LSF "Larger values represent higher
priority; 1 is the lowest" (`lsf_admin/job_priority_config.html`). bsub -sp
and qsub.1B `-p` themselves do not state the direction. LSF accepts an
unknown project at submit.

### R12. Node features

| | Option (example) | Value kind | Scope | Semantics | Built in or site-defined | Source |
|---|---|---|---|---|---|---|
| SGE | `-l bigmem=TRUE`; `-l arch="lx-amd64"` | boolean ("the strings TRUE and FALSE are allowed"); string with wildcards and `! & \|` | the global, host or queue object the complex is attached to | filter | site-defined complex; some (`arch`, `hostname`, `qname`) built in | complex(5) `type`; sge_types(5) MATCHING TYPES |
| Slurm | `-C bigmem`; `-C "intel&gpu"` | feature name; expression `& \| [] *n` | per node | filter; "a node either has a feature or it does not" | option built in; names from node `Features` | sbatch(1) `-C`; slurm.conf(5) `Features` |
| LSF | `-R "bigmem"` (the same as `select[bigmem]`: "If you do not give a section name, the first resource requirement string is treated as a selection string"); `-R "select[type==X86_64]"` | boolean name; string or numeric comparison | per host | filter; with an "exclusive resource" the host takes only jobs that ask for it | site-defined in `lsf.shared` (default TYPE is Boolean); `type`, `model` and others built in | select_string; bsub -R; lsf.shared |
| PBS | `-l select=1:ncpus=1:bigmem=True`; `:arch=linux` | boolean (`TRUE True true T t Y y 1`); string | per chunk | filter; "An unset Boolean resource at a server, queue, or vnode behaves as if that resource is set to "false"." | site-defined custom host-level resource; `arch` built in | RG-261; pbs_resources.7B "Boolean Resources", "Behavior of Unset Resources" |

**Consequence for a front end.** On SGE and PBS a feature is `name=value`
through the same option as a consumable. On Slurm it is a bare name; on LSF
a Boolean is a bare name and a string feature is `name==value`. SGE also
fills in `TRUE` for a Boolean requested with no value (read from source, not
run). complex(5) lists `TRUE` and `FALSE` for BOOL; `-l feat=1` (the plan's
table) is accepted and gets the same value as `TRUE`: a BOOL value goes
through the numeric parser, which maps `true` to 1 and `false` to 0 and
otherwise parses a number (src SoGE 8.1.9: `sge_centry.c`,
`sge_parse_num_par.c`; read from source, not run).

### R13. Specific nodes; excluding nodes

| | Option (example) | Value kind | Scope | Semantics | Built in or site-defined | Source |
|---|---|---|---|---|---|---|
| SGE | `-l hostname=exec1`; `-q all.q@exec1`; exclusion `-l hostname="!exec1"` | host expression | per job | filter; a HOST value containing `!` is treated as an expression and matched by the same evaluator as strings (read from source, not run); no man page example applies `!` to a host name (the `!` example is `arch="!lx-x86&!sol-amd64"`) | built in (`hostname` is a default queue attribute; its type HOST was read from the `daimh/sge` fork's `centry/hostname`, not from 8.1.9: complex(5) does not give it) | complex(5); sge_types(5) `wc_queue`, MATCHING TYPES; src SoGE 8.1.9: `sge_host.c`, `sge_string.c`, `sge_complex_schedd.c` |
| Slurm | `-w exec[1-2]`; `-x exec3` | host list, ranges, or file | per job | `-w`: the job "will contain all of these hosts and possibly additional hosts"; `-x`: excluded | built in | sbatch(1) `-w`, `-x` |
| LSF | `-m "exec1 exec2"`; exclusion `-R "select[hname!='exec3']"` | list of hosts, host groups or compute units | per job | `-m`: candidates; "By default, if multiple hosts are candidates, runs the job on the least-loaded host" (example: "run on one of the candidate hosts"); a parallel job can use more than one listed host | built in | bsub -m; bsub -R |
| PBS | `-l select=1:ncpus=2:host=exec1` (or `vnode=`) | string | per chunk | filter | built in; no exclusion syntax found: **UNVERIFIED**, a negative (searched "exclude", "!=", "host!=", "vnode!=", "avoid" in qsub.1B, pbs_resources.7B, UG and RG; the only hit is an ignored `-exclude` option of another command) | UG-69; pbs_resources.7B `host`, `vnode` |

**Consequence for a front end.** A host name is a string value in the
general resource option on SGE and PBS and a dedicated option on Slurm and
LSF. A Slurm list means all of the hosts; an LSF list, any of them
(a parallel job may use several).

### R14. Soft versus hard requests

| | Option (example) | Value kind | Scope | Semantics | Built in or site-defined | Source |
|---|---|---|---|---|---|---|
| SGE | `-soft -l hostname=exec1`, `-hard` to switch back | toggle over following `-q` and `-l` | per request | hint: "nice-to-have, but not essential"; "requests for consumable resources cannot be soft" | built in | submit(1) `-soft`, `-hard` |
| Slurm | `--prefer=fast` | feature expression | per job | hint for features only: tried first, then `--constraint` | built in; present on the 22.05 page | sbatch(1) `--prefer` |
| LSF | `-m "exec1+2 others"`; `-R "order[...]"`; `rusage[a=1\|\|b=1]` | host with level; sort key; alternatives | per job | ordering of candidate hosts; alternatives tried in turn | built in | bsub -m; bsub -R |
| PBS | none found for one job: **UNVERIFIED**, a negative (searched "soft" in UG, AG, RG, qsub.1B and pbs_resources.7B: only soft limits, `soft_walltime` and preemption) | n/a | n/a | n/a; UG-108 has "runone" sets of alternative jobs (2020.1 beta) | n/a | UG-108; qsub.1B; pbs_resources.7B |

**Consequence for a front end.** A soft form exists for any non-consumable
`-l` or `-q` request on SGE, for features on Slurm and for host order on
LSF; none was found for PBS (**UNVERIFIED**, section 7).

### R15. Unknown resource name at submit

| | Option (example) | Value kind | Scope | Semantics | Built in or site-defined | Source |
|---|---|---|---|---|---|---|
| SGE | `-l nosuch=1` | n/a | n/a | rejected: the qmaster job verifier refuses a name it cannot find, whatever `-w` is (message "unknown resource"; src SoGE 8.1.9), and the integration test asserts qsub's stderr names it. A defined consumable with no `complex_values` is accepted and never scheduled (from the repo's review note; no capture or SGE text was read for it). `-w` defaults to `n` | n/a | `tests/integration/test_submit.py:76-86`; `docs/dev/testbed/TESTBED_REVIEW.md:195-198, 340-343`; submit(1) `-w`; src SoGE 8.1.9: `sge_job_verify.c:339`, `sge_centry.c:790-791`, `msg_common.h:256` |
| Slurm | `-L nosuch:1` | n/a | n/a | an unknown licence, GRES or feature name is rejected at job creation (read from source, not run). Licence: "Invalid license specification" when the name is not found, or when the count is above the total unless `SchedulerParameters` has `allow_zero_lic`. GRES: `ESLURM_INVALID_GRES`, "Invalid generic resource (gres) specification", at both tags. Feature on no node's list: `ESLURM_INVALID_FEATURE`, "Invalid feature specification", at 25.11 unless `SchedulerParameters` has `ignore_constraint_validation` (`ignore_prefer_validation` for `--prefer`); 22.05 has the same check, but its skip conditions were not read | n/a | src 25.11: `job_mgr.c:_job_create`, `licenses.c:license_validate`, `gres.c:_get_next_gres`, `job_scheduler.c:_valid_node_feature`, `slurm_errno.c` (same strings at the 22.05 tag) |
| LSF | `-R "rusage[nosuch=1]"` | n/a | n/a | **UNVERIFIED** (section 7): no page found says what bsub does with an undefined name. Read: `defined()` accepts only names in `lsf.shared`; with `LSF_STRICT_RESREQ=Y` a non-consumable in `rusage` is rejected | n/a | select_string; LSF_STRICT_RESREQ; lsf.shared |
| PBS | `-l nosuch=1` | n/a | n/a | rejected by the server (read from OpenPBS source `v23.06.06`, not run; not PBS Professional 2021.1.2). An unknown job-wide name is refused when the job is queued in an execution queue, not in a route queue (same branch at `v20.0.1`). An unknown name, or a resource without the `h` flag, inside `select` is refused (at `v20.0.1` the `select` path was not read). A host-level resource given job-wide together with `select` or `place` is refused. The reply is the message, ": " and the resource name; messages `Unknown resource` and `Resource invalid in "select" specification`. qsub's own stderr line was not captured. Error codes: `PBSE_UNKRESC 15035 Unknown resource`, `PBSE_INVALSELECTRESC 15108 resource invalid in select spec`. The forum threads of [LPR 10] are secondary and were not fetched | n/a | RG-392; RG-394; src OpenPBS v23.06.06: `req_quejob.c`, `vnparse.c`, `attr_resc_func.c`, `pbs_messages.c`, `reply_send.c` |

**Consequence for a front end.** SGE, Slurm (licence, GRES and feature
names) and PBS (per the OpenPBS source) reject an unknown name at submit;
the Slurm and PBS statements were read from source, not run. LSF is
**UNVERIFIED**. On SGE a known name with no capacity is accepted and waits.

### R16. Arrays: per task or per array

| | Option (example) | Value kind | Scope | Semantics | Built in or site-defined | Source |
|---|---|---|---|---|---|---|
| SGE | `-t 1-10` | range | per task | "All array job tasks inherit the same resource requests and attribute definitions"; tasks "scheduled independently" | built in | submit(1) `-t` |
| Slurm | `-a 0-9%4` | index list | per task | "multiple jobs to be executed with identical parameters" | built in | sbatch(1) `-a` |
| LSF | `-J "arr[1-10]"` | index list | per element | "groups of jobs with the same executable and resource requirements" | built in | job_arrays_lsf |
| PBS | `-J 1-10` | range `X-Y[:Z]`, indices ≥ 0 | per subjob | "All subjobs in one job array have the same attributes, including resource requirements and limits"; "The scheduler handles each subjob in a job array as a separate job"; subjobs "share a single job script, including the PBS directives" | built in | qsub.1B `-J`; UG-149; UG-150 |

**Consequence for a front end.** The text is explicit on all four: a
request applies to each task; no page read describes a per-array total.

### R17. Value grammar

| | Option (example) | Value kind | Scope | Semantics | Built in or site-defined | Source |
|---|---|---|---|---|---|---|
| SGE | `8G`, `1:0:0`, `TRUE`, `"lx*\|sol*"` | size: `K M G T` 1024-based, lower case 1000-based, bare = bytes. Duration: seconds or `h:m:s`. Boolean: `TRUE`/`FALSE`. String: wildcard expression | n/a | typed by the complex (`INT DOUBLE TIME MEMORY BOOL STRING CSTRING HOST`; `RESTRING` is deprecated) | types built in | complex(5) `type`; sge_types(5) |
| Slurm | `8G`, `1-00:00:00`, `gpu:a100:2` | size: `[K\|M\|G\|T]`, bare = MB; base 1024: stated for GRES counts; not stated on the `--mem` page, read from `str_to_mbytes` at both tags (read from source, not run). Duration: R6. Count: integer. Lists: commas; feature operators `& \| [ ] *` and parentheses | n/a | typed by option | built in | sbatch(1) `--mem`, `--gres`, `-t`, `-C`; src: `src/common/proc_args.c:str_to_mbytes` |
| LSF | `8G`, `1:30`, `select[hname!='host06-x12']` | size: bare = MB in `rusage`/`select`, KB for `-M` when `LSF_UNIT_FOR_LIMITS` is not defined (the installer sets it to MB), rescaled by `LSF_UNIT_FOR_LIMITS`; suffixes `KB`..`ZB`; example converts 1TB to 1024 GB. Duration: `[hour:]minute`. Boolean: bare name. Non-alphabetic strings in single quotes | n/a | typed by section; sections separated by a space | built in | bsub -R; LSF_UNIT_FOR_LIMITS; bsub -W |
| PBS | `8gb`, `1:00:00`, `True`, `host=exec1` | size: `b kb mb gb tb pb` 1024-based, bare = bytes. Duration: seconds or `[[HH:]MM:]SS[.milliseconds]`. Boolean: `True`, `1` and others. String: "Any character, including the space character". Chunks are joined by `+`; `:` separates the `resource=value` items inside a chunk and the count from its chunk | n/a | typed by the resource's definition (`long size float string string_array boolean`; the guide also lists Duration) | types built in | RG-261, RG-262; pbs_resources.7B |

**Consequence for a front end.** `20G` is valid as typed on SGE and Slurm.
On PBS `gb` is the documented suffix (RG-262); the OpenPBS `v20.0.1` size
parser also takes `20G`, `20g` and `20GB` (src: `attr_fn_size.c`; read from
source, not run; PBS Professional 2021.1.2 itself was not read). LSF takes
`20G` or `20GB`; the qualifier "where units are enabled" is **UNVERIFIED**
(the pages state an enabling condition for `tmp` only; section 7). Every
scheduler has resource values that are not integers (sizes, strings,
booleans). hpc-runner refuses whitespace in a rendered SGE value
(`sge/scheduler.py:431-459`).

## 4. Summary matrix

| Resource | SGE | Slurm | LSF | PBS | hpc-runner today |
|---|---|---|---|---|---|
| R1 CPUs, one process | `-pe <pe> N` | `-c N` | `-n N -R "span[hosts=1]"` | `select=1:ncpus=N` | `cpu` |
| R2 tasks / nodes | `-pe <pe> N` / none | `-n` / `-N` | `-n` / `span[ptile=]` (`-nnodes`: CSM jobs only) | `select=N:...:mpiprocs=` | `tasks`, `nodes` (unused on SGE) |
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
| R12 features | `-l name=TRUE` (`=1` parses to the same value) | `-C` | `select[name]` | `select=...:name=True` | `resources` |
| R13 nodes | `-l hostname=` | `-w`, `-x` | `-m` | `select=...:host=` (no exclusion found: UNVERIFIED) | `resources` |
| R14 soft | `-soft` | `--prefer` | `-m host+`, `order[]` | none found (UNVERIFIED) | not modelled |
| R15 unknown name | rejected | licence, GRES and feature rejected (read from source, not run) | UNVERIFIED | rejected (OpenPBS source, not run) | n/a |
| R16 arrays | per task | per task | per element | per subjob | n/a |
| R17 grammar | typed by complex | typed by option | typed by section | typed by resource | `mem`, `time` parsed; `resources` values passed through |

Keys: `src/hpc_runner/core/spec.py:40-48` (`cpu` to `tasks`), `:71`
(`resources`), `:75` (`scheduler_args`); `resources` rendering:
`schedulers/sge/scheduler.py:419-421`.

## 5. Classification

**Same meaning on all four** (spelling differs):

- R1 CPUs for one process (SGE also needs a site PE).
- R6 wall-clock limit (grammar differs; LSF normalises the limit by host CPU
  factor unless `ABS_RUNLIMIT=Y`).
- R10 floating licences (counted, cluster-wide, site-named; on SGE the count
  is multiplied by slots unless the complex is `JOB`).
- R11 queue, project, priority (priority ranges differ; a larger value is
  higher priority on SGE, LSF and PBS and lower on Slurm `--nice`).
- R12 node features (a filter on a site-defined attribute).
- R16 arrays (per task; per subjob on PBS).

**Present on some, or differing in scope, with the gaps named:**

- R2 tasks and nodes: no node-count option on SGE; on LSF `-nnodes`, for CSM
  jobs only.
- R3 exclusive node: site-defined on SGE.
- R4 memory request: per node, per CPU or per GPU (Slurm), per chunk (PBS),
  per job, host or task (LSF), per slot or job (SGE).
- R5 memory limit: no separate option on Slurm; enforcement depends on
  site configuration on Slurm, LSF and PBS.
- R7 CPU-time limit: no sbatch option.
- R8 scratch: built in on Slurm and LSF; site-defined on SGE and PBS.
- R9 GPUs: built in on Slurm and LSF; site-defined on SGE and PBS; type in
  the request on Slurm and LSF only.
- R13 nodes: no exclusion syntax found for PBS (**UNVERIFIED**, section 7).
- R14 soft requests: none found for PBS (**UNVERIFIED**, section 7);
  features only on Slurm.
- R15 unknown names: rejected on SGE, Slurm and PBS (Slurm and PBS read from
  source, not run); LSF **UNVERIFIED**.
- R17 grammar: no two schedulers share a size and duration grammar.

**One scheduler only:** none of R1 to R17. Sub-features seen on one
scheduler only: Slurm `--mem-per-gpu`; LSF `rusage` `duration`/`decay`; SGE
`-soft` on any non-consumable `-l` or `-q` request; PBS word-size suffixes (`kw`).

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
| `scratch=20G` | `-l scratch=20G` (site complex) | `--tmp=20G` | `-R "rusage[tmp=20480]"` (MB while `LSF_ENABLE_TMP_UNIT=N`, the default; a suffix on `tmp` is UNVERIFIED, section 7) | `-l select=1:<site name>=20gb` |
| `tmpfs=8G` | `-l tmpfs=8G` (site complex) | no equivalent found (**UNVERIFIED**: the repo gives `tmpfs` no meaning, so there was nothing to look for) | no equivalent found (**UNVERIFIED**, same reason) | site custom resource |
| `h_vmem=50M` | `-l h_vmem=50M` | no equivalent option; `--mem` enforcement is RSS-based (sbatch(1) 22.05) | `-v 51200` (virtual, whole job; KB only when `LSF_UNIT_FOR_LIMITS` is not defined; the installer sets MB, under which this is 51200 MB) or `-M 50M` | `-l select=1:vmem=50mb` or `-l pvmem=50mb` |
| `hostname=exec1` | `-l hostname=exec1` | `-w exec1` | `-m exec1` | `-l select=1:host=exec1` |

The repo does not say what `tmpfs` measures; that row maps the name only.

## 7. Unverified

Accepted by the user on 2026-11-15: the items below are closed as they
stand. LSF is dropped (items 1, 2, 3, 10). Items 4, 7, 8, 9, 12, 13, 14
and 15 do not change how hpc-runner is built. Items 5 and 6, the PBS
`-V` result and the difference between OpenPBS and PBS Professional wait
until a PBS backend is planned.

Each item is marked **UNVERIFIED** where it stands. The 2026-11-13 re-check
settled these former items, now stated with their source in the R-section:
Slurm unknown GRES and feature names (R15), PBS unknown or misplaced
resource (R15), PBS arrays (R16), Slurm `--tmp` (R8), Slurm `--mem` base
(R17), SGE BOOL spelled `=1` (R12), SGE host exclusion (R13), LSF
`LSB_GPU_NEW_SYNTAX` and `-R` beside `-gpu` (R9), LSF and PBS priority
direction (R11), LSF `-M` default unit (R5, R17), PBS `mem` outside
`select` (R4).

1. **LSF, unknown resource name in `rusage[]` or `select[]`** (R15,
   Appendix A item 2): all saved and fetched LSF pages were searched for
   "unknown resource", "undefined resource", "bad resource", "invalid
   resource", "not defined in lsf.shared", "Job not submitted"; the two
   troubleshooting pages were read; the IBM docs search returned no hit for
   "Bad resource requirement syntax". LSF is closed source.
2. **LSF `tmp` with a unit suffix when `LSF_ENABLE_TMP_UNIT=N`** (R8, R17,
   section 6): bsub -R says "you can specify unit for mem, swp and tmp in
   select and rusage section" and also that `tmp` is outside
   `LSF_UNIT_FOR_LIMITS` by default. bsub -R, LSF_ENABLE_TMP_UNIT,
   LSF_UNIT_FOR_LIMITS, load_indices, usage_string and
   `res_limits_units.html` were read; none says whether `tmp=20G` is
   accepted or how it is read.
3. **LSF, "where units are enabled" for a suffix on `mem` or `swp`** (R17):
   bsub -R, bsub -M and LSF_UNIT_FOR_LIMITS were read in full; an enabling
   condition is stated for `tmp` only.
4. **Slurm `--propagate=CPU` is per process** (R7): sbatch(1) at both tags
   says only "The maximum amount of CPU time"; searched for "cpu.time",
   "cputime", "RLIMIT_CPU".
5. **PBS, no host exclusion syntax** (R13): a negative. Searched "exclude",
   "!=", "host!=", "vnode!=", "avoid" in qsub.1B, pbs_resources.7B, UG, RG.
6. **PBS, no soft request for one job** (R14): a negative. Searched "soft"
   in UG, AG, RG, qsub.1B, pbs_resources.7B.
7. **`tmpfs`, no Slurm or LSF equivalent** (section 6): the repo gives
   `tmpfs` no meaning, so there was no property to look for.
8. **PBS `resourcedef` file is legacy** (Appendix A item 6): searched AG (no
   occurrence) and RG (`pbs_python -r`, `pbs_snapshot` file list); no text
   calls it legacy.
9. **PBS forum threads 2604 and 3360** (Appendix A item 10): secondary; not
   fetched.
10. **Weak evidence: page not saved** (R9, R10): the `lsf.cluster`
    ResourceMap statement and the `LSB_GPU_NEW_SYNTAX=N` statement came
    through a summarising web-fetch tool, not from a saved page (the
    `LSB_GPU_NEW_SYNTAX` default was read from a saved page).
11. **Read from source, not run**: Slurm unknown GRES and feature names
    (R15), Slurm `--tmp` as a filter (R8), Slurm `--mem` base (R17), SGE
    BOOL `=1` (R12), SGE `hostname="!exec1"` (R13; one of the two checkers
    who looked held it not verified from the man pages alone), PBS unknown
    or misplaced resource (R15), PBS size suffixes (R17). The PBS source
    read was OpenPBS `v20.0.1` and `v23.06.06`, not PBS Professional
    2021.1.2. The Slurm 22.05 conditions for skipping feature validation
    were not read. The SGE `hostname` type was read from the `daimh/sge`
    fork, not from 8.1.9.
12. **SGE, a consumable with no `complex_values`** (R15): the repo's review
    note says "accepted and never scheduled". Testbed run G1 (soge 8.1.9,
    `gpu` INT consumable, default 0, no `complex_values` on any host,
    queue or global): `-l gpu=1` ran, 3 of 3; `-l gpu=5` stayed `qw`. The
    cause was not traced, so the rule is still unverified.
13. **Provenance statements** (header, section 1): who did the 2026-11-09
    research, what the brief listed, and the HTTP 503 and 404 statuses of
    that day have no source that can be re-read.
14. **Live licence feeds** (R10): not researched; see [DIFF §4.3], [LPR 7].
15. **OCS**: not read (Son of Grid Engine only).

### Run on the SGE testbed, 2026-11-14

Son of Grid Engine 8.1.9 (`soge` testbed). Output:
`runs/sge-soge-2026-11-14-exp*.out`; table in `SGE_SLURM_DIFFERENCES.md`,
"Testbed runs". These turn four "read from source, not run" items into
runs:

- R15: `-l nosuch=1` is rejected at submit, `unknown resource "nosuch"`,
  exit 1 (run E10).
- R13: `-l 'hostname=!exec1'` runs on the other host, 5 of 5, both ways
  (run F2).
- R12: for a BOOL complex set `TRUE` on one host, `=1`, `=TRUE` and `=true`
  all select that host; `=0`, `=FALSE` and `=2` stay pending; `=yes` is
  rejected (run G2).
- R8, R4: not run (no scratch or memory consumable was exercised).

### Run on the Slurm testbeds, 2026-11-14 and 2026-11-15

Debian slurm-wlm 24.11.5 and 22.05.8. Table and differences between the
two: `SGE_SLURM_DIFFERENCES.md`, "Testbed runs". For this file:

- R8: `--tmp` is a filter. Two jobs on one node, each `--tmp=800` of
  `TmpDisk=1000`, ran together. `--tmp=5000` is rejected at submit.
- R10: licences are counted (third job of three waits, reason `Licenses`);
  an unknown licence and a count above the total are rejected at submit.
- R9, R12, R15: `--gres=gpu:1` and `-C fast` select the node that has them;
  an unknown GRES name or feature is rejected at submit; `--gres=gpu:5`
  (more than any node has) is rejected at submit.
- R17: `--mem=1G` is recorded as `1G`, `--mem=1000` as `1000M`.
- R11: an unknown partition is rejected; an unknown account is accepted
  when the site does not enforce accounts.

### Run on the PBS testbed, 2026-11-15

OpenPBS v23.06.06 built from source (Rocky Linux 8), not PBS Professional.
Custom resources defined for the run: `scratch` (size, flags `nh`),
`ngpus` (long, `nh`), `fast` (boolean, `h`), `lic` (long, `q`, 2 on the
server), all added to the scheduler's `resources:` line. Output:
`runs/pbs-openpbs-2026-11-15-exp*.out`.

- R9: `ngpus` is not built in on OpenPBS 23.06.06: `set node ...
  resources_available.ngpus` gives "Unknown resource" until the resource is
  created. Once created, `select=1:ngpus=1` runs on the node that has it.
- R15: an unknown name is rejected at submit, job-wide (`-l nosuch=1`:
  "Unknown resource Resource_List.nosuch", exit 187) and inside `select`
  ("Unknown resource: nosuch", exit 187). An unknown queue: exit 170. A
  bad boolean value: "Illegal attribute or resource value", exit 166.
- R15: a request no node can meet is accepted and waits with the comment
  "Can Never Run" (`select=1:ncpus=99`, `lic=5`, `ngpus=2`). `afterok` on
  an unknown job id is accepted.
- R17: for a `size` resource `500mb`, `500MB`, `500m`, `500M`, `1G`, `1gb`,
  `1GB` are all accepted; a bare `500` is 500 bytes.
- R8: a `size` resource with flags `nh` is booked: of two jobs asking
  600mb of a node's 1gb, the second waits and `resources_assigned.scratch`
  shows the first.
- R10: a server-level consumable is counted: third job of three waits.
- R12: `fast=True`, `fast=true` and `fast=1` select the node that has it;
  `fast=False` selects the other.
- R4: job-wide `-l mem=1gb` becomes `select=1:mem=1gb:ncpus=1`.
- R16: an array's subjobs each get the request (`ncpus=2` each, on
  different nodes). Ids are `39[].master` and `39[1].master`; output files
  `<script>.o39.1`.
- R11: `-p` takes -1024 to 1023 (`-p 2000`: "illegal -p value").
- Not run: host exclusion, soft requests, `place=scatter`.
- Also seen: exit codes are 256 plus the signal number for a signalled job
  (265, 271), -29 for an exceeded walltime; a job whose output directory is
  missing stays in state `E`; `qsub -V` did not carry a caller's variable
  while `-v NAME` did (the caller's environment held exported bash
  functions; cause not traced).

## 8. Questions for the main session

Item numbers are unchanged. Items 1 and 8 are no longer questions: the
2026-11-13 re-check settled them and each now states the fact.

1. Fact, not a question: bsub -gpu reads "By default, the number is per
   host", with `/task` as the alternative.
2. Q2: `scratch=20G`, `tmpfs=8G`, `h_vmem=50M` are sizes and
   `hostname=exec1` is a host name; Slurm has an option for three of them
   (`--tmp`, `--mem`, `-w`). Are they carried by the three maps with
   non-integer values, by new keys, or by `scheduler_args`?
3. R8: scratch is built in on Slurm and LSF and site-defined on SGE and
   PBS. Is it a fourth kind beside licence, device and feature, a device
   with a size value, or out of scope?
4. R13: is a specific-host request part of the model (two integration
   tests use one), or a raw argument?
5. R12: `PLAN.md` renders an SGE feature as `-l feat=1`; complex(5) documents
   `TRUE`/`FALSE`; the SoGE 8.1.9 parser gives `1` the same value as `TRUE`
   (read from source, not run). Which is rendered, and is a capture of
   `-l <bool>=1` wanted first?
6. R9: Slurm offers `--gres=gpu:N` (per node) and `--gpus=N` (per job).
   `PLAN.md` names `--gres`. Is that the intended one for multi-node jobs?
7. R9, R10: a GPU type (`gpu:a100:2`, `gmodel=`) and a licence server
   (`name@db`) are part of the value. In scope for the maps?
8. Fact, not a question: Slurm rejects an unknown GRES name (both tags), an
   unknown feature name (25.11) and an unknown licence name at job creation
   (R15; read from source, not run). No testbed capture of the three was
   taken.
9. R5: is a capture of a job exceeding `--mem` under `ConstrainRAMSpace`
   wanted before Slurm status mapping is fixed?
10. R4: `--mem-per-cpu` counts allocated CPUs, which can exceed `cpu`. Does
    the plan's `memory_per = "cpu"` division need a note or a guard?
11. R1: CL 23.11 restores srun's use of `SLURM_CPUS_PER_TASK`. Is the
    plan's export of `SRUN_CPUS_PER_TASK` kept for all versions?
12. R3, R7, R14: exclusive nodes, CPU-time limits and soft requests are not
    modelled today. In scope, or left to `scheduler_args`?
13. Do the LSF and PBS items still open in section 7 (1, 2, 3, 5, 6, 8, 9,
    10) need closing now, or are they parked?

## Appendix A. LSF and PBS: the earlier research (2026-11-04)

A factual record of how IBM Spectrum LSF and PBS Professional / OpenPBS take
resource requests (licences, devices, node features, memory, cores), gathered
on 2026-11-04 by a research sub-agent for the three maps in `PLAN.md`, before the
sections above were written; corrected on 2026-11-13 where the re-check
found an error (quotes, page labels, items marked UNCONFIRMED). Like
`SGE_SLURM_DIFFERENCES.md` it records behaviour and its source and makes no
decisions. Every claim cites a documentation page or guide section; where
the primary text was not found the item says **UNVERIFIED**.

The Summary table at the end is the four-scheduler comparison that the table in
`PLAN.md` condenses; this file keeps the "Must know kind?" column and the per-item
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
- (ii) Node attribute: `select[]`. Boolean resources "have a value of one if they are defined for a host, and zero if they are not defined for the host": `bsub -R "bigmem"` (`defined(bigmem)` also shown); the form `bsub -R "select[bigmem]"` is not printed on the pages and follows from "If you do not give a section name, the first resource requirement string is treated as a selection string (select[selection_string])"; on those pages `bigmem` is the example of an exclusive resource [bsub.__r.1.html; ?topic=strings-selection-string]. Strings: `select[type==X86_64 && tmp>10000]`, shared strings quoted `os_version=='4.2'`.
- (iii) GPUs: see 3.

**2. Licence definition.** Resources are declared in `lsf.shared` Resource section with TYPE Numeric/Boolean/String and CONSUMABLE; "Static and dynamic numeric resources can be specified as consumable"; by default external shared numeric resources are consumable, String/Boolean are not; "There is no distinction between shared and non-shared resources" in lsf.shared (that is lsf.cluster ResourceMap, e.g. `lic1 (10@[all])`) [lsf_admin/resource_section_configure.html; floating_software_licenses.html]. `rusage` is numeric-consumable only: "LSF rejects resource requirement strings where an rusage section contains a non-consumable resource" [?topic=strings-usage-string]; the selection-string and `LSF_STRICT_RESREQ` pages tie this to `LSF_STRICT_RESREQ=Y` ("Set to Y at time of installation"), and the Resource-section page says "Non-consumable resources are ignored in rusage sections". A user can see kind via `lsinfo -l <name>` (TYPE, CONSUMABLE columns) [resource_section_configure.html]. License Scheduler: features are still requested in rusage, `bsub -Lp Lp1 -R "rusage[AppZ201=2]" myjob`; `-Lp` "Assigns the job to the specified LSF License Scheduler project" [lsf_config_ref/lsf.licensescheduler.5.html; bsub.__lp.1.html]. Unknown name: **UNVERIFIED** (section 7, item 1). Docs say `defined()` accepts only names configured in lsf.shared and that under `LSF_STRICT_RESREQ=Y` malformed select strings are rejected at bsub; with `BSUB_CHK_RESREQ` "bsub only checks the select section of the resource requirement", without submitting [selection-string page; lsf_envars_ref.html]. No primary text found for the exact message or pend-vs-reject behaviour of an undefined rusage name.

**3. GPUs.** Current: `bsub -gpu "num=2:mode=exclusive_process:mps=yes"`; default `num=1:mode=shared:mps=no:j_exclusive=no`; "num=2 is converted to rusage[ngpus_physical=2]"; mode/j_exclusive/mps "are not supported in the bsub -R syntax" [bsub.gpu.1.html; lsf.conf.lsb_gpu_new_syntax.5.html, default `extend` (page fetched and read on 2026-11-13)]. `-R "rusage[ngpus_physical=1:gmodel=K80]"` (the page's example) remains for compound/alternative requests; bsub -R: when GPU resource requirements are given with `-R`, "the LSF ignores the -gpu option"; LSB_GPU_NEW_SYNTAX: `j_exclusive`, `mode` and `mps` may be given with `-gpu` beside `-R`, and mixing any other GPU key across both forms rejects the job (the two pages do not say the same thing) [bsub.__r.1.html; lsf_gpu/lsf_gpu_example_jobs.html]. Legacy `ngpus_shared/ngpus_excl_*` cannot be used with `LSB_GPU_NEW_SYNTAX=Y`.

**4. Memory/cores.** `-n min_tasks[,max_tasks]`; slots usually equal tasks [bsub.n.1.html]. `-M mem_limit[!]`: "By default, LSF sets a per-process (soft) memory limit for all the processes that belong to the job"; `LSB_JOB_MEMLIMIT=y` makes it a per-job limit enforced by LSF (sum over processes); `!` = hard limit [bsub.__m.1.html; lsf.conf.lsb_job_memlimit.5.html]. Units: `-M` default KB when `LSF_UNIT_FOR_LIMITS` is not defined, rusage mem/swp/tmp default MB, both scaled by `LSF_UNIT_FOR_LIMITS` ("Set to MB at time of installation"; `tmp` only with `LSF_ENABLE_TMP_UNIT=Y`) [lsf.conf.lsf_unit_for_limits.5.html]. `rusage[mem=N/task|/job|/host]` sets reservation method, overriding lsb.resources ReservationUsage (PER_JOB/PER_HOST/PER_TASK; the `lsb.params` parameter `RESOURCE_RESERVE_PER_SLOT` is obsolete) [usage-string page; ?topic=reservationusage-method]. Default method when unconfigured: "RESOURCE_RESERVE_PER_TASK parameter still controls resources not configured in lsb.resources. Resources not reserved in lsb.resources are reserved by job if it is a shared resource or by host if it is a host-based resource." [lsb.resources ReservationUsage]. `affinity[core(5)]` = 5 cores per task [?topic=strings-affinity-string].

**5. One door?** Neither: one option (`-R`) but typed by section (select = attributes, rusage = numeric consumables), plus separate typed options (`-gpu`, `-M`, `-n`, `-Lp`). The section choice encodes the kind.

### PBS Pro / OpenPBS (qsub)

**6. Chunk vs job-wide.** "Resources are requested by using the -l option, either in job-wide requests using <resource name>=<value> pairs, or in chunks inside of selection statements"; `-l select=[N:]<chunk>[+...]`, `-l place=...` [qsub.1B; RG 2.59.2.6, RG-217]. Scope column: "Job-wide and can be requested only outside of a select statement" / "Host-level and can be requested only inside of a select statement" [RG 5.5, RG-267]. Built-ins: ncpus, vmem Host-level consumable; arch Host-level string; walltime, cput, pmem, pvmem, nodect Job-wide; mem "Either job-wide or host-level" [RG ch.5 tables]. Custom: `qmgr -c "create resource <name> type=<long|size|float|string|string_array|boolean> flag=<flags>"`; flags: none = server/queue, non-consumable; `h` = host-level, "Required for any resource that will be used inside a select statement"; `nh` = host-level consumable; `fh` = consumable on first vnode only; `q` = consumable at queue and server [RG 2.47.7.4, Table 2-18/2-19, RG-160..162]. `resourcedef` file is legacy: **UNVERIFIED** (AG has no occurrence; RG names the file only for `pbs_python -r` and in the `pbs_snapshot` file list; no text calls it legacy); qmgr is the documented path.

**7. Licences.** No built-in licence concept; the Glossary's example of a job-wide resource is a custom `FloatingLicenses` set via `resources_available` [RG-11]. Static: `create resource sitelicense type=long, flag=q`, `set server resources_available.sitelicense=N`, add to `resources:` line in sched_config; dynamic (external licence manager): `create resource floatlicense type=long` (no flags) plus `server_dyn_res: "floatlicense !/usr/local/bin/serverdyn.pl"`; request `-l floatlicense=<n>` (plus a Boolean chunk resource for eligible hosts) [AG 5.14.3, AG-270]. "A scheduler honors all resources listed in the resources: line ... If this line is not present, a scheduler honors all resources"; listing is "required only if the resource is to be specifically requested by jobs" [AG 4.5.3, AG-97; AG-175].

**8. GPUs.** `ngpus` is not in the built-in table; AG: "We recommend that the custom GPU resource is named ngpus", `create resource ngpus type=long, flag=nh`, set `resources_available.ngpus` per node, add to `resources:`; "used exactly the way you use the ncpus resource" [AG 5.14.7.1]. Request: `-l select=1:ngpus=2`.

**9. Node attributes.** Chunk-level Boolean/string resources use the same `name=value` syntax inside select: `qsub -lselect=1:arch=windows`, `-l select=runsAppF=True`; a separate example defines `create resource CanRun type=boolean, flag=h` [`arch=windows`: AG-667; `runsAppF`: AG-279; `CanRun`: AG-270; RG arch row]. Syntactically indistinguishable from consumables; only the server's flag differs.

**10. Unknown names.** Server error codes: `PBSE_UNKRESC 15035 Unknown resource`; `PBSE_INVALSELECTRESC 15108 resource invalid in select spec` [RG-392, RG-394]. Forum reports show qsub rejecting with "unknown resource ngpus" and "qsub: Resource invalid in "select" specification: <name>" when the resource lacks the `h` flag (community.openpbs.org threads 2604, 3360; secondary; **UNVERIFIED**: not fetched). The behaviour itself is in the OpenPBS server source (`v23.06.06`; read from source, not run; not PBS Professional 2021.1.2), where the message is capitalised `Unknown resource`: see R15. So the submitter must put host-level (`h`) resources inside select and job-wide ones outside; the server enforces this.

**11. Memory.** Chunk `mem` applies to "the part of the job running on that chunk"; `-l select=6:ncpus=2:mem=4gb` = 4 GB per chunk; job-wide limit is "the smaller of a job-wide resource request and the sum of that for all chunks" [AG 5.4.9.2, AG-238; AG-309]. Size units: `b/w, kb/kw, mb/mw, gb/gw, tb/tw, pb/pw`, default bytes, rounded up to kb [RG Table 5-1, RG-262]. `pmem`/`pvmem` = per-process, job-wide; `vmem` = per-chunk host-level consumable [RG ch.5].

### Summary

| | Floating licence | Device count (scope in each row) | Node feature | Must know kind? |
|---|---|---|---|---|
| SGE | `-l lic=1` | `-l gpu=2` (per slot or per job) | `-l feat=1` (same value as `=TRUE`; read from source, not run) | No (one door; complex(5) decides) |
| Slurm | `-L lic:1` | `--gres=gpu:2` (per node) | `-C feat` | Yes (three options) [sbatch.html] |
| LSF | `-R "rusage[lic=1]"` | `-gpu "num=2"` (or `rusage[ngpus_physical=2]`; per host by default) | `-R "select[feat]"` | Yes (section/option encodes it; rusage rejects non-consumables with `LSF_STRICT_RESREQ=Y`) |
| PBS | `-l lic=1` (job-wide) | `-l select=1:ngpus=2` (per chunk) | `-l select=1:feat=True` | Yes (chunk vs job-wide, server rejects misplacement) |
