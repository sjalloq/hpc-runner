Slurm Guide
===========

This page describes how hpc-runner maps job fields onto Slurm submission
flags, and how it reads a job's status and exit code back.


Requirements
------------

- **Slurm 22.05 or later.** hpc-runner is tested against 22.05 and 24.11.
- **Accounting.** Job status is read from ``sacct``, so the cluster needs
  Slurm accounting and ``sacct`` on the submit host.
- **A shared filesystem.** The job script, the working directory and the
  output files are read on the submit host and on the nodes by the same
  paths.

Name the scheduler once, in the project config:

.. code-block:: toml

   config_version = 1
   scheduler = "slurm"


How each option reaches Slurm
-----------------------------

A batch job is submitted with ``sbatch <script>``. Every option is a
``#SBATCH`` line of the script, so the script is the record of how the job
was submitted. An interactive job is run with ``srun <script>`` and carries
the same flags as arguments (see `Interactive jobs`_).

.. list-table::
   :header-rows: 1
   :widths: 22 38 40

   * - Option
     - Slurm flag
     - Notes
   * - ``name``
     - ``--job-name=NAME``
     - Sanitised, see `Output files and job names`_.
   * - ``cpu``
     - ``--cpus-per-task=N --ntasks=1``
     - Always sent. ``N`` is 1 when ``cpu`` is not set. See
       `CPUs and tasks`_.
   * - ``mem``
     - ``--mem=MB``
     - ``--mem-per-cpu=MB`` with ``memory_per = "cpu"``. See `Memory`_.
   * - ``time``
     - ``--time=D-HH:MM:SS``
     - ``4h`` is ``0-04:00:00``; ``24h`` is ``1-00:00:00``.
   * - ``queue``
     - ``--partition=NAME``
     -
   * - ``project``
     - ``--account=NAME``
     -
   * - ``workdir``
     - ``--chdir=PATH``
     - Always sent.
   * - ``stdout``
     - ``--output=PATH``
     - Batch jobs only. Always sent, as an absolute path.
   * - ``stderr``
     - ``--error=PATH``
     - Batch jobs only. Sent only when ``stderr`` is set.
   * - ``inherit_env``
     - ``--export=ALL`` when true, ``--export=NONE`` when false
     - See `Environment and modules`_.
   * - ``env_passthrough``
     - ``--export=NAME=value,...``
     - Used only with ``inherit_env = false``.
   * - ``licenses``
     - ``-L name:N,name:N``
     -
   * - ``devices``
     - ``--gres=name:N,name:N``
     -
   * - ``features``
     - ``-C name&name``
     -
   * - ``array``
     - ``--array=START-END[:STEP][%MAX]``
     - Batch jobs only. See `Arrays`_.
   * - ``dependencies``
     - ``--dependency=TYPE:ID[:ID...][,TYPE:ID...]``
     - Batch jobs only. See `Dependencies`_.
   * - ``scheduler_args``
     - the arguments as given
     - After hpc-runner's own flags.

Every job runs in its working directory under bash: ``--chdir`` is always
sent and the script starts ``#!/bin/bash``.


CPUs and tasks
--------------

hpc-runner runs one command, and on Slurm that is one task. ``cpu = N``
asks for ``N`` CPUs for that one task:

.. code-block:: bash

   hpc run --batch --cpu 8 make -j8 sim     # --cpus-per-task=8 --ntasks=1

- Both flags are always sent. A job that does not set ``cpu`` asks for one
  CPU.
- Slurm records the job as one task with ``N`` CPUs, and ``${hpc.cpus}`` is
  ``N`` inside the job.
- ``cpu`` is the number of cores your command may use. It is not a number
  of tasks and not a number of nodes. hpc-runner has no option for either.

A job that needs several tasks or several nodes, an MPI job for example,
asks for them with Slurm's own flags in ``scheduler_args``
(:ref:`scheduler-args`). They are written after hpc-runner's own flags:

.. code-block:: toml

   [types.mpi]
   scheduler_args = ["--nodes=2", "--ntasks=32"]


Memory
------

``mem`` is the memory for the **whole job** (see :ref:`memory-and-time`).
It is sent as ``--mem`` in whole megabytes, rounded up: ``16G`` is
``--mem=16384`` and ``1.5G`` is ``--mem=1536``.

