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
  file; ``$VAR`` expanded). The target must exist, and a cycle is an error.
- ``[defaults]``: baseline job settings applied to all jobs
- ``[tools.<name>]``: settings keyed by tool name (e.g., ``python``, ``make``).
  Each tool entry can also contain an ``[tools.<name>.options]`` sub-table for
  argument-specific specialisation (see :ref:`tool-option-specialisation`).
- ``[types.<name>]``: named flows that compose tools (see :ref:`job-types`)
- ``[schedulers.<name>]``: site facts for one scheduler (e.g., SGE resource names)

``[defaults]``, ``[tools.*]``, ``[tools.*.options.*]`` and ``[types.*]`` all
accept the same job settings: ``name``, ``cpu``, ``mem``, ``time``,
``queue``, ``priority``, ``project``, ``nodes``, ``tasks``, ``stdout``,
``stderr``, ``inherit_env``, ``workdir``, ``shell``, ``use_cwd``, ``venv``,
``interactive``, ``env_vars``, ``env_prepend``, ``env_append``,
``env_unset``, ``modules``, ``modules_path``, ``modules_path_append``,
``resources`` and ``scheduler_args``. In addition ``options`` is allowed only
under ``[tools.<name>]`` and ``tools`` only under ``[types.<name>]``.


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


How jobs pick up config
-----------------------

``resolve(spec, config, host)`` applies a fixed chain of layers, least
specific first, then the spec (the CLI options or the fields set on the
``JobSpec``):

.. code-block:: text

   no type:      defaults -> tools.<argv0> -> tools.<argv0>.options.<first match> -> spec
   with -t T:    defaults -> tools.<each of types.T.tools, in order> -> types.T -> spec

``<argv0>`` is the command's first word with any path stripped. With a type
named, the tool inferred from the command is **not** consulted, and the
``options`` of the type's tools are not applied: the type is the whole
specification.

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
``resources`` and each scheduler's ``scheduler_args`` list follow one rule
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
  resource names are configured under ``[schedulers.sge]``.


Environment variables
---------------------

Three dictionary fields control the job environment.  All values support
``$VAR`` and ``${VAR}`` expansion — variables are expanded at job creation
time, before the scheduler's ``module purge`` clears the environment.

- ``env_vars``: set variables to explicit values (overwrites any existing value)
- ``env_prepend``: prepend to PATH-like variables (colon-separated)
- ``env_append``: append to PATH-like variables (colon-separated)

These can appear in ``[defaults]``, ``[tools.<name>]``, or ``[types.<name>]``.

.. code-block:: toml

   [tools.myapp.env_vars]
   LICENSE_FILE = "/opt/licenses/myapp.lic"

   [tools.myapp.env_prepend]
   PATH = "$HOME/.local/bin"

   [types.gpu.env_append]
   LD_LIBRARY_PATH = "/opt/cuda/lib64"


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

   stdout = "hpc.%N.%J.out"
   stderr = ""                 # empty means "unset"

   modules = ["gcc/12.2", "python/3.11"]
   modules_path = []           # module use <path>: searched before the site paths
   modules_path_append = []    # module use --append <path>: searched after them

   resources = [
     { name = "scratch", value = "20G" }
   ]

   env_unset = ["https_proxy", "http_proxy"]   # unset in the job script

   # Environment variable manipulation (values support $VAR / ${VAR} expansion)
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

   purge_modules = true
   silent_modules = false
   module_init_script = ""
   expand_makeflags = true

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
