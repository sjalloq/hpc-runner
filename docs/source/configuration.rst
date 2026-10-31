:orphan:

Configuration
=============

This page describes the TOML configuration format used by **hpc-runner** and how
configuration values map to scheduler flags (with a focus on SGE).


Where configuration is loaded from
----------------------------------------

Configuration uses a two-level discovery model.  Both levels are deep-merged
(level 2 wins on conflicts).

1. **Project config (level 1):** ``$HPC_RUNNER_CONFIG`` if set and the file
   exists, otherwise ``<git root>/hpc-runner.toml``.  These are mutually
   exclusive — the env var exists for submodule scenarios where a parent
   project's config should win over the submodule's git root.
2. **Local override (level 2):** ``./hpc-runner.toml`` in the current working
   directory, when it resolves to a different file from level 1.  Allows
   subdirectories to override project defaults.

You can always bypass discovery by providing ``--config /path/to/file.toml``.


Top-level sections
------------------

Every file starts with ``config_version = 1``. A file without it, or with a
version this hpc-runner does not know, is an error (the message gives the
one-line fix); ``hpc config init`` writes it for you. The remaining
top-level keys are:

- ``extends = "path.toml"``: load another file first (relative to this
  file; variables expanded as in :ref:`variable-expansion`). The target must exist, and a cycle is an error.
- ``[defaults]``: baseline job settings applied to all jobs
- ``[tools.<name>]``: settings keyed by tool name (e.g., ``python``, ``make``).
  A tool entry can list the ``commands`` it matches (see :ref:`tool-commands`)
  and can contain an ``[tools.<name>.options]`` sub-table for argument-specific
  specialisation (see :ref:`tool-option-specialisation`).
- ``[types.<name>]``: named flows that compose tools (see :ref:`job-types`)
- ``[schedulers.<name>]``: site facts for one scheduler (e.g., SGE resource names)

``[defaults]``, ``[tools.*]``, ``[tools.*.options.*]`` and ``[types.*]`` are
*job layers*: they all accept the same job settings, listed in
:ref:`job-settings`. In addition ``options`` and ``commands`` are allowed
only under ``[tools.<name>]`` and ``tools`` only under ``[types.<name>]``.


.. _job-settings:

Job settings reference
----------------------

Every key a job layer accepts. They are the fields of ``JobSpec`` (the
Python API) except the per-invocation ones, ``command``, ``type``,
``dependencies`` and ``array``, which only the command line or the spec
can set. *Merge* says how a key combines across layers
(:ref:`how-jobs-pick-up-config`): **scalar** (most specific layer wins),
**table** (keys merge, later layer wins per key) or **list**
(:ref:`list-merging`). Keys marked *expanded* have ``$VAR`` resolved on
the submit host (:ref:`variable-expansion`).