- The request is never 0. An amount under one megabyte is sent as 1.
- A job that does not set ``mem`` sends no memory flag.
- On a cluster that enforces memory, a job that uses more than its ``mem``
  is killed and reported as ``KILLED`` (see `Job status`_).

Some sites want memory asked for per CPU. They set ``memory_per = "cpu"``.
``mem`` is still the whole job's: hpc-runner divides it by ``cpu``, rounds
up to the megabyte and sends ``--mem-per-cpu``. A job with ``cpu = 4`` and
``mem = "16G"`` sends ``--mem-per-cpu=4096``. A job with two CPUs and
``mem = "300M"`` sends ``--mem-per-cpu=150``, and Slurm books it 300M.


Scheduler settings
------------------

``[schedulers.slurm]`` has two keys. Any other key there is a config error.

.. list-table::
   :header-rows: 1
   :widths: 28 20 12 40

   * - Key
     - Values
     - Default
     - Meaning
   * - ``memory_per``
     - ``"job"`` or ``"cpu"``
     - ``"job"``
     - ``"job"``: ``mem`` is sent as ``--mem``. ``"cpu"``: ``mem`` is
       divided by ``cpu`` and sent as ``--mem-per-cpu``.
   * - ``kill_invalid_dependents``
     - ``true`` or ``false``
     - ``true``
     - ``true``: ``--kill-on-invalid-dep=yes`` is sent with a job's
       dependencies. ``false``: it is not sent. See `Dependencies`_.

.. code-block:: toml

   [schedulers.slurm]
   memory_per = "cpu"
   kill_invalid_dependents = false


Output files and job names
--------------------------

Every batch job gets an explicit, absolute ``--output``. The default
pattern ``${hpc.job_name}.${hpc.job_id}.out`` renders as
``#SBATCH --output=<workdir>/%x.%j.out``, which Slurm fills in. An array
job gets the task id before the last extension (``%x.%A.%a.out``) unless
the pattern already uses ``${hpc.task_id}``. See :ref:`output-files`.

- Without ``stderr``, no ``--error`` is sent and Slurm writes both streams
  to the output file. A ``stderr`` pattern renders ``--error``.
- A relative pattern is joined onto the job's working directory, so the
  path in the script is always absolute.
- The directory of each path is created before ``sbatch`` runs.

hpc-runner sanitises the job name it sends. Whitespace, ``/``, quotes,
backslashes and ``#`` each become ``_``: ``--name "my job"`` is submitted
as ``my_job``. The sanitised name is the one in ``--job-name``,
``${hpc.job_name}`` and the output path.


Values a job script cannot carry
--------------------------------

A ``#SBATCH`` line is split on spaces and does not keep a quote, a
backslash or a ``#``. hpc-runner keeps every option in the script, where it
is the record of how the job was submitted, so a value with one of those
characters cannot be passed.

hpc-runner refuses such a value before ``sbatch`` runs, with a
``SubmissionError`` that names it. Nothing is submitted. The values checked
are:

- the working directory;
- ``queue`` and ``project``;
- the ``stdout`` and ``stderr`` paths;
- each value passed with ``env_passthrough``;
- the name of each licence, device and feature.

The job name is not refused. It is sanitised, as above.

An interactive job has no ``#SBATCH`` lines: ``srun`` takes each value as
one argument, so these values are accepted there.


Licences, devices and features
------------------------------

``licenses``, ``devices`` and ``features``
(:ref:`licenses-devices-features`) are requested with ``-L``, ``--gres``
and ``-C``:

.. code-block:: toml

   [types.gpu]
   queue = "gpu"
   licenses = { xcelium = 1 }
   devices = { gpu = 2 }
   features = ["bigmem"]

This renders as:

- ``#SBATCH -L xcelium:1``, ``#SBATCH --gres=gpu:2`` and
  ``#SBATCH -C bigmem`` in the generated script;
- ``-L xcelium:1 --gres=gpu:2 -C bigmem`` on the ``srun`` command line of
  an interactive job.

Several of a kind go in one option: ``-L xcelium:1,vcs:2``,
``--gres=gpu:2,fpga:1``, ``-C bigmem&avx2``.

A job that asks for a licence, device or feature another job holds waits
in the queue. A request the cluster cannot meet (an unknown name, more
than exist) is refused at submission, and the error carries Slurm's own
message.

On the command line: ``--license xcelium=1``, ``--device gpu=2``,
``--feature bigmem`` (each repeatable). A GPU type, a licence server or any
other Slurm request goes in ``scheduler_args``, below.


