:orphan:

Configuration
=============

This page describes the TOML configuration format used by **hpc-runner** and how
configuration values map to scheduler flags. Each scheduler has its own
page for the detail: :doc:`sge` and :doc:`slurm`.


Where configuration is loaded from
----------------------------------------

Two files are read, in this order.

1. **The project file:** ``$HPC_RUNNER_CONFIG`` if set and the file exists,
   otherwise ``<git root>/hpc-runner.toml``. It is one or the other: the
   variable is for a submodule, where the parent project's config should
   win over the submodule's git root.
2. **The local file:** ``./hpc-runner.toml`` in the current directory, when
   it is a different file from the project file. It overrides a job's
   config for one directory.

The local file may hold ``config_version``, ``[tools.*]`` and ``[types.*]``
only. Anything else there is an error that names the key and the file. The
scheduler, ``[defaults]``, the module paths and ``[schedulers.*]`` are the
site's setup, and are said once, in the project file.

A tool or type in both files is one entry. Its keys combine as layers do
(:ref:`layer-rules`), the local file after the project file.

``--config /path/to/file.toml`` reads that one file and nothing else.


Top-level sections
------------------

Every file starts with ``config_version = 1``. A file without it, or with a
version this hpc-runner does not know, is an error (the message gives the
one-line fix); ``hpc config init`` writes it for you. The remaining
top-level keys are:

- ``scheduler = "sge"``: the scheduler this installation submits to
  (``sge``, ``slurm``, or ``local`` to run jobs on this machine). There is one per
  installation and hpc-runner never guesses it: without this key, or
  ``HPC_SCHEDULER`` in the environment (which overrides it), every command
  that needs a scheduler fails with an error saying where to set it.
- ``module_init_script = "/path/init.sh"``: the script every job sources to
  define ``module`` on the compute nodes. Empty, or absent, tries the
  standard Environment Modules locations (``/etc/profile.d/modules.sh``,
  ``/usr/share/Modules/init/bash``, ``/etc/modules/init/bash``). It
  describes the nodes, not a scheduler, so it is set once here; a copy
  under ``[schedulers.<name>]`` is an error.
- ``[defaults]``: baseline job settings applied to all jobs
- ``[tools.<name>]``: settings keyed by tool name (e.g., ``python``, ``make``).
  A tool entry can list the ``commands`` it matches (see :ref:`tool-commands`)
  and can contain an ``[tools.<name>.options]`` sub-table for argument-specific
  specialisation (see :ref:`tool-option-specialisation`).
- ``[types.<name>]``: named flows that compose tools (see :ref:`job-types`)
- ``[schedulers.<name>]``: site facts for one scheduler (e.g., SGE resource
  names, how a Slurm site wants memory asked for)

``[defaults]``, ``[tools.*]``, ``[tools.*.options.*]`` and ``[types.*]`` are
*job layers*: they all accept the same job settings, listed in
:ref:`job-settings`. ``modules_path`` and ``modules_path_append`` are the
exception: they are set in ``[defaults]`` only. In addition ``options`` and
``commands`` are allowed only under ``[tools.<name>]`` and ``tools`` only
under ``[types.<name>]``.


.. _job-settings:

Job settings reference
----------------------

Every key a job layer accepts. They are the fields of ``JobSpec`` (the
Python API) except the per-invocation ones, ``command``, ``type``,
``dependencies`` and ``array``, which only the command line or the spec
can set. *Layers* says how a key combines across layers
(:ref:`layer-rules`): **adds** (each layer adds to the earlier ones) or
**overrides** (a layer that sets the key replaces the earlier value, whole).
Keys marked *expanded* have ``$VAR`` resolved on the submit host
(:ref:`variable-expansion`).

