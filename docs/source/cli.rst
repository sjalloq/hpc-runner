CLI
===

The entry point is the ``hpc`` command. ``hpc run`` runs a command on the
cluster, interactively by default; the other subcommands manage jobs and
configuration.


Global options
--------------

These options come before the subcommand:

- ``--config PATH``: use an explicit config file (bypasses discovery)
- ``--scheduler NAME``: force a scheduler (``sge``, ``slurm``, ``pbs``, ``local``)
- ``--verbose``: enable debug-level logging (e.g. shows script paths when
  ``--keep-script`` is used)


``hpc run``
-----------

Run a command on the cluster. By default the job runs interactively (SGE:
``qrsh``): output streams to your terminal and ``hpc`` waits for it to
finish. ``--batch`` submits and returns immediately. Array jobs are always
batch.

Options:

- ``--name TEXT``
- ``--cpu N``
- ``--mem TEXT`` (e.g. ``16G``)
- ``--time TEXT`` (e.g. ``4:00:00``)
- ``--queue TEXT`` (SGE queue)
- ``--nodes N`` (number of nodes, MPI jobs)
- ``--ntasks N`` (number of tasks, MPI jobs)
- ``--directory PATH`` (working dir)
- ``-t, --type TEXT`` (named profile from config)
- ``--extra-module TEXT`` (extra module on top of config, repeatable)
- ``--stdout TEXT`` (stdout file path pattern)
- ``--stderr TEXT`` (separate stderr file; default: merged)
- ``--array TEXT`` (e.g. ``1-100``)
- ``--depend TEXT``
- ``-b, --batch`` (submit and return; default is interactive via qrsh/srun)
- ``--local`` (run as local subprocess)
- ``--dry-run`` (render, don’t submit)
- ``--wait`` (wait for completion)
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
   hpc run -q gpu.q -l gpu=1 -- python train.py
   hpc run --cpu 4 -q batch.q -- mpirun -N 4 ./sim



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

- ``hpc cancel JOB_ID``: cancel a job
- ``hpc monitor``: launch the TUI job monitor
- ``hpc config show``: print the active config file contents
- ``hpc config path``: print the active config file path
- ``hpc config init``: create a starter ``hpc-runner.toml`` in the current directory