Native sbatch passthrough
-------------------------

If you need a Slurm flag hpc-runner has no option for, pass it through:

.. code-block:: bash

   hpc run --batch -S '--nice=500 --comment=nightly' make sim

In config the same flags go in ``scheduler_args`` (:ref:`scheduler-args`).
A batch job carries them together on one ``#SBATCH`` line, after
hpc-runner's own. An interactive job carries them at the end of the
``srun`` command line.


Environment and modules
-----------------------

- ``inherit_env = true`` (the default) sends ``--export=ALL``: the job
  inherits the submitting shell's environment.
- ``inherit_env = false`` sends ``--export=NONE``. With ``env_passthrough``
  it sends ``--export=NAME=value,...`` instead, each value read from the
  submitting shell. Only the variables named reach the job from that shell.
- Slurm separates the variables of ``--export`` with commas, so a value
  that contains ``,`` cannot be passed. hpc-runner refuses it, on a batch
  and on an interactive job, with a ``SubmissionError`` that names the
  variable.
- Every job script then runs ``module purge`` (always; it is how the
  configured module versions are guaranteed) and the configured
  ``module use`` / ``module load`` commands, quietly unless one fails. Set
  the top-level ``module_init_script`` if the node's module init is not in
  a standard place.
- After that environment block, a batch script exports two variables for
  an ``srun`` inside your command: ``SRUN_CPUS_PER_TASK``, set to
  ``$SLURM_CPUS_PER_TASK``, and ``SLURM_EXPORT_ENV=ALL``.

.. code-block:: toml

   [types.regress]
   inherit_env = false
   env_passthrough = ["DISPLAY", "LM_LICENSE_FILE"]

Two things to know:

- **An interactive job with** ``inherit_env = false``. Name every variable
  the job needs in ``env_passthrough``, ``HOME``, ``USER`` and ``LOGNAME``
  included if your tools read them.
- **Slurm's own input variables.** Do not combine hpc-runner with
  ``SBATCH_*`` variables (``SBATCH_PARTITION``, ``SBATCH_EXPORT``, ...) set
  in a shell profile. hpc-runner states every option in the job script and
  expects Slurm to take it from there. Unset those variables in the shell
  you submit from.

See :ref:`environment` in the configuration guide.


``${hpc.*}`` variables
----------------------

Runtime values (:ref:`variable-expansion`) render as Slurm's own. In the
job script they are environment variables. In an output path they are the
symbols Slurm fills in.

.. list-table::
   :header-rows: 1
   :widths: 22 48 30

   * - Variable
     - In the script
     - In an output path
   * - ``${hpc.cpus}``
     - ``$SLURM_CPUS_PER_TASK``
     - not allowed
   * - ``${hpc.job_id}``
     - ``${SLURM_ARRAY_JOB_ID:-$SLURM_JOB_ID}``
     - ``%j``; ``%A`` for an array
   * - ``${hpc.task_id}``
     - ``$SLURM_ARRAY_TASK_ID``
     - ``%a``
   * - ``${hpc.job_name}``
     - ``$SLURM_JOB_NAME``
     - ``%x``
   * - ``${hpc.tmpdir}``
     - ``$TMPDIR``
     - not allowed
   * - ``${hpc.host}``
     - ``$SLURMD_NODENAME``
     - ``%N``

- ``${hpc.job_id}`` in an output path is the id ``sbatch`` printed, for
  every task of an array: the tasks of job 123 write ``name.123.1.out``,
  ``name.123.2.out`` and so on.
- ``${hpc.tmpdir}`` is plain ``$TMPDIR``, whatever it holds on the node.
  hpc-runner creates no directory and removes none. Scratch space is yours
  to arrange: make a directory in your command and clean it up there.


Arrays
------

An array range ``START-END[:STEP][%MAX]`` renders as one
``#SBATCH --array=START-END[:STEP][%MAX]`` line; ``%MAX`` caps the number
of tasks running at once.

- ``sbatch`` prints one job id for the array. A task is
  ``<id>_<index>``: task 3 of job 95 is ``95_3``.
- The status of the array's id is the array as a whole. The status of a
  task id is that task's own. Tasks that have not started are ``PENDING``.
- ``hpc status`` and ``hpc monitor`` list an array as one entry under its
  id. ``hpc status --history`` lists one entry per task.
- ``hpc status <id>`` lists every task of the array, also while tasks are
  pending or running. A task that has not started has no output path yet.
