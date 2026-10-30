Getting Started
===============

This guide gets you from “installed” to a first successful submission.


Install
-------

In a virtual environment:

.. code-block:: bash

   pip install hpc-runner


Quick start: run a job
----------------------

Run a command on the cluster. By default it runs interactively (SGE:
``qrsh``) and its output streams to your terminal:

.. code-block:: bash

   hpc run python -c 'print("hello")'

Submit as a batch job and return immediately:

.. code-block:: bash

   hpc run --batch python train.py

Show what would be submitted without actually submitting:

.. code-block:: bash

   hpc run --dry-run python train.py

Pass raw scheduler arguments using ``--`` as a separator:

.. code-block:: bash

   hpc run -q gpu.q -l gpu=1 -- python train.py

Use a named job type from the config:

.. code-block:: bash

   hpc run -t gpu --cpu 4 python train.py

Pass the command as separate words, as you would type it. A quoted
``"python train.py"`` is one word, so the job would look for a program with
a space in its name.


Pick a scheduler
----------------

By default hpc-runner detects the scheduler, in this order:

1. ``HPC_SCHEDULER`` in the environment.
2. SGE: ``qsub`` on ``PATH`` with ``SGE_ROOT`` set, or ``qstat -help``
   reporting Grid Engine.
3. Slurm: ``sbatch`` and ``squeue`` on ``PATH`` (not yet supported; see the
   compatibility table on the :doc:`index` page).
4. PBS: ``qsub`` on ``PATH`` with ``PBS_CONF_FILE`` set, or ``qsub --version``
   reporting ``pbs_version`` (likewise).
5. Local.

You can force one:

.. code-block:: bash

   hpc --scheduler sge run python train.py
   export HPC_SCHEDULER=sge

``hpc --scheduler local run ...`` runs the job on this machine, through the
same generated script with the same ``module purge`` and ``module load``
lines, so a workstation without a scheduler still gets the configured tool
versions.


First config
------------

Create a starter ``hpc-runner.toml`` in the current directory (put it at
your git root to make it the project config; safe to commit):

.. code-block:: bash

   hpc config init

Then open the file and set your site defaults, e.g. a default SGE queue:

.. code-block:: toml

   config_version = 1

   [defaults]
   queue = "batch.q"

Run ``hpc config check`` after editing to validate it.

See :doc:`configuration` for the full configuration format and examples.
