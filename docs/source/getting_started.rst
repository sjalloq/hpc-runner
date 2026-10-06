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


Pick a scheduler
----------------

By default, hpc-runner tries to auto-detect the scheduler. You can force it:

.. code-block:: bash

   hpc --scheduler sge run "python train.py"

Or use the environment variable:

.. code-block:: bash

   export HPC_SCHEDULER=sge


First config
------------

Create a project-local config (safe to commit):

.. code-block:: bash

   hpc config init

Create a user config:

.. code-block:: bash

   hpc config init --global

Then open the file and set your site defaults, e.g. a default SGE queue:

.. code-block:: toml

   [defaults]
   queue = "batch.q"

See :doc:`configuration` for the full configuration format and examples.