- Cancelling the array's id runs ``scancel <id>``, and the array is then
  ``CANCELLED``.


Dependencies
------------

Slurm's own ``--dependency`` is used. Each type is sent under its Slurm
name (``afterok``, ``afterany``, ``afternotok``, ``after``), and the
parents of one type share an element:
``#SBATCH --dependency=afterok:1:2,afterany:3``. A parent that is an array
is named by its job id, so the dependent waits for every task.

``kill_invalid_dependents = true`` (the default) adds
``--kill-on-invalid-dep=yes``. A dependent whose dependency can never be
satisfied (an ``afterok`` child of a failed job, say) is then cancelled by
Slurm. hpc-runner reports it as ``SKIPPED``:

- ``SKIPPED`` means the job never ran. It has no exit code and no output
  file.
- A job that depends on a skipped job with ``afterok`` is skipped in turn.
- Slurm's own state for it is ``CANCELLED`` with nobody named. A job that
  a person cancelled is ``CANCELLED by <uid>``, which hpc-runner reports as
  ``CANCELLED``.
- ``hpc status --history`` does not list a skipped job: ``sacct`` leaves it
  out of what it lists by time. ``hpc status <id>`` shows it.

With ``kill_invalid_dependents = false`` the flag is not sent. A dependent
whose dependency can never be satisfied then stays ``PENDING``, and
hpc-runner goes on reporting it as ``PENDING``: it reads ``sacct``, which
does not say why a job is waiting. Cancel such a job yourself.

.. code-block:: toml

   [schedulers.slurm]
   kill_invalid_dependents = true

