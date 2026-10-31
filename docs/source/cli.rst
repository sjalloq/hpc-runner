CLI
===

The entry point is the ``hpc`` command. ``hpc run`` runs a command on the
cluster, interactively by default; the other subcommands manage jobs and
configuration.


Global options
--------------

These options come before the subcommand:

- ``--config PATH``: use an explicit config file (bypasses discovery)
- ``--scheduler NAME``: force a scheduler (``sge`` or ``local``; see the
  compatibility table on the :doc:`index` page)
- ``--verbose``: log every scheduler command (argv) to stderr, and on a
  scheduler error print the failing command and its full stderr (e.g. also
  shows script paths when ``--keep-script`` is used)
- ``--trace DIR``: record every scheduler call (argv, exit status, stdout,
  stderr) as one JSON file per call under ``DIR``, for this invocation. To
  record every invocation during a period of use, set ``[trace] enabled =
  true`` in the config instead (:ref:`trace-settings`); the flag wins when
  both are given. ``hpc trace`` reads a trace back (below).

Errors are printed as one line, ``error: <message>``, and ``hpc`` exits 1.
A Python traceback means a bug in hpc-runner.


``hpc run``
-----------

Run a command on the cluster. By default the job runs interactively (SGE:
``qrsh``): output streams to your terminal and ``hpc`` waits for it to
finish. ``--batch`` submits and returns immediately. Array jobs are always
batch.

Options:

- ``--name TEXT``
- ``--cpu N``
- ``--mem TEXT`` (memory for the whole job, e.g. ``16G``, ``1.5GiB``; see :ref:`memory-and-time`)
- ``--time TEXT`` (e.g. ``4:00:00``, ``4h``, ``1h30m``; a bare integer is an error)
- ``--queue TEXT`` (SGE queue)
- ``--nodes N`` (number of nodes, MPI jobs)
- ``--ntasks N`` (number of tasks, MPI jobs)
- ``--directory PATH`` (working dir)
- ``-t, --type TEXT`` (named profile from config)
- ``--stdout TEXT`` (stdout file pattern, batch only; default
  ``${hpc.job_name}.${hpc.job_id}.out``; see :ref:`output-files`)