.. list-table::
   :header-rows: 1
   :widths: 18 18 10 54

   * - Key
     - Type
     - Layers
     - Meaning
   * - ``name``
     - string
     - overrides
     - Job name; expanded. Default ``<user>_<first command word>``.
   * - ``cpu``
     - integer
     - overrides
     - CPUs / slots (SGE ``-pe <parallel_environment> <cpu>``; Slurm
       ``--cpus-per-task=<cpu> --ntasks=1``).
   * - ``mem``
     - string
     - overrides
     - Memory for the whole job (:ref:`memory-and-time`); expanded.
   * - ``time``
     - string
     - overrides
     - Run-time limit (:ref:`memory-and-time`); expanded.
   * - ``queue``
     - string
     - overrides
     - Queue / partition (SGE ``-q``, Slurm ``--partition``); expanded.
   * - ``project``
     - string
     - overrides
     - Project / account charged (SGE ``-P``, Slurm ``--account``);
       expanded.
   * - ``stdout``
     - string
     - overrides
     - Output file pattern (:ref:`output-files`); expanded. Default
       ``${hpc.job_name}.${hpc.job_id}.out``.
   * - ``stderr``
     - string
     - overrides
     - Separate stderr pattern; unset means merged into ``stdout``; expanded.
   * - ``inherit_env``
     - boolean
     - overrides
     - Inherit the submitting shell's environment (SGE ``-V``, Slurm
       ``--export=ALL``). Default ``true``; see :ref:`environment`.
   * - ``workdir``
     - string
     - overrides
     - Working directory, relative to the submit directory; expanded.
       Default: the submit directory.
   * - ``venv``
     - path or ``false``
     - overrides
     - Virtualenv to activate; unset captures ``$VIRTUAL_ENV``, ``false``
       means none (:ref:`environment`); expanded.
   * - ``interactive``
     - boolean
     - overrides
     - Run attached and wait (SGE ``qrsh``, Slurm ``srun``). Default
       ``false``; ``hpc run`` sets it unless ``--batch`` or ``--array`` is
       given.
   * - ``env_vars``
     - table of strings
     - overrides
     - Variables set in the job; expanded.
   * - ``env_prepend``
     - table of strings
     - overrides
     - Values prepended to variables, ``:``-separated; expanded.
   * - ``env_append``
     - table of strings
     - overrides
     - Values appended to variables, ``:``-separated; expanded.
   * - ``env_unset``
     - list of names
     - overrides
     - Variables unset in the job.
   * - ``env_passthrough``
     - list of names
     - overrides
     - Submit-host variables passed when ``inherit_env = false``.
   * - ``modules``
     - list of strings
     - adds
     - ``module load`` each, after ``module purge``; expanded.
   * - ``modules_path``
     - list of strings
     - ``[defaults]`` only
     - ``module use`` each (searched first); expanded. A ``JobSpec`` value
       replaces the one in ``[defaults]``.
   * - ``modules_path_append``
     - list of strings
     - ``[defaults]`` only
     - ``module use --append`` each (searched last); expanded. A
       ``JobSpec`` value replaces the one in ``[defaults]``.
   * - ``licenses``
     - table of ``name = count``
     - adds
     - Licences the job needs, each with a count (a whole number, 1 or
       more). SGE ``-l name=count``, Slurm ``-L name:count``.
   * - ``devices``
     - table of ``name = count``
     - adds
     - Devices the job needs (a GPU, ...), each with a count (a whole
       number, 1 or more). SGE ``-l name=count``, Slurm
       ``--gres=name:count``.
   * - ``features``
     - list of strings
     - overrides
     - Node features the job needs, by name. SGE ``-l name=1``, Slurm
       ``-C name``.
   * - ``scheduler_args``
     - list of strings
     - overrides
     - Raw arguments for the scheduler (:ref:`scheduler-args`); expanded.

Structural keys:

- ``commands`` (``[tools.<name>]`` only): the command names the entry
  matches instead of its own name, see :ref:`tool-commands`.
- ``options`` (``[tools.<name>]`` only): argument-specific sub-entries, see
  :ref:`tool-option-specialisation`.
- ``tools`` (``[types.<name>]`` only): the tools a type pulls in, in order,
  see :ref:`job-types`.


.. _scheduler-settings:

Scheduler settings reference
----------------------------

``[schedulers.<name>]`` holds site facts for one scheduler. Each backend
declares the keys it reads; any other key, or a value of the wrong type, is
an error. A section for a scheduler this hpc-runner does not have (for
example ``[schedulers.pbs]`` today) is a warning and is not checked.

``[schedulers.sge]``:

.. list-table::
   :header-rows: 1
   :widths: 24 12 14 50

   * - Key
     - Type
     - Default
     - Meaning
   * - ``parallel_environment``
     - string
     - ``"smp"``
     - PE used for ``cpu``: ``-pe <parallel_environment> <cpu>``.
   * - ``memory_resource``
     - string
     - ``"mem_free"``
     - Resource ``mem`` is requested as (``h_vmem``, ``virtual_free``, ...).
   * - ``time_resource``
     - string
     - ``"h_rt"``
     - Resource ``time`` is requested as.
   * - ``memory_per``
     - ``"job"`` or ``"slot"``
     - ``"job"``
     - ``"job"``: ``mem`` is requested as given; ``"slot"``: the memory
       resource is a per-slot consumable, so ``mem`` is divided by ``cpu``.
   * - ``emulate_afterok``
     - boolean
     - ``true``
     - Emulate afterok/afternotok with exit sentinels (:doc:`sge`); with
       ``false`` only afterany dependencies are accepted.
   * - ``sentinel_ttl_days``
     - integer, 1 or more
     - ``30``
     - Exit sentinels older than this are removed when a scheduler is
       created. Raise it if dependency chains wait longer than a month.

``[schedulers.slurm]`` (:doc:`slurm`):

.. list-table::
   :header-rows: 1
   :widths: 24 12 14 50

   * - Key
     - Type
     - Default
     - Meaning
   * - ``memory_per``
     - ``"job"`` or ``"cpu"``
     - ``"job"``
     - ``"job"``: ``mem`` is requested as given, with ``--mem``; ``"cpu"``:
       ``mem`` is divided by ``cpu``, rounded up to the megabyte, and
       requested with ``--mem-per-cpu``.
   * - ``kill_invalid_dependents``
     - boolean
     - ``true``
     - Send ``--kill-on-invalid-dep=yes`` with a job's dependencies; with
       ``false`` it is not sent.