An interactive job cannot have dependencies. One with any raises
``NotSupportedError`` ("interactive jobs cannot have dependencies; use
--batch").


Interactive jobs
----------------

An interactive job is ``srun <script>``. hpc-runner writes a small script
(the environment, then your command) to its script directory, which is
``$HPC_SCRIPT_DIR`` when that is set and must be on the shared filesystem.
The script deletes itself after it has run, unless ``--keep-script`` is
given.

- ``srun`` gets ``--pty`` only when hpc-runner's stdin and stdout are both
  terminals. With either redirected or piped, ``--pty`` is not sent.
- ``stdout`` and ``stderr`` are ignored: the job's output is your terminal,
  and ``--output`` and ``--error`` are never sent to ``srun``.
- When every slot is busy the job waits in the queue, then runs.
- ``hpc run`` exits with what ``srun`` exits with. That is your command's
  exit code. When ``srun`` itself cannot start the job (an unknown
  partition, for example) it prints its own message and exits 1.
- An interactive job cannot be an array and cannot have dependencies
  (:ref:`job-rules`).


Job status
----------

Status comes from ``sacct``, one command for any number of jobs. ``sacct``
has pending and running jobs too, so one source answers for a job's whole
life.

.. list-table::
   :header-rows: 1
   :widths: 62 38

   * - Slurm state
     - hpc-runner status
   * - ``PENDING``, ``REQUEUED``, ``REQUEUE_HOLD``, ``REQUEUE_FED``,
       ``RESV_DEL_HOLD``
     - ``PENDING``
   * - ``RUNNING``, ``CONFIGURING``, ``COMPLETING``, ``SIGNALING``,
       ``STAGE_OUT``, ``RESIZING``
     - ``RUNNING``
   * - ``SUSPENDED``, ``STOPPED``
     - ``SUSPENDED``
   * - ``COMPLETED``
     - ``COMPLETED``
   * - ``FAILED``, ``NODE_FAIL``, ``BOOT_FAIL``, ``SPECIAL_EXIT``
     - ``FAILED``
   * - ``CANCELLED by <uid>``, ``PREEMPTED``, ``REVOKED``
     - ``CANCELLED``
   * - ``CANCELLED``
     - ``SKIPPED``
   * - ``TIMEOUT``, ``DEADLINE``
     - ``TIMEOUT``
   * - ``OUT_OF_MEMORY``
     - ``KILLED``
   * - anything else
     - ``UNKNOWN``

- **Out of memory** is ``KILLED``. Slurm's own word for it is kept in the
  job's details: ``hpc status <id> --verbose`` shows ``OUT_OF_MEMORY``, and
  ``--json`` has it under ``details``. The same place shows who cancelled a
  job (``CANCELLED by 1003``).
- **A job** ``sacct`` **has no row for** is ``UNKNOWN``, after hpc-runner
  has asked ``sacct`` again for up to ten seconds: a new job reaches
  accounting a moment after it reaches the queue. So ``get_status`` of an
  id that does not exist takes ten seconds to answer ``UNKNOWN``.
  ``wait()`` rides a short ``UNKNOWN`` out (see ``unknown_after`` in
  :doc:`programmatic_api`).
- ``hpc status <id>`` **never waits.** It asks ``scontrol`` and ``sacct``
  once each. A job too new for ``sacct`` is shown with the status
  ``scontrol`` gives it, and an id neither knows is ``UNKNOWN`` at once.
- **A requeued job** is ``PENDING`` again, and ``wait()`` goes on to the
  end of its next run. ``hpc status --history`` lists it once.
- **A job on a node that dies** is requeued where the site requeues jobs,
  and is then ``PENDING``. Submitted with ``-S '--no-requeue'`` it ends
  ``FAILED``, with ``NODE_FAIL`` in the details.
- **A suspended job** is ``SUSPENDED``, and ``wait()`` does not return
  while it is.
- **The output path** in ``hpc status <id>`` comes from the controller. A
  job the controller no longer remembers is shown without one.
- **Cancel** is ``scancel <id>``.


Exit codes
----------

The exit code is what a shell would have reported for the job's command.
Slurm records an exit status and a signal, ``N:S``. hpc-runner reports
``N`` when ``S`` is 0, and ``128 + S`` otherwise.

.. list-table::
   :header-rows: 1
   :widths: 50 20 30

   * - Job
     - Status
     - Exit code
   * - Command exits 3
     - ``FAILED``
     - 3
   * - Killed at its time limit
     - ``TIMEOUT``
     - 143
   * - Cancelled while running
     - ``CANCELLED``
     - 143
   * - Killed for using more than its memory
     - ``KILLED``
     - 253
   * - Cancelled while pending, or skipped
     - ``CANCELLED``, ``SKIPPED``
     - none

A job in which nothing ran has no exit code. The exit code is 0 only for a
``COMPLETED`` job.

The code is read from ``scontrol show job`` while the controller remembers
the job, and from ``sacct`` after that. ``hpc status --history`` reads
``sacct`` alone.

Known limits
^^^^^^^^^^^^

Two things are not the same everywhere you look.

**1. An exit status above 128 shows as 128 lower in the history.**
``sacct`` prints an exit status above 128 with 128 taken off. A script that
exits 137 therefore shows:

- 137 in ``hpc status <id>`` and ``result.returncode`` while the controller
  remembers the job;
- 9 in ``hpc status <id>`` once the controller has forgotten it, and 9 in
  ``hpc status --history`` always.

The codes Slurm records as a signal (143 and 253 above) are not affected.

**2. A waited submission returns what** ``sbatch --wait`` **returns.**
``hpc run --batch --wait`` and ``submit(job, wait=True)`` submit with
``sbatch --wait`` and exit with its exit status. For your command's own
exit code that is the code itself, 137 included. For a job Slurm ended,
``sbatch --wait`` returns 1, while the same job read later gives the
signal's code:

.. list-table::
   :header-rows: 1
   :widths: 46 13 13 15 13

   * - Where you look
     - Time limit
     - Over memory
     - Cancelled while running
     - Script exits 137
   * - ``hpc run --batch --wait``, ``submit(job, wait=True)``
     - 1
     - 1
     - 1
     - 137
   * - ``result.wait()`` then ``result.returncode``; ``hpc status <id>``
       while the controller remembers the job
     - 143
     - 253
     - 143
     - 137
   * - ``hpc status <id>`` after that; ``hpc status --history``
     - 143
     - 253
     - 143
     - 9

The status is the same in every row: ``TIMEOUT``, ``KILLED``,
``CANCELLED``, ``FAILED``. Test the status, not the number, to tell why a
job ended.

A waited job that never ran (cancelled while pending, or skipped) makes
``hpc run`` exit 1. A waited array exits 0 only if every task completed,
otherwise 1.


What is out of scope
--------------------

hpc-runner runs one command as one batch or interactive job. It has no
support for, and no test of:

- heterogeneous jobs;
- federated clusters;
- job steps of its own (an ``srun`` inside your command is yours);
- ``salloc``;
- ``srun --x11``;
- ``hpc run`` from inside a running job;
- a dependency on a job the controller no longer remembers.
