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

Run a command on the cluster. By default it runs interactively (SGE
``qrsh``, Slurm ``srun``) and its output streams to your terminal:

.. code-block:: bash

   hpc run python -c 'print("hello")'

Submit as a batch job and return immediately:

.. code-block:: bash

   hpc run --batch python train.py

Show what would be submitted without actually submitting:

.. code-block:: bash

   hpc run --dry-run python train.py

Pass raw scheduler arguments as one quoted string with ``-S``/``--scheduler-args``:

.. code-block:: bash

   hpc run -S '-l gpu=1 -P proj' python train.py     # SGE flags
   hpc run -S '--nice=500' python train.py           # Slurm flags

Use a named job type from the config:

.. code-block:: bash

   hpc run -t gpu --cpu 4 python train.py

Pass the command as separate words, as you would type it. A quoted
``"python train.py"`` is one word, so the job would look for a program with
a space in its name.


Pick a scheduler
----------------

An installation has one scheduler, and hpc-runner is told which; it never
probes ``PATH`` or the environment to guess. Name it in the config (see
:doc:`configuration`), usually once in the project or site file:

.. code-block:: toml

   config_version = 1
   scheduler = "sge"

(``"sge"``, ``"slurm"`` or ``"local"``), or with ``HPC_SCHEDULER`` in the environment, which overrides the config:

.. code-block:: bash

   export HPC_SCHEDULER=sge

With neither, every command that needs a scheduler stops with an error
saying where to set it. See the compatibility table on the :doc:`index`
page for the schedulers this release supports, and :doc:`sge` and
:doc:`slurm` for what each one needs.

``HPC_SCHEDULER=local hpc run ...`` runs the job on this machine, through the
same generated script with the same ``module purge`` and ``module load``
lines, so a workstation without a scheduler still gets the configured tool
versions.


First config
------------

Create a starter ``hpc-runner.toml`` in the current directory (put it at
your git root to make it the project config; safe to commit):

.. code-block:: bash

   hpc config init

Then open the file and set your site defaults, e.g. a default queue (an
SGE queue or a Slurm partition):

.. code-block:: toml

   config_version = 1

   [defaults]
   queue = "batch.q"

Run ``hpc config check`` after editing to validate it.

See :doc:`configuration` for the full configuration format and examples.