``[schedulers.local]`` (runs jobs as local subprocesses) has no settings.
The module init script is the top-level ``module_init_script`` key, shared
by every scheduler.

.. _trace-settings:

Trace settings
--------------

``[trace]`` turns on a recording of what every ``hpc`` command does, as
JSON files under one directory per day. It is the same recording ``hpc
--trace DIR`` makes for a single command (:doc:`cli`), kept across every
command while the setting is on:

- ``<time>-<pid>-hpc.json``, one per ``hpc`` command: the command line
  (``argv``), the config files read with their text (``config``), the
  whole environment, one variable per line (``env``), what ``hpc`` printed (``stdout``,
  ``stderr``, without colour codes), the error it raised (``error``) and
  its ``exit_code``.
- ``<time>-<pid>-<command>.json``, one per scheduler command (``qsub``,
  ``qstat``, ``qacct``, ``sbatch``, ``squeue``, ...): ``argv``, ``returncode``, ``stdout``,
  ``stderr``. The record of the command that submitted a job also has
  ``script``: the path and full text of the job script, which otherwise
  deletes itself.

The process id in the file name ties a command to the scheduler commands
it ran. The output of an interactive job goes straight to the terminal and
is not recorded. ``hpc monitor`` is never traced, with the flag or the
setting: a trace is for debugging what a command submitted or read. Nothing is
scrubbed: the records hold real user, host and job names and every
environment variable, which may include a token or password. Before a
record leaves your site, delete or edit the lines that must not.

.. list-table::
   :header-rows: 1
   :widths: 22 12 16 50

   * - Key
     - Type
     - Default
     - Meaning
   * - ``enabled``
     - bool
     - ``false``
     - Record every ``hpc`` command.
   * - ``dir``
     - string
     - ``~/.cache/hpc-runner/trace``
     - Where the records go (``~`` is expanded). Private to you: nothing is
       scrubbed.

.. code-block:: toml

   [trace]
   enabled = true
   dir = "~/.cache/hpc-runner/trace"

Nothing removes old records: every command adds files for as long as the
setting is on, and says so in a closing line on stderr (``trace: 3 records
written to ...``). Turn it off when you have what you need, and delete the day
directories you no longer want.

The local scheduler runs no scheduler command and is not traced.


Validation
----------

Config is checked on every load, so ``hpc run`` refuses a broken config
before it touches the scheduler. An unknown key or a value of the wrong
type is an error naming the file, line, section and key, with a "did you
mean" suggestion for typos; every problem in the config is reported at once:

.. code-block:: text

   error: hpc-runner.toml:5: [defaults] cpu: expected an integer, got string "4"
   error: hpc-runner.toml:9: [types.sim] modles: unknown key; did you mean 'modules'?

``[schedulers.<name>]`` keys are checked against the keys that scheduler
declares. A section for a scheduler this hpc-runner does not have is a
warning, not an error, so a site config can lead support.

Two commands help:

- ``hpc config check [PATH]`` validates ``PATH`` (or the discovered files),
  prints every error and warning, and exits 1 if there is any error.
- ``hpc config resolve [-t TYPE] COMMAND...`` prints the job ``COMMAND``
  would get, one row per field (one per module, licence and device, which
  add up across layers) with the layer that supplied it: ``defaults``, ``tools.<name>``, ``tools.<name>.options.<key>``,
  ``types.<name>``, ``spec``, ``package`` (built-in default) or ``host``
  (captured from the submitting shell).


.. _how-jobs-pick-up-config:

How jobs pick up config
-----------------------

``resolve(spec, config, host)`` applies a fixed chain of layers, least
specific first, then the spec (the CLI options or the fields set on the
``JobSpec``):

.. code-block:: text

   no type:      defaults -> tools.<tool> -> tools.<tool>.options.<first match> -> spec
   with -t T:    defaults -> tools.<each of types.T.tools, in order> -> types.T -> spec

``<tool>`` is the entry whose name, or whose ``commands`` list
(:ref:`tool-commands`), matches the command's first word with any path
stripped. With a type named, the tool inferred from the command is **not**
consulted, and the ``options`` of the type's tools are not applied: the type
is the whole specification.

The command line is a layer like any other. Options repeated on one command
line combine with each other first: ``-E A=1 -E B=2`` is one ``env_vars``
table with both.


.. _layer-rules:

How layers combine
------------------

.. list-table::
   :header-rows: 1
   :widths: 30 40 30

   * - Keys
     - Rule
     - Why
   * - ``modules``, ``licenses``, ``devices``
     - Add up across layers. Modules append; a module already listed is not
       listed again. A licence or device named again takes the later count.
     - The job consumes them. A type that pulls in two tools needs both
       tools' modules and licences.
   * - Every other key, including ``features``, ``env_vars``,
       ``env_prepend``, ``env_append``, ``env_unset``, ``env_passthrough``
       and ``scheduler_args``
     - A layer that sets the key replaces what earlier layers set, whole. A
       layer that does not set it inherits it.
     - They say where and how the job runs.
   * - ``modules_path``, ``modules_path_append``
     - Set in ``[defaults]`` only. In a tool or a type they are an unknown
       key.
     - They say where the site's modules are read from, once.