.. list-table::
   :header-rows: 1
   :widths: 18 18 10 54

   * - Key
     - Type
     - Merge
     - Meaning
   * - ``name``
     - string
     - scalar
     - Job name; expanded. Default ``<user>_<first command word>``.
   * - ``cpu``
     - integer
     - scalar
     - CPUs / slots (SGE ``-pe <parallel_environment> <cpu>``).
   * - ``mem``
     - string
     - scalar
     - Memory for the whole job (:ref:`memory-and-time`); expanded.
   * - ``time``
     - string
     - scalar
     - Run-time limit (:ref:`memory-and-time`); expanded.
   * - ``queue``
     - string
     - scalar
     - Queue / partition (SGE ``-q``); expanded.
   * - ``priority``
     - integer
     - scalar
     - Scheduler priority (SGE ``-p``).
   * - ``project``
     - string
     - scalar
     - Project / account charged (SGE ``-P``); expanded.
   * - ``nodes``
     - integer
     - scalar
     - Number of nodes for MPI jobs (not used by SGE).
   * - ``tasks``
     - integer
     - scalar
     - Number of tasks for MPI jobs (not used by SGE); ``--ntasks`` on the CLI.
   * - ``stdout``
     - string
     - scalar
     - Output file pattern (:ref:`output-files`); expanded. Default
       ``${hpc.job_name}.${hpc.job_id}.out``.
   * - ``stderr``
     - string
     - scalar
     - Separate stderr pattern; unset means merged into ``stdout``; expanded.
   * - ``inherit_env``
     - boolean
     - scalar
     - Inherit the submitting shell's environment (SGE ``-V``). Default
       ``true``; see :ref:`environment`.
   * - ``workdir``
     - string
     - scalar
     - Working directory, relative to the submit directory; expanded.
       Default: the submit directory.
   * - ``shell``
     - string
     - scalar
     - Shell the job script runs under (SGE ``-S``). Default ``/bin/bash``.
   * - ``use_cwd``
     - boolean
     - scalar
     - Run in ``workdir`` (SGE ``-cwd``). Default ``true``.
   * - ``venv``
     - path or ``false``
     - scalar
     - Virtualenv to activate; unset captures ``$VIRTUAL_ENV``, ``false``
       means none (:ref:`environment`); expanded.
   * - ``interactive``
     - boolean
     - scalar
     - Run attached and wait (SGE ``qrsh``). Default ``false``; ``hpc run``
       sets it unless ``--batch`` or ``--array`` is given.
   * - ``env_vars``
     - table of strings
     - table
     - Variables set in the job; expanded.
   * - ``env_prepend``
     - table of strings
     - table
     - Values prepended to variables, ``:``-separated; expanded.
   * - ``env_append``
     - table of strings
     - table
     - Values appended to variables, ``:``-separated; expanded.
   * - ``env_unset``
     - list of names
     - list
     - Variables unset in the job.
   * - ``env_passthrough``
     - list of names
     - list
     - Submit-host variables passed when ``inherit_env = false``.
   * - ``modules``
     - list of strings
     - list
     - ``module load`` each, after ``module purge``; expanded.
   * - ``modules_path``
     - list of strings
     - list
     - ``module use`` each (searched first); expanded.
   * - ``modules_path_append``
     - list of strings
     - list
     - ``module use --append`` each (searched last); expanded.
   * - ``resources``
     - list of ``{ name, value }``
     - list (by ``name``)
     - Scheduler resources and licences (SGE ``-l name=value``).
   * - ``scheduler_args``
     - table of lists of strings
     - table of lists
     - Raw per-scheduler arguments (:ref:`scheduler-args`); expanded.

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
example ``[schedulers.slurm]`` today) is a warning and is not checked.

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
   * - ``module_init_script``
     - string
     - ``""``
     - Script sourced to define ``module``; empty tries the standard
       locations.
   * - ``module_system``
     - ``"modules"``, ``"lmod"`` or ``""``
     - ``""``
     - ``"modules"`` (Environment Modules 4.3+) exports
       ``MODULES_VERBOSITY=concise``.
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

``[schedulers.local]`` (runs jobs as local subprocesses) reads
``module_init_script`` and ``module_system`` with the same meaning.

.. _trace-settings:

Trace settings
--------------

``[trace]`` turns on a recording of every scheduler command hpc-runner
runs (``qsub``, ``qstat``, ``qacct``, ...), one JSON file per call with the
argv, exit status, stdout and stderr, under one directory per day. It is
the same recording ``hpc --trace DIR`` makes for a single invocation, kept
across every invocation while the setting is on, so a period of normal use
can be checked against the parsers afterwards with ``hpc trace compare``
and packed, scrubbed, for a bug report with ``hpc trace bundle`` (:doc:`cli`).

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
     - Record scheduler calls.
   * - ``dir``
     - string
     - ``~/.cache/hpc-runner/trace``
     - Where the records go (``~`` is expanded). Private to you: nothing is
       scrubbed until ``hpc trace bundle``.
   * - ``max_days``
     - integer
     - ``14``
     - Day directories older than this are removed when recording starts.
   * - ``dedupe``
     - bool
     - ``true``
     - Do not write a call whose argv and output equal the previous call
       with that argv. ``hpc monitor`` polls every few seconds and most
       snapshots are identical; the trace keeps the first of each run.

.. code-block:: toml

   [trace]
   enabled = true
   dir = "~/.cache/hpc-runner/trace"
   max_days = 14

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
  would get, one row per field (one per item for lists) with the layer that
  supplied it: ``defaults``, ``tools.<name>``, ``tools.<name>.options.<key>``,
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

- **Scalars** (``cpu``, ``queue``, ...): the most specific layer that sets
  one wins.
- **Tables** (``env_vars``, ``env_prepend``, ``env_append``,
  ``scheduler_args``): keys merge across layers; the later layer wins per key.
