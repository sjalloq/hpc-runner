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

Configuration supports four top-level namespaces:

- ``[defaults]``: baseline job settings applied to all jobs
- ``[tools.<name>]``: overrides keyed by tool name (e.g., ``python``, ``make``).
  Each tool entry can also contain an ``[tools.<name>.options]`` sub-table for
  argument-specific specialisation (see :ref:`tool-option-specialisation`).
- ``[types.<name>]``: named job profiles (e.g., ``gpu``, ``interactive``)
- ``[schedulers.<name>]``: scheduler-specific behavior (e.g., SGE resource names)


How jobs pick up config
-----------------------

Every ``Job()`` automatically consults the config hierarchy.  Values merge in
this order:

1. Start from ``[defaults]``.
2. If ``job_type`` is given, merge ``[types.<name>]``.
   Otherwise auto-detect the tool name from the command (first word, path
   stripped) and merge ``[tools.<name>]`` if it exists.
3. If ``[tools.<name>.options]`` entries exist and the command arguments match
   one (see :ref:`tool-option-specialisation`), merge that option config on top.
4. CLI options (or explicit keyword arguments) override whatever came from
   config.

.. note::

   List values use a "merge by union" strategy — items from both the base and
   override are combined, preserving order and deduplicating.
   If you need to *replace* a list instead of merging, use a leading ``"-"`` entry
   to reset it, e.g. ``modules = ["-", "python/3.11"]``.


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
   modules = ["slang/0.9"]          # union-merged → ["fusesoc/2.0", "slang/0.9"]

   [tools.fusesoc.options."--tool verilator"]
   cpu = 4
   modules = ["verilator/5.0"]      # union-merged → ["fusesoc/2.0", "verilator/5.0"]

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

   submit fusesoc run --tool slang core:v:n     # mem=16G, modules includes slang/0.9
   submit fusesoc run --tool=slang core:v:n     # same (= normalised)
   submit fusesoc run --tool verilator core     # cpu=4, modules includes verilator/5.0
   submit fusesoc run core:v:n                  # no match — base fusesoc config only


Example: fully populated config (standalone file)
-------------------------------------------------

Save as ``hpc-runner.toml`` at your git root or in the current directory:

.. code-block:: toml

   [defaults]
   scheduler = "auto"          # auto|sge|slurm|pbs|local
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

   raw_args = []
   sge_args = []

   resources = [
     { name = "scratch", value = "20G" }
   ]

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

   merge_output = true
   purge_modules = true
   silent_modules = false
   module_init_script = ""
   expand_makeflags = true
   unset_vars = ["https_proxy", "http_proxy"]

   [tools.python]
   cpu = 4
   mem = "16G"
   time = "4:00:00"
   queue = "short.q"
   modules = ["-", "python/3.11"]  # replace list rather than union-merge
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

   [types.interactive]
   queue = "interactive.q"
   time = "8:00:00"
   cpu = 2
   mem = "8G"

   [types.gpu]
   queue = "gpu.q"
   cpu = 8
   mem = "64G"
   time = "12:00:00"
   resources = [
     { name = "gpu", value = 1 }
   ]