.. code-block:: toml

   [defaults]
   modules = ["gcc/12"]
   env_vars = { TMPDIR = "/scratch" }
   features = ["bigmem"]

   [tools.xrun]
   modules = ["xcelium/23"]             # the job loads gcc/12, then xcelium/23
   licenses = { xcelium = 1 }
   env_vars = { XRUN_OPTS = "-64bit" }  # the only variable set: TMPDIR is gone
   features = []                        # cleared

- A layer cannot drop a module, licence or device it inherited.
  ``[defaults]`` holds only what every job wants.
- ``[]`` or ``{}`` on a key that overrides clears it. On ``modules``,
  ``licenses`` or ``devices`` it adds nothing.
- On the command line, ``--license`` and ``--device`` add to the config's.
  ``-E``, ``--feature`` and ``-S`` replace the config's ``env_vars``,
  ``features`` and ``scheduler_args``.
- A ``JobSpec`` follows the same rules. Its ``modules_path`` and
  ``modules_path_append`` replace the ones in ``[defaults]``; they are there
  for a Python caller with no config file.


.. _job-types:

Types compose tools
-------------------

A type defines a flow and everything it needs. Rather than restating tool
settings, it pulls tools in by name, in order, then applies its own keys:

.. code-block:: toml

   [tools.xrun]
   modules = ["xcelium/23.09"]
   licenses = { xcelium_lic = 1 }

   [tools.slang]
   modules = ["slang/0.9"]

   [types.lint]
   tools = ["xrun", "slang"]   # pulled in, in this order, then the keys below
   cpu = 2

``hpc run -t lint make lint`` loads ``xcelium/23.09`` and ``slang/0.9``,
requests the licence and two CPUs. One type per job; a combined flow is its
own named type. References are one level deep (tools cannot list tools,
types cannot list types); a type naming an undefined tool, or a ``-t``
naming an undefined type, is an error.


.. _licenses-devices-features:

Licences, devices and features
------------------------------

Three keys say what a job needs from the cluster by name, on any job layer:

.. code-block:: toml

   [tools.xcelium]
   licenses = { xcelium = 1 }
   devices  = { gpu = 2 }
   features = ["bigmem"]

- ``licenses`` and ``devices`` are tables of ``name = count``. A count is a
  whole number, 1 or more; anything else is an error before submission.
  They add up across layers: a later layer setting a name replaces that
  name's count and leaves the other names alone.
- ``features`` is a list of names. A layer that sets it replaces the
  earlier list (:ref:`layer-rules`).
- A name is only the name the site gave the thing. A GPU type, a licence
  server, scratch space, a host name or a memory limit by its site name is
  one scheduler's syntax and goes in :ref:`scheduler_args <scheduler-args>`.

On the command line: ``--license NAME=N``, ``--device NAME=N`` and
``--feature NAME``, each repeatable. A licence or device given there is
added to the config's, replacing the count of the same name; the same name
given twice keeps the last. ``--feature`` replaces the config's features.

Interactive and batch jobs carry all three alike. On SGE each is a ``-l``
request (:doc:`sge`). On Slurm they are ``-L``, ``--gres`` and ``-C``
(:doc:`slurm`).


.. _scheduler-args:

Scheduler-specific arguments
----------------------------

``scheduler_args`` is an escape hatch for flags hpc-runner does not model
(``-binding``, ``-masterq``, ...). It may appear on any job layer, as a
plain list of arguments. A config file is for one scheduler, so the list is
written in that scheduler's own flags:

.. code-block:: toml

   [defaults]
   scheduler_args = ["-binding", "linear:1"]

   [tools.sim]
   scheduler_args = ["-binding", "linear:1", "-masterq", "fast.q"]

A layer that sets ``scheduler_args`` replaces the earlier list
(:ref:`layer-rules`), so ``[tools.sim]`` above repeats the flag it wants to
keep. Repeated flags (``-l a=1 -l b=2``) are kept as written. The examples
here are SGE's flags; on Slurm the list holds ``sbatch`` flags
(``scheduler_args = ["--nice=500"]``).

On the command line, raw arguments go in ``-S``/``--scheduler-args``
(``hpc run -S '-l gpu=2' make sim``). They replace the config's list.

An option means the same on every scheduler (see :ref:`job-rules` for the
few that a job kind or the local scheduler does not take). Anything that is
one scheduler's own idea goes here instead. Job priority is an example (each
scheduler has its own scale), as is an MPI job's node and task count:

.. code-block:: toml

   [types.low]
   scheduler_args = ["-p", "-500"]      # SGE; on Slurm ["--nice=500"]

``[schedulers.<name>]`` holds site facts only (PE name, resource names,
module init script).


.. _job-rules:

Rules for every job
-------------------

hpc-runner has a view on how jobs are run. Where it does not pass an option
on, that is a rule, and it holds on every scheduler:

