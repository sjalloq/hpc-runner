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

- ``-N, --name TEXT``
- ``-c, --cpu N``
- ``-m, --mem TEXT`` (memory for the whole job, e.g. ``16G``, ``1.5GiB``; see :ref:`memory-and-time`)
- ``-T, --time TEXT`` (e.g. ``4:00:00``, ``4h``, ``1h30m``; a bare integer is an error)
- ``-q, --queue TEXT`` (SGE queue)
- ``-n, --nodes N`` (number of nodes, MPI jobs)
- ``--ntasks N`` (number of tasks, MPI jobs)
- ``-d, --directory PATH`` (working dir)
- ``-t, --type TEXT`` (named profile from config)
- ``-o, --stdout TEXT`` (stdout file pattern, batch only; default
  ``${hpc.job_name}.${hpc.job_id}.out``; see :ref:`output-files`)
- ``-e, --stderr TEXT`` (separate stderr file pattern; default: merged into stdout)
- ``-a, --array TEXT`` (e.g. ``1-100``, ``1-100:10%5``; implies batch)
- ``-E, --env KEY=VALUE`` (repeatable; sets ``KEY`` in the job, added to the
  config's ``env_vars``; ``$VAR`` is resolved where you submit)
- ``-R, --resource NAME=VALUE`` (repeatable; requests a scheduler resource or
  licence, added to the config's ``resources``; SGE ``-l NAME=VALUE``)
- ``-D, --depend TEXT`` (repeatable; ``ID`` (afterok), or ``TYPE:ID[:ID...][,TYPE:ID...]``,
  e.g. ``afterok:123:124,afterany:125``)
- ``-b, --batch`` (submit and return; default is interactive via qrsh/srun)
- ``--dry-run`` (print the mode and the rendered script, don’t submit)
- ``-w, --wait`` (with ``--batch`` or ``--array``: block until the job completes;
  without them it is a usage error, since an interactive run already waits)
- ``-S, --scheduler-args 'FLAGS'`` (repeatable; raw flags for the active
  scheduler, see below)
- ``--keep-script`` (keep generated script; path is logged at debug level —
  use ``--verbose`` to see it)

Scheduler passthrough
^^^^^^^^^^^^^^^^^^^^^

Raw scheduler flags go in one quoted string with ``-S``/``--scheduler-args``.
It is split like a shell would split it and appended to the scheduler's
command line (``qsub``/``sbatch`` flags) after anything the config adds. It may
be repeated; the pieces are kept in order.

.. code-block:: bash

   hpc run -S '-l gpu=1 -P proj' python train.py
   hpc run -c 4 -S '-l scratch=50G' -S '-P proj' make -j8 sim
   hpc run -S '--gres=gpu:2 --partition=short' srun ./a.out   # Slurm

``hpc run`` stops reading its own options at the first word that is not one,
so the command's flags are never interpreted (``hpc run mpirun -N 4 ./sim``
runs ``mpirun -N 4 ./sim``). A command that itself starts with a dash needs
the usual ``--`` first (``hpc run -- -h``). A flag ``hpc run`` does not know
is a usage error, never forwarded.

.. note:: **Why a quoted string?**

   hpc-runner cannot tell where raw scheduler flags end and the command begins
   without knowing every scheduler's option grammar: whether ``-N`` is a bare
   flag or takes a value differs between qsub and sbatch. Quoting the raw flags
   makes the boundary explicit and leaves every short letter free for
   hpc-runner's own options. It is the shape other scheduler front ends use
   (Snakemake's ``slurm_extra``, dask-jobqueue's ``job_extra_directives``).
   Site and tool policy belongs in the config's ``scheduler_args`` rather than
   on the command line; ``-S`` is the escape hatch.

Examples:

.. code-block:: bash

   hpc run xterm
   hpc run -t xcelium make sim
   hpc run --batch --cpu 4 --mem 16G --time 2:00:00 python train.py
   hpc run --batch -t gpu python train.py
   hpc run --batch --env OMP_NUM_THREADS=4 --resource xcelium_lic=1 make sim
   hpc run -S '-l gpu=1 -P proj' python train.py
   hpc run --cpu 4 -S '-l scratch=50G' mpirun -N 4 ./sim



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
- ``hpc trace bundle DIR [-o FILE] [--raw] [--scrub NAME]``: pack the trace
  for that issue. Usernames and hostnames are replaced everywhere by
  ``user1``, ``host1``, ...; job names, projects, departments, accounts and
  queues where the scheduler writes them; every value in a job's recorded
  environment becomes ``[scrubbed]``. The map of what was replaced is
  printed to stderr: read it before attaching the archive, and add
  ``--scrub NAME`` for anything it missed. ``--raw`` skips the scrub for a
  bundle that stays private. The archive holds ``meta.json`` (versions,
  the scheduler banner, the ``[schedulers.*]`` config, scrubbed) and the
  records under ``trace/``; ``hpc trace compare`` reads it extracted.