- ``--stderr TEXT`` (separate stderr file pattern; default: merged into stdout)
- ``--array TEXT`` (e.g. ``1-100``, ``1-100:10%5``; implies batch)
- ``--env KEY=VALUE`` (repeatable; sets ``KEY`` in the job, added to the
  config's ``env_vars``; ``$VAR`` is resolved where you submit)
- ``--resource NAME=VALUE`` (repeatable; requests a scheduler resource or
  licence, added to the config's ``resources``; SGE ``-l NAME=VALUE``)
- ``--depend TEXT`` (repeatable; ``ID`` (afterok), or ``TYPE:ID[:ID...][,TYPE:ID...]``,
  e.g. ``afterok:123:124,afterany:125``)
- ``-b, --batch`` (submit and return; default is interactive via qrsh/srun)
- ``--dry-run`` (print the mode and the rendered script, don’t submit)
- ``--wait`` (with ``--batch`` or ``--array``: block until the job completes;
  without them it is a usage error, since an interactive run already waits)
- ``--keep-script`` (keep generated script; path is logged at debug level —
  use ``--verbose`` to see it)

Scheduler passthrough
^^^^^^^^^^^^^^^^^^^^^

Use ``--`` to pass raw scheduler arguments. Everything before ``--`` is passed
directly to the scheduler (e.g. ``qsub`` flags); everything after is the
command:

.. code-block:: bash

   # No passthrough — everything is the command
   hpc run --batch python train.py --epochs 10

   # Passthrough — scheduler flags before --, command after
   hpc run -q gpu.q -l gpu=1 -- python train.py
   hpc run --cpu 4 -q batch.q -l scratch=50G -- make -j8 sim

Without ``--``, all arguments are treated as the command. This avoids any
ambiguity between scheduler flags and command flags (e.g. ``mpirun -N 4``
won't be misinterpreted).

.. note:: **Why ``--``?**

   ``hpc run`` uses long-form options for itself (``--cpu``, ``--queue``,
   etc.) so that short flags (``-q``, ``-l``, ``-N``) are unambiguously
   scheduler arguments. The two exceptions, ``-t`` and ``-b``, shadow
   scheduler flags whose meaning hpc-runner already covers with ``--array``,
   ``--time`` and the generated job script. Unknown long options, and a
   single-dash flag given without ``--``, are rejected rather than forwarded. However, hpc-runner cannot determine where scheduler
   args end and the command begins without knowing every scheduler's option
   grammar — a flag like ``-N`` might be standalone or might consume the next
   argument. The ``--`` separator is the standard Unix solution to this
   ambiguity, used by ``git``, ``ssh``, ``docker exec``, and others.

Examples:

.. code-block:: bash

   hpc run xterm
   hpc run -t xcelium make sim
   hpc run --batch --cpu 4 --mem 16G --time 2:00:00 python train.py
   hpc run --batch -t gpu python train.py
   hpc run --batch --env OMP_NUM_THREADS=4 --resource xcelium_lic=1 make sim
   hpc run -q gpu.q -l gpu=1 -- python train.py
   hpc run --cpu 4 -q batch.q -- mpirun -N 4 ./sim



To run on the local machine instead of a cluster (for testing), use the
global option: ``hpc --scheduler local run ...``.

If a tool has ``[tools.<name>.options]`` entries in the config, the command
arguments are matched automatically to apply option-specific overrides (see
:ref:`tool-option-specialisation`).


``hpc status``
--------------

Show job status. ``JOB_ID`` is optional — without it, active jobs are listed.

.. code-block:: bash

   hpc status              # list active jobs
   hpc status 12345        # details for a single job
   hpc status --history    # recently completed jobs (via accounting)

Options:

- ``--history / -H``: show recently completed jobs
- ``--since / -s TEXT``: time window for ``--history`` (e.g. ``30m``, ``2h``, ``1d``)
- ``--all / -a``: show all users' jobs
- ``--json / -j``: output as JSON
- ``--verbose / -v``: show extra columns/fields
- ``--watch``: refresh periodically


Other commands
--------------

- ``hpc cancel JOB_ID [--force]``: cancel a job (``--force`` skips the
  confirmation)
- ``hpc kill [-N PATTERN] [--all] [--pending] [--running] [--force]``: cancel
  your active jobs whose name matches the regex ``PATTERN`` (or all of them
  with ``--all``), optionally only pending or only running ones
- ``hpc monitor [--refresh SECONDS]``: launch the TUI job monitor
- ``hpc config show [--raw]``: print the active configuration (merged, or the
  raw file contents)
- ``hpc config path [--all]``: print the active config file path (``--all``
  lists the whole merge chain)
- ``hpc config init``: create a starter ``hpc-runner.toml`` in the current directory
- ``hpc config check [PATH]``: validate the config (or ``PATH``), print every
  error and warning, exit 1 on any error
- ``hpc config resolve [-t TYPE] COMMAND...``: show the job ``COMMAND`` would
  get and which config layer supplied each value
- ``hpc trace compare DIR [--fixtures FIXTURES] [--json]``: run every
  scheduler output recorded under ``DIR`` (by ``--trace`` or ``[trace]``)
  back through the parsers and list what they could not make sense of: a
  parser exception, nothing parsed from text that holds something, a job
  whose state maps to ``UNKNOWN``, a qacct field (exit status, runtime,
  timestamps, failed reason) the record has but the result lacks. Each argv
  *shape* (``qacct -j <arg>``, ``qstat -xml -u <arg>``, ...) is listed with
  its call count. With ``--fixtures tests/unit/fixtures/sge`` the shapes
  no captured fixture covers are listed too. Exits 1 on any finding or
  uncovered shape, so it can run unattended over a trace directory. A
  finding is a parser bug: open an issue with the record attached.