.. list-table::
   :header-rows: 1
   :widths: 45 55

   * - Rule
     - Why
   * - An interactive job's ``stdout`` and ``stderr`` are ignored.
     - Its output is your terminal. A file named in config for the batch
       case does not apply.
   * - An interactive job cannot be an array. The job is refused.
     - No scheduler runs an array from its interactive command.
   * - An interactive job cannot have dependencies. The job is refused.
     - An interactive job runs now, at your terminal. A job that waits for
       another belongs in the queue: submit it with ``--batch``.
   * - A scheduler that cannot honour an option a job sets refuses the job,
       and the error names the option.
     - A job never runs with less than it asked for without saying so.

The local scheduler (``scheduler = "local"``) runs the command on this
machine. It is there to give a tool its modules and environment without a
cluster:

.. list-table::
   :header-rows: 1
   :widths: 45 55

   * - Rule
     - Why
   * - ``cpu`` is given to the job as ``${hpc.cpus}``. Nothing holds the
       job to that many cores.
     - It is for your tools and flows to use the count they asked for.
   * - ``mem``, ``time``, ``queue``, ``project``, ``licenses``, ``devices``
       and ``features`` are ignored.
     - Nothing here reserves or limits them, and the same config runs
       unchanged on the cluster.
   * - ``scheduler_args`` is ignored.
     - The arguments are the cluster scheduler's own flags. There is no
       scheduler here to pass them to.
   * - Arrays and dependencies are refused.
     - There is no scheduler to fan a job out or hold it.

Slurm mapping notes
-------------------

- ``queue`` maps to ``--partition`` and ``project`` to ``--account``.
- ``cpu`` maps to ``--cpus-per-task=<cpu> --ntasks=1``: one task with that
  many CPUs.
- ``mem`` maps to ``--mem`` in megabytes. It is per job;
  ``memory_per = "cpu"`` divides it by ``cpu`` and sends ``--mem-per-cpu``.
- ``time`` maps to ``--time=D-HH:MM:SS``.
- ``stdout``/``stderr`` map to ``--output``/``--error``; without ``stderr``
  no ``--error`` is sent.

See :doc:`slurm` for the whole table.

SGE mapping notes
-----------------

- ``queue`` maps to SGE ``qsub -q <queue>``.
- ``cpu`` maps to SGE parallel environment slots: ``qsub -pe <parallel_environment> <cpu>``.
- ``mem`` and ``time`` map to SGE hard resources via ``-l <resource>=<value>``, where the
  resource names are configured under ``[schedulers.sge]``. ``mem`` is per job
  and requested as given; ``memory_per = "slot"`` divides it by ``cpu`` for a
  site whose resource is a per-slot consumable (see :doc:`sge`).
- ``stdout``/``stderr`` map to ``-o``/``-e``; without ``stderr``, ``-j y``.


.. _output-files:

Output files
------------

hpc-runner always names a batch job's output file itself, never leaving it
to the scheduler's default, so the path is known when the job is submitted
and ``JobResult.stdout_path`` / ``read_stdout()`` work on every scheduler.

- **Default:** ``stdout = "${hpc.job_name}.${hpc.job_id}.out"``, the same
  on every scheduler. A site that prefers SGE's native ``*.o<id>`` names
  sets its own pattern.
- **Patterns** use the ``${hpc.*}`` runtime values
  (:ref:`variable-expansion`) that the scheduler knows before the job
  starts: ``${hpc.job_name}``, ``${hpc.job_id}``, ``${hpc.task_id}`` and
  ``${hpc.host}``, and only in the final path component: the directory is
  created before submission, so ``logs/${hpc.job_id}/x.out`` is an error
  (SGE puts a job whose output directory is missing in the error
  state). ``${hpc.cpus}`` and
  ``${hpc.tmpdir}`` are an error anywhere in an output path. On SGE the
  pattern becomes ``<workdir>/$JOB_NAME.$JOB_ID.out`` and SGE fills it in;
  on Slurm it becomes ``<workdir>/%x.%j.out``.
- **Arrays:** the default is
  ``${hpc.job_name}.${hpc.job_id}.${hpc.task_id}.out`` (``name.123.4.out``).
  A pattern of your own without ``${hpc.task_id}`` gets it before the last
  extension, so tasks never write the same file: ``run.log`` gives
  ``run.4.log``; a name with no extension gets ``.4`` appended. The same
  applies to ``stderr``.
- **Relative paths** are relative to the job's working directory
  (``workdir``, else the directory you submit from). The directory part is
  created on the submit host before submission.
- **stderr** is merged into stdout unless a ``stderr`` pattern is set, which
  turns merging off.
- ``${hpc.job_name}`` is the name as the scheduler submits it: on every
  scheduler whitespace, ``/`` and non-printable characters become ``_``
  (SGE and Slurm each add their own rules, see :doc:`sge` and
  :doc:`slurm`).
- An **empty** ``stdout`` or ``stderr`` is an error: omit the key to use the
  default (stdout) or to merge (stderr).