- **Lists**: see below.

When several files are loaded (``extends``, project and local override), a
section set in more than one file merges with the same rules, later file
over earlier.


.. _list-merging:

Lists: append, set, remove
--------------------------

``modules``, ``modules_path``, ``modules_path_append``, ``env_unset``,
``env_passthrough``, ``resources`` and each scheduler's ``scheduler_args`` list follow one rule
in every layer, including the spec:

- A plain list **appends** to what the earlier layers built, dropping
  duplicates (first-seen order kept).
- ``{ set = [...] }`` **replaces** the list outright.
- ``{ remove = [...], add = [...] }`` removes items, then appends ``add``
  (either key may be omitted).

.. code-block:: toml

   [defaults]
   modules = ["gcc/12", "python/3.11"]

   [tools.xrun]
   modules = ["xcelium/23"]                    # gcc/12, python/3.11, xcelium/23

   [tools.legacy]
   modules = { set = ["gcc/9"] }               # gcc/9 only

   [tools.pytorch]
   modules = { remove = ["python/3.11"], add = ["python/3.12"] }

``resources`` entries are matched by ``name``: a later layer setting
``{ name = "gpu", value = 2 }`` over an earlier ``gpu = 1`` requests one
``gpu=2``, and ``remove`` drops entries by name. ``scheduler_args`` lists
are raw arguments, so repeated flags (``-l a=1 -l b=2``) are kept.

The Python API mirrors the verbs: ``modules=[...]`` appends,
``modules=Set([...])`` replaces, ``modules=Edit(remove=[...], add=[...])``
edits (``from hpc_runner import Set, Edit``).


.. _job-types:

Types compose tools
-------------------

A type defines a flow and everything it needs. Rather than restating tool
settings, it pulls tools in by name, in order, then applies its own keys:

.. code-block:: toml

   [tools.xrun]
   modules = ["xcelium/23.09"]
   resources = [{ name = "xcelium_lic", value = 1 }]

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


.. _scheduler-args:

Scheduler-specific arguments
----------------------------

``scheduler_args`` is an escape hatch for flags hpc-runner does not model
(``-binding``, ``-masterq``, ``--constraint``, ...). It may appear on any job
layer, as a table of argument lists keyed by scheduler name; each list
merges per :ref:`list-merging`, and each scheduler uses only its own:

.. code-block:: toml

   [defaults.scheduler_args]
   sge = ["-binding", "linear:1"]

   [tools.sim]
   scheduler_args = { sge = ["-masterq", "fast.q"], slurm = ["--constraint=avx512"] }

On the command line, raw arguments go before ``--``
(``hpc run -q batch.q -- make sim``) and are appended after config's.
``[schedulers.<name>]`` holds site facts only (PE name, resource names,
module init script).


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
  (SGE discards output to a missing directory). ``${hpc.cpus}`` and
  ``${hpc.tmpdir}`` are an error anywhere in an output path. On SGE the
  pattern becomes ``<workdir>/$JOB_NAME.$JOB_ID.out`` and SGE fills it in.
- **Arrays:** a pattern without ``${hpc.task_id}`` gets ``.${hpc.task_id}``
  appended, so tasks never write the same file
  (``name.123.out.4``). The same applies to ``stderr``.
- **Relative paths** are relative to the job's working directory
  (``workdir``, else the directory you submit from). The directory part is
  created on the submit host before submission.
- **stderr** is merged into stdout unless a ``stderr`` pattern is set, which
  turns merging off.
- ``${hpc.job_name}`` is the name as the scheduler submits it: on every
  scheduler whitespace, ``/`` and non-printable characters become ``_``
  (SGE adds its own rules, see :doc:`sge`).
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
Odd site resources still go through :ref:`scheduler_args <scheduler-args>`
untouched.


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
expanded: it is your shell.

**Runtime values.** Values only the scheduler knows when the job starts use
the ``${hpc.*}`` namespace, which each scheduler renders in its own terms:

