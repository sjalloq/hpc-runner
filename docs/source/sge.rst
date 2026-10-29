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

   hpc run --queue gpu.q python train.py

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

   hpc run --cpu 8 python train.py


Memory and time resources
-------------------------

hpc-runner renders ``mem`` and ``time`` using SGE hard resources:

- config: ``[schedulers.sge].memory_resource`` (default: ``mem_free``)
- config: ``[schedulers.sge].time_resource`` (default: ``h_rt``)
- SGE: ``qsub -l <memory_resource>=<mem> -l <time_resource>=<time>``

``mem`` is the memory for the **whole job** (see :ref:`memory-and-time`).
SGE multiplies a consumable such as ``mem_free`` or ``h_vmem`` by the
number of slots, so by default hpc-runner divides ``mem`` by ``cpu``
(rounded up) before rendering: ``cpu = 4, mem = "16G"`` renders
``-l mem_free=4G``. A site whose memory resource is per job sets
``memory_per = "job"`` and the value is passed as given.

- config: ``[schedulers.sge].memory_per`` (``"slot"``, the default, or ``"job"``)

``mem`` renders in the largest exact binary unit (``16G``; ``1.5G`` becomes
``1536M``) and ``time`` as ``H:MM:SS`` with unbounded hours (``2d`` becomes
``48:00:00``).

Example (site using ``h_vmem`` and ``h_rt``):

.. code-block:: toml

   [schedulers.sge]
   memory_resource = "h_vmem"
   memory_per = "slot"
   time_resource = "h_rt"


Output files and job names
--------------------------

Every batch job gets an explicit, absolute ``-o``. The default pattern
``${hpc.job_name}.${hpc.job_id}.out`` renders as
``#$ -o <workdir>/$JOB_NAME.$JOB_ID.out``, which SGE expands; array jobs get
``.$TASK_ID`` appended unless the pattern already uses ``${hpc.task_id}``.
Without ``stderr``, ``#$ -j y`` merges stderr into the same file; a
``stderr`` pattern renders ``#$ -e`` instead. The directory of each path is
created before ``qsub`` (SGE silently drops output whose directory is
missing). See :ref:`output-files`.

A relative pattern is joined onto the job's working directory (``workdir``,
else the submit directory) before rendering, so ``-o``/``-e`` are always
absolute: ``use_cwd`` only decides where the command runs, and the recorded
``stdout_path`` is right either way.

SGE rejects job names that start with a digit or contain whitespace,
``/``, ``:``, ``@``, ``\``, ``*`` or ``?``. hpc-runner sanitises the name
it renders: a leading digit gets a ``j`` prefix and each invalid character
becomes ``_`` (``--name "9 lives"`` is submitted as ``j9_lives``). The
sanitised name is the one in ``-N``, ``$JOB_NAME`` and the recorded output
path. Generated names (``<user>_<command>``) are unaffected.


Custom resources
----------------

Use ``resources`` to request arbitrary ``-l`` resources:

.. code-block:: toml

   [types.gpu]
   queue = "gpu.q"
   resources = [{ name = "gpu", value = 1 }]

This renders as:

- ``#$ -l gpu=1`` in the generated script

On the command line, ``--resource gpu=1`` (repeatable) adds to the config's
resources.


Native qsub passthrough
-----------------------

If you need site-specific flags not modeled as first-class fields, pass them
through:

.. code-block:: bash

   hpc run -l exclusive=true -binding linear:1 -- python train.py

In config the same flags go in ``scheduler_args``
(:ref:`scheduler-args`). The project is a job
setting, ``project = "myproject"`` (rendered ``-P``), so it needs no
passthrough.


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


Dependencies
------------

Every dependency renders one ``#$ -hold_jid <id>,<id>,...`` line. SGE's
hold is *afterany*: it releases the dependent once every parent has exited,
whether it succeeded or not. hpc-runner emulates the other kinds:

- ``afterany``: the hold alone.
- ``afterok``: a guard at the top of the dependent's script requires each
  afterok parent's exit sentinel (below) to hold ``0``.
- ``afternotok``: the same guard with the test inverted: each afternotok
  parent's sentinel must hold a non-zero code. A missing sentinel cannot
  prove the parent failed, so it fails the test too.
- ``after`` (start-after): ``-hold_jid`` cannot express it, so ``submit()``
  and ``preview()`` raise ``NotSupportedError``. Use ``afterany``.

Each parent is guarded with its own type, so ``afterok:1,afternotok:2`` is
one hold and two guards. A parent that is an array job (a dependency built
from its ``ArrayJobResult``) is checked task by task: the guard reads every
task's sentinel and all must pass. Elementwise array-to-array dependencies
(``-hold_jid_ad``) are not used; an array dependent waits for the whole
parent array. A parent known only by its id (``--depend`` on the command
line) may be an array job: the guard checks ``<id>.rc`` and, if that is
absent after the bounded wait, every ``<id>.*.rc`` that exists (at least one
is required), applying the parent's test to each. ``-hold_jid`` guarantees
every task has exited and published first, so that set is complete.

An interactive job (``qrsh``) cannot honour dependencies, so one with any
raises ``NotSupportedError`` ("interactive jobs cannot have dependencies;
use --batch"); the local scheduler does the same.

The guard allows up to 15 s per sentinel for the NFS directory cache to show
the parent's file. When a dependency is not satisfied the dependent does not
run its command: it exits with the reserved code **113**
(``hpc_runner.core.result.HPC_EXIT_DEP_FAILED``) and ``get_status`` reports
``JobStatus.SKIPPED``. Because 113 is non-zero, its own afterok dependents
are skipped in turn. 113 is reserved: a command that itself exits 113 is
also reported as SKIPPED. The code avoids SGE's special exits: 99 re-queues
the job (unless ``FORBID_RESCHEDULE``), and 100 puts it in error state
(unless ``FORBID_APPERROR``), which leaves a ``-hold_jid`` dependent held
forever.

Emulation is on by default and controlled by:

.. code-block:: toml

   [schedulers.sge]
   emulate_afterok = true

With ``emulate_afterok = false`` no sentinels are written and only
``afterany`` dependencies are accepted; ``afterok`` and ``afternotok`` raise
``NotSupportedError`` rather than silently becoming afterany.


Exit sentinels and job status
-----------------------------

With ``emulate_afterok`` enabled (the default), every batch job writes its
exit code to ``<exit_dir>/<JOB_ID>.rc`` (``<JOB_ID>.<TASK>.rc`` for each
task of an array job), where ``exit_dir`` is ``$HPC_EXIT_DIR`` or
``~/.cache/hpc-runner/exit`` on the shared home. These files implement the
dependency guard and also serve as the status fallback: if ``qstat`` no
longer knows a job and ``qacct`` cannot report it (accounting disabled or
delayed), hpc-runner reads the sentinel to report COMPLETED, FAILED or
SKIPPED and the exit code instead of UNKNOWN. Sentinels older than seven
days are removed.

Status queries are batched: ``get_statuses(ids)`` runs one
``qstat -xml -u '*'`` for all ids (each array task reports its own state),
then ``qacct`` (``qacct -j <id> -t <task>`` for a task) and the sentinel
only for ids no longer active. A ``qacct`` record with ``failed`` 37
(qmaster enforced ``h_rt``, ``h_cpu`` or ``h_vmem``) is reported as
``TIMEOUT``; a limit enforced by the execution daemon is recorded as a
signal death (``exit_status`` 137) and reported as ``FAILED``.