- **Interactive** jobs write to your terminal; ``stdout`` and ``stderr`` do
  not apply.

.. code-block:: toml

   [defaults]
   stdout = "logs/${hpc.job_name}.${hpc.job_id}.out"
   stderr = "logs/${hpc.job_name}.${hpc.job_id}.err"


.. _memory-and-time:

Memory and time
---------------

``mem`` and ``time`` are values hpc-runner owns: they are parsed once and
rendered in each scheduler's syntax, so a config file means the same thing
on every cluster. A value that does not parse is reported with its file and
line when the config is loaded.

``mem`` is the memory for the **whole job**, regardless of ``cpu``.
Accepted forms: ``16G``, ``16GB``, ``16GiB``, ``4096M``, ``1.5G``,
``512K``, ``2T``. Units are binary (``G`` is 2\ :sup:`30` bytes, as SGE
reads it) and case-insensitive; a bare integer is bytes.

``time`` accepts ``HH:MM:SS``, ``MM:SS``, ``D-HH:MM:SS``, ``D-HH:MM``,
``D-HH``, and units ``90s``, ``30m``, ``4h``, ``2d`` or a compound such as
``1h30m`` or ``1d2h``. **A bare integer is an error** ("ambiguous: use 90s
or 90m"): SGE reads ``90`` as seconds and Slurm as minutes.

Values are expanded (:ref:`variable-expansion`) before they are parsed.
Licences, devices and node features have their own keys
(:ref:`licenses-devices-features`). Any other site request goes through
:ref:`scheduler_args <scheduler-args>` untouched.


.. _variable-expansion:

Variables in config values
--------------------------

One rule: **every** ``$VAR`` **in config is resolved where you submit from;
runtime values come from** ``${hpc.*}`` **; anything else is shell in your
script or Makefile.**

``$VAR`` and ``${VAR}`` are looked up on the submit host when the job is
resolved. A variable that is not defined is an error, never a literal ``$``
that silently reaches the job. This applies to every value that reaches the
job script or the scheduler: ``env_vars``, ``env_prepend``, ``env_append``,
``modules``, ``modules_path``, ``modules_path_append``, ``venv``,
``stdout``, ``stderr``, ``workdir``, ``queue``, ``name``, ``project``,
``mem``, ``time`` and ``scheduler_args``. The command itself is not
expanded: its ``$VAR``, ``${VAR}`` and ``$$`` are left for the job's shell.
Only ``${hpc.*}`` is read in a command (see below).

**Runtime values.** Values only the scheduler knows when the job starts use
the ``${hpc.*}`` namespace, which each scheduler renders in its own terms:

.. list-table::
   :header-rows: 1

   * - Name
     - Meaning
     - SGE
     - Slurm
     - local
   * - ``${hpc.cpus}``
     - slots / CPUs granted
     - ``$NSLOTS``
     - ``$SLURM_CPUS_PER_TASK``
     - ``cpu`` (or 1)
   * - ``${hpc.job_id}``
     - job id
     - ``$JOB_ID``
     - ``${SLURM_ARRAY_JOB_ID:-$SLURM_JOB_ID}``
     - the local id
   * - ``${hpc.task_id}``
     - array task index
     - ``$SGE_TASK_ID``
     - ``$SLURM_ARRAY_TASK_ID``
     - empty
   * - ``${hpc.tmpdir}``
     - the job's ``$TMPDIR``: a job scratch directory on SGE; on Slurm
       see :doc:`slurm`
     - ``$TMPDIR``
     - ``$TMPDIR``
     - ``$TMPDIR``
   * - ``${hpc.job_name}``
     - job name
     - ``$JOB_NAME``
     - ``$SLURM_JOB_NAME``
     - the job name
   * - ``${hpc.host}``
     - execution host
     - ``$HOSTNAME``
     - ``$SLURMD_NODENAME``
     - ``$HOSTNAME``

Any other ``${hpc.x}`` is an error. Writing a scheduler variable directly,
such as ``-j$NSLOTS``, is an undefined variable on the submit host; the
error suggests the ``${hpc.*}`` equivalent:

.. code-block:: toml

   [tools.make]
   env_vars = { MAKEFLAGS = "-j${hpc.cpus}" }

The command takes ``${hpc.*}`` too, and nothing else in it is expanded.
Single quotes keep the submitting shell from reading the reference first:

.. code-block:: bash

   hpc run --cpu 8 cargo build -j '${hpc.cpus}'

Each word of the command reaches the job as one argument, with the
reference inside it replaced by the value: ``'-j${hpc.cpus}'`` arrives as
``-j8``. An unknown ``${hpc.x}`` in the command is an error.

**Resolver variables.** These are always defined:

- ``${CONFIG_DIR}``: the directory of the config file that set the value
  (so a project can say ``venv = "${CONFIG_DIR}/.venv"``). It exists only
  for values from a config file; in a value set on the CLI or a ``JobSpec``
  it is an error.
- ``${GIT_ROOT}``: the git repository containing the submit directory (an
  error outside one).
- ``${CWD}``: the submit directory.
- ``${USER}``: the submitting user.

They take precedence over environment variables of the same name.

**Literal dollars.** ``$$`` is a literal ``$`` (as in Make and docker
compose): ``"$$HOME"`` is the text ``$HOME``. A value that depends on a
module (``XCELIUM_HOME=$CDS_INST_DIR`` after the load) is shell logic and
belongs in the recipe or script.


.. _environment:

Environment
-----------

**The reproducibility contract.** By default a job inherits the submitting
shell's environment (``inherit_env = true``; SGE ``-V``, Slurm
``--export=ALL``) and its script
then always runs ``module purge`` followed by the configured ``module use``
and ``module load`` commands. Tool versions come from modules: because the
module system owns the ``PATH`` and library entries of what it loaded, the
purge-and-load guarantees that every tool the config names is the version
the config names, whatever the submitting shell had loaded. That is the
whole contract. Everything else in the inherited environment (session
variables, ``DISPLAY``, a tool a site put on ``PATH`` outside the module
system) is carried through unchanged; the fix for the last of these is to
put the tool in a module, not to reset variables in hpc-runner.

