SGE Guide
=========

This page describes how hpc-runner maps job fields onto Sun Grid Engine (SGE)
submission flags and directives.


Queues (default queue)
----------------------

To set a **default queue** for all jobs:

.. code-block:: toml

   [defaults]
   queue = "batch.q"

To override per job:

.. code-block:: bash

   hpc run --queue gpu.q "python train.py"

Or using passthrough flags directly:

.. code-block:: bash

   hpc run -q gpu.q -- python train.py


CPU / slots (parallel environment)
----------------------------------

``cpu`` maps to SGE PE slots:

- config: ``[schedulers.sge].parallel_environment`` (default: ``smp``)
- job field: ``cpu`` (e.g. 8)
- SGE: ``qsub -pe <parallel_environment> <cpu>``

Example:

.. code-block:: toml

   [schedulers.sge]
   parallel_environment = "smp"

.. code-block:: bash

   hpc run --cpu 8 "python train.py"


Memory and time resources
-------------------------

hpc-runner renders ``mem`` and ``time`` using SGE hard resources:

- config: ``[schedulers.sge].memory_resource`` (default: ``mem_free``)
- config: ``[schedulers.sge].time_resource`` (default: ``h_rt``)
- SGE: ``qsub -l <memory_resource>=<mem> -l <time_resource>=<time>``

Example (site using ``h_vmem`` and ``h_rt``):

.. code-block:: toml

   [schedulers.sge]
   memory_resource = "h_vmem"
   time_resource = "h_rt"


Custom resources
----------------

Use ``resources`` to request arbitrary ``-l`` resources:

.. code-block:: toml

   [types.gpu]
   queue = "gpu.q"
   resources = [{ name = "gpu", value = 1 }]

This renders as:

- ``#$ -l gpu=1`` in the generated script


Native qsub passthrough
-----------------------

If you need site-specific flags not modeled as first-class fields, pass them
through:

.. code-block:: bash

   hpc run -P myproject -l exclusive=true -- python train.py


Environment and modules
-----------------------

- ``inherit_env = true`` (the default) renders ``#$ -V``: the job inherits
  the submitting shell's environment. ``inherit_env = false`` omits it, and
  each name in ``env_passthrough`` becomes ``#$ -v NAME=value`` (``qrsh -v``
  for interactive jobs) with the value read from the submitting shell.
- Every job script then runs ``module purge`` (always; it is how the
  configured module versions are guaranteed) and the configured
  ``module use`` / ``module load`` commands, quietly unless one fails. Set
  ``[schedulers.sge] module_init_script`` if the node's module init is not
  in a standard place, and ``module_system = "modules"`` on Environment
  Modules sites for terser output.
- ``${hpc.*}`` runtime values render as SGE variables: ``${hpc.cpus}`` is
  ``$NSLOTS``, ``${hpc.job_id}`` ``$JOB_ID``, ``${hpc.task_id}``
  ``$SGE_TASK_ID``, ``${hpc.tmpdir}`` ``$TMPDIR``, ``${hpc.job_name}``
  ``$JOB_NAME`` and ``${hpc.host}`` ``$HOSTNAME``.

See :ref:`variable-expansion` and the Environment section of the
configuration guide.


Exit sentinels and job status
-----------------------------

With ``enforce_afterok`` enabled (the default), every batch job writes its
exit code to ``<exit_dir>/<JOB_ID>.rc``, where ``exit_dir`` is
``$HPC_EXIT_DIR`` or ``~/.cache/hpc-runner/exit``. These files implement
afterok dependencies and also serve as the status fallback: if ``qstat`` no
longer knows a job and ``qacct`` cannot report it (accounting disabled or
delayed), hpc-runner reads the sentinel to report COMPLETED or FAILED and the
exit code instead of UNKNOWN. Sentinels older than seven days are removed.