.. list-table::
   :header-rows: 1

   * - Name
     - Meaning
     - SGE
     - local
   * - ``${hpc.cpus}``
     - slots / CPUs granted
     - ``$NSLOTS``
     - ``cpu`` (or 1)
   * - ``${hpc.job_id}``
     - job id
     - ``$JOB_ID``
     - the local id
   * - ``${hpc.task_id}``
     - array task index
     - ``$SGE_TASK_ID``
     - empty
   * - ``${hpc.tmpdir}``
     - job scratch directory
     - ``$TMPDIR``
     - ``$TMPDIR``
   * - ``${hpc.job_name}``
     - job name
     - ``$JOB_NAME``
     - the job name
   * - ``${hpc.host}``
     - execution host
     - ``$HOSTNAME``
     - ``$HOSTNAME``

Any other ``${hpc.x}`` is an error. Writing a scheduler variable directly,
such as ``-j$NSLOTS``, is an undefined variable on the submit host; the
error suggests the ``${hpc.*}`` equivalent:

.. code-block:: toml

   [tools.make]
   env_vars = { MAKEFLAGS = "-j${hpc.cpus}" }

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
shell's environment (``inherit_env = true``, SGE ``-V``) and its script
then always runs ``module purge`` followed by the configured ``module use``
and ``module load`` commands. Tool versions come from modules: because the
module system owns the ``PATH`` and library entries of what it loaded, the
purge-and-load guarantees that every tool the config names is the version
the config names, whatever the submitting shell had loaded. That is the
whole contract. Everything else in the inherited environment (session
variables, ``DISPLAY``, a tool a site put on ``PATH`` outside the module
system) is carried through unchanged; the fix for the last of these is to
put the tool in a module, not to reset variables in hpc-runner.

**Strict mode.** ``inherit_env = false`` drops ``-V``: the job starts from
the scheduler's clean environment. ``env_passthrough`` names submit-host
variables to pass explicitly (SGE ``-v NAME=value``); each must be set
where you submit, or resolution fails. With ``inherit_env = true``,
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
renders last).

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
the job. ``hpc --verbose run`` shows module output as it happens. A site
running Environment Modules (4.3 or later) can declare it to make the
module system itself terser:

.. code-block:: toml

   [schedulers.sge]
   module_system = "modules"     # "modules", "lmod" or "" (the default)
   module_init_script = ""       # site module init; empty = auto-detect

With ``module_system = "modules"`` the script exports
``MODULES_VERBOSITY=concise``.


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
   resources = [{ name = "xcelium_lic", value = 1 }]

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
  are not accepted. Across files the list merges like every other list
  (:ref:`list-merging`).


.. _tool-option-specialisation:

Tool option specialisation
--------------------------

A tool entry can define ``[tools.<name>.options]`` sub-entries that match
against arguments in the command string.  When matched, the option entry is
merged on top of the base tool config.  This allows a single tool to have
different environments depending on how it is invoked.

**TOML syntax:**

.. code-block:: toml

   [tools.fusesoc]
   cpu = 2
   modules = ["fusesoc/2.0"]

   [tools.fusesoc.options."--tool slang"]
   mem = "16G"
   modules = ["slang/0.9"]          # appended → ["fusesoc/2.0", "slang/0.9"]

   [tools.fusesoc.options."--tool verilator"]
   cpu = 4
   modules = ["verilator/5.0"]      # appended → ["fusesoc/2.0", "verilator/5.0"]

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

Save as ``hpc-runner.toml`` at your git root or in the current directory:

.. code-block:: toml

   config_version = 1

   [defaults]
   name = "job"
   cpu = 1
   mem = "4G"
   time = "1:00:00"
   queue = "batch.q"           # SGE default queue
   priority = 0
   workdir = "."
   shell = "/bin/bash"
   use_cwd = true
   inherit_env = true

   stdout = "${hpc.job_name}.${hpc.job_id}.out"   # the default
   # stderr = "${hpc.job_name}.${hpc.job_id}.err"  # unset: merged into stdout

   modules = ["gcc/12.2", "python/3.11"]
   modules_path = []           # module use <path>: searched before the site paths
   modules_path_append = []    # module use --append <path>: searched after them

   resources = [
     { name = "scratch", value = "20G" }
   ]

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

   module_init_script = ""
   module_system = "modules"

   [trace]
   enabled = false             # record every scheduler call (see Trace settings)

   [tools.python]
   cpu = 4
   mem = "16G"
   time = "4:00:00"
   queue = "short.q"
   modules = { set = ["python/3.11"] }  # replace the list rather than append
   resources = [
     { name = "tmpfs", value = "8G" }
   ]

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
   resources = [
     { name = "gpu", value = 1 }
   ]