**Strict mode.** ``inherit_env = false`` drops ``-V`` (Slurm:
``--export=NONE``): the job starts from the scheduler's clean environment.
``env_passthrough`` names submit-host variables to pass explicitly (SGE
``-v NAME=value``, Slurm ``--export=NAME=value,...``); each must be set
where you submit, or resolution fails. On Slurm a value with a ``,`` in it
is refused (:doc:`slurm`). With ``inherit_env = true``,
``env_passthrough`` is ignored, since the variables arrive anyway.

.. code-block:: toml

   [types.regress]
   inherit_env = false
   env_passthrough = ["DISPLAY", "LM_LICENSE_FILE"]

**Setting variables.** Four keys shape the job's environment, and render in
a fixed order after the modules and venv, before the command:

1. ``env_unset``: a list of variables to remove (for example proxies,
   whether inherited or set by a module).
2. ``env_prepend``: ``NAME = "value"`` puts ``value`` in front of the
   current value, ``:``-separated.
3. ``env_append``: the same, at the end.
4. ``env_vars``: ``NAME = "value"`` sets the variable outright.

The separator is always ``:``. Names must be valid shell identifiers and
values are shell-quoted, so they arrive exactly as written. Setting a
variable in ``env_vars`` and prepending or appending to it in the same
section is an error; across layers it is allowed (the set wins, since it
renders last). Each of the four keys overrides: a layer that sets
``env_vars`` replaces the whole table from the earlier layers
(:ref:`layer-rules`).

.. code-block:: toml

   [tools.myapp.env_vars]
   LICENSE_FILE = "/opt/licenses/myapp.lic"

   [tools.myapp.env_prepend]
   PATH = "${CONFIG_DIR}/bin"

   [types.gpu.env_append]
   LD_LIBRARY_PATH = "/opt/cuda/lib64"

**Virtual environments.** ``venv`` has three states:

- unset (the default): the submitting shell's ``$VIRTUAL_ENV`` is
  activated in the job, if there is one;
- a path: that venv (``venv = "${CONFIG_DIR}/.venv"``);
- ``false``: no venv, whatever the submitting shell has active.

A later layer wins, so ``[types.lint] venv = false`` beats the captured
one. The venv's ``bin/activate`` must exist on the submit host (the
filesystem is assumed shared), which catches a stale ``VIRTUAL_ENV``.
``hpc config resolve`` reports where the value came from
(``captured from VIRTUAL_ENV``, ``tools.python``, ``types.lint (disabled)``)
and ``hpc run --dry-run`` shows ``venv: <path>`` or ``venv: none``.

**Module output.** Module commands are quiet when they succeed and loud
when they fail: each one's output is kept aside and printed, with a
``hpc-runner: module load X failed`` line, only if it fails, which stops
the job. ``hpc --verbose run`` shows module output as it happens. The
script also exports ``MODULES_VERBOSITY=concise``, which makes Environment
Modules 4.3 and later terser and is ignored by older versions. The module
init script itself is the top-level ``module_init_script`` key:

.. code-block:: toml

   module_init_script = ""       # site module init; empty tries the standard locations


.. _tool-commands:

One tool, several commands
--------------------------

A tool entry matches the command whose first word is its name. A suite that
installs many executables would need one copy of the entry per command, so
an entry can instead list the ``commands`` it stands for:

.. code-block:: toml

   [tools.xcelium]
   commands = ["xrun", "xmvlog", "xmelab", "xmsim", "simvision"]
   modules = ["xcelium/23.09"]
   licenses = { xcelium_lic = 1 }

``hpc run xmvlog -sv top.sv`` and ``hpc run xrun -f top.f`` both get the
``tools.xcelium`` layer, and ``hpc config resolve xmvlog`` reports it under
that name. The rules:

- An entry with ``commands`` matches exactly those names. Its own name is a
  label (what a type's ``tools`` list refers to) and is matched only if it
  is listed too.
- An entry without ``commands`` matches its own name, as before.
- A command name belongs to one entry. Two entries listing the same name,
  or an entry listing a name that is also a ``[tools.<name>]`` section, is a
  config error reported at the ``commands`` line.
- ``options`` sub-entries apply under every listed command. A command that
  needs different settings is its own entry or a type.
- Names are bare commands (no path, no whitespace); patterns such as ``x*``
  are not accepted.


.. _tool-option-specialisation:

Tool option specialisation
--------------------------

A tool entry can define ``[tools.<name>.options]`` sub-entries that match
against arguments in the command string.  When matched, the option entry is
one more layer after the tool's (:ref:`layer-rules`).  This allows a single
tool to have different environments depending on how it is invoked.

**TOML syntax:**

.. code-block:: toml

   [tools.fusesoc]
   cpu = 2
   modules = ["fusesoc/2.0"]

   [tools.fusesoc.options."--tool slang"]
   mem = "16G"
   modules = ["slang/0.9"]          # added → ["fusesoc/2.0", "slang/0.9"]

   [tools.fusesoc.options."--tool verilator"]
   cpu = 4
   modules = ["verilator/5.0"]      # added → ["fusesoc/2.0", "verilator/5.0"]

   [tools.mytool]
   cpu = 2

   [tools.mytool.options."--gui"]
   queue = "interactive.q"

**Matching rules:**

1. The command arguments (everything after the tool name) are tokenised.
2. ``--flag=value`` is normalised to ``--flag value`` before matching (so
   ``--tool=slang`` and ``--tool slang`` are equivalent).
3. Each option key is tokenised the same way and checked for a **contiguous
   subsequence** match against the command tokens.
4. **First match wins** — keys are checked in TOML insertion order, so place
   more specific patterns before less specific ones.
5. No partial value matching: ``"--tool slang"`` does **not** match
   ``--tool slang-lint``.
6. Short flags (e.g. ``"-g"``) match literally — there is no automatic
   expansion of short to long options.

**Usage:**

.. code-block:: bash

   hpc run fusesoc run --tool slang core:v:n     # mem=16G, modules includes slang/0.9
   hpc run fusesoc run --tool=slang core:v:n     # same (= normalised)
   hpc run fusesoc run --tool verilator core     # cpu=4, modules includes verilator/5.0
   hpc run fusesoc run core:v:n                  # no match — base fusesoc config only


Example: fully populated config (standalone file)
-------------------------------------------------

Save as ``hpc-runner.toml`` at your git root:

.. code-block:: toml

   config_version = 1
   scheduler = "sge"
   module_init_script = ""     # script sourced to define `module`; empty tries the standard locations

   [defaults]
   name = "job"
   cpu = 1
   mem = "4G"
   time = "1:00:00"
   queue = "batch.q"           # SGE default queue
   workdir = "."
   inherit_env = true

   stdout = "${hpc.job_name}.${hpc.job_id}.out"   # the default
   # stderr = "${hpc.job_name}.${hpc.job_id}.err"  # unset: merged into stdout

   modules = ["gcc/12.2", "python/3.11"]
   modules_path = []           # module use <path>: searched before the site paths
   modules_path_append = []    # module use --append <path>: searched after them

   scheduler_args = ["-l", "scratch=20G"]      # a site request with no option

   env_unset = ["https_proxy", "http_proxy"]   # unset in the job script
   env_passthrough = ["DISPLAY"]               # used only with inherit_env = false

   # Environment variable manipulation ($VAR resolved on the submit host)
   [defaults.env_vars]
   # MY_VAR = "value"            # set variable to an explicit value

   [defaults.env_prepend]
   # PATH = "/opt/mytools/bin"   # prepend to PATH-like variable

   [defaults.env_append]
   # LD_LIBRARY_PATH = "/opt/mytools/lib"  # append to PATH-like variable

   [schedulers.sge]
   parallel_environment = "smp"
   memory_resource = "mem_free"
   time_resource = "h_rt"
   memory_per = "job"          # mem is per job; "slot" divides it by cpu for a per-slot consumable
   sentinel_ttl_days = 30      # exit sentinels older than this are swept

   [trace]
   enabled = false             # record every scheduler call (see Trace settings)

   [tools.python]
   cpu = 4
   mem = "16G"
   time = "4:00:00"
   queue = "short.q"
   modules = ["python/3.11"]            # added to the modules in [defaults]
   scheduler_args = ["-l", "tmpfs=8G"]  # replaces the list in [defaults]

   [tools.fusesoc]
   modules = ["fusesoc/2.0"]

   [tools.fusesoc.options."--tool slang"]
   mem = "16G"
   modules = ["slang/0.9"]

   [tools.fusesoc.options."--tool verilator"]
   cpu = 4
   modules = ["verilator/5.0"]

   [tools.xterm]
   queue = "interactive.q"
   time = "8:00:00"
   cpu = 2
   mem = "8G"

   [types.gpu]
   tools = ["python"]
   queue = "gpu.q"
   cpu = 8
   mem = "64G"
   time = "12:00:00"
   devices = { gpu = 1 }
