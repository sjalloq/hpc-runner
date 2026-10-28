:orphan:

Programmatic API
================

This page documents the Python API for creating, submitting, and monitoring jobs
from code (without using the CLI).


Installation
------------

.. code-block:: bash

   pip install hpc-runner


Quick start
-----------

.. code-block:: python

   from hpc_runner import Job

   job = Job(command="python my_script.py", cpu=4, mem="8G", time="1:00:00")
   result = job.submit()          # auto-detect scheduler
   final = result.wait()          # block until complete
   print(final, result.returncode)


Creating jobs
-------------

``Job`` is the core unit of work. It’s a scheduler-agnostic data container; a
scheduler implementation is responsible for translating fields into submission
flags/directives.

.. code-block:: python

   from hpc_runner import Job

   job = Job(
       command="python train.py",
       name="training_job",
       cpu=4,
       mem="16G",
       time="4:00:00",
       queue="gpu.q",
       modules=["python/3.11", "cuda/12.0"],
       workdir="/path/to/project",
       stdout="train.out",
       stderr=None,   # None means “merge stderr into stdout” for most schedulers
   )


Submitting jobs
---------------

Submit to an auto-detected scheduler:

.. code-block:: python

   result = job.submit()

Or explicitly select a scheduler:

.. code-block:: python

   from hpc_runner import get_scheduler

   scheduler = get_scheduler("sge")
   result = scheduler.submit(job)


Monitoring jobs
---------------

``Job.submit()`` returns a ``JobResult`` which can be polled or waited on.

.. code-block:: python

   from hpc_runner import Job, JobStatus

   result = Job("python train.py").submit()

   while not result.is_complete:
       print(result.job_id, result.status)

   if result.status == JobStatus.COMPLETED:
       print("ok:", result.read_stdout(tail=20))
   else:
       print("failed:", result.read_stderr(tail=50))

Or wait and get the exit code in one call; a job that does not finish
``COMPLETED`` raises ``JobFailedError``:

.. code-block:: python

   exit_code = result.result()

Cancel a job:

.. code-block:: python

   result.cancel()


Configuration-driven jobs
-------------------------

``Job()`` is config-aware by default.  It auto-consults the TOML config
hierarchy, merging ``[defaults]`` with a matched ``[tools.<name>]`` or
``[types.<name>]`` section, then applies any explicit keyword arguments you
pass.  The tool name is auto-detected from the command (first word, path
stripped).  Use the ``job_type`` keyword to look up a ``[types.*]`` entry
instead (this skips tool auto-detection).

If the tool has ``[tools.<name>.options]`` entries defined in the config, the
**full command string** (not just the first word) is used to match
argument-specific overrides.  See :ref:`tool-option-specialisation` for the
matching rules.

.. code-block:: python

   from hpc_runner import Job, reload_config

   # Explicitly load a config file for this process (optional)
   reload_config("./hpc-runner.toml")

   # Auto-detects "python" from command → looks up [tools.python]
   job = Job("python train.py", cpu=8)

   # Look up [types.gpu], merge with [defaults], override cpu
   job = Job("python train.py", job_type="gpu", cpu=8)

   # No matching tool — just [defaults]
   job = Job("echo hello")

   # With [tools.fusesoc.options."--tool slang"] in config, the command
   # arguments trigger option-specific overrides automatically:
   job = Job("fusesoc run --tool slang core:v:n")

   result = job.submit()


Job dependencies
----------------

At the low level, you can chain jobs by attaching a dependency to a new job:

.. code-block:: python

   from hpc_runner import Job

   r1 = Job("python preprocess.py", name="pre").submit()
   j2 = Job("python train.py", name="train")
   j2.dependencies = [r1]     # programmatic dependency
   j2.dependency_type = "afterok"
   r2 = j2.submit()


Pipelines (multi-step workflows)
--------------------------------

For larger workflows, use ``Pipeline`` to define steps and dependencies by
name.  When used as a context manager, the pipeline auto-submits on exit and
results are available via the ``results`` property.

.. code-block:: python

   from hpc_runner import Pipeline

   with Pipeline("ml") as p:
       p.add("python preprocess.py", name="preprocess", cpu=8, mem="32G")
       p.add("python train.py", name="train", depends_on=["preprocess"])
       p.add("python evaluate.py", name="evaluate", depends_on=["train"])

   # Auto-submitted when the with-block exits cleanly.
   # Results are accessible on the pipeline object.
   for name, result in p.results.items():
       print(name, result.job_id, result.status)

   p.wait()

Without a context manager, call ``submit()`` explicitly:

.. code-block:: python

   p = Pipeline("ml")
   p.add("make build", name="build")
   p.add("make test", name="test", depends_on=["build"])

   results = p.submit()          # must call manually
   p.wait()

Config-aware pipeline jobs
^^^^^^^^^^^^^^^^^^^^^^^^^^

``Pipeline.add()`` creates jobs through ``Job()``, so every step picks up
``[defaults]`` automatically and the tool is auto-detected from the command.
Use the ``job_type`` keyword to pull in ``[types.*]`` config instead:

.. code-block:: python

   with Pipeline("ml") as p:
       # Auto-detects "python" → picks up [tools.python] config
       p.add("python preprocess.py", name="preprocess")

       # Picks up [types.gpu] config (queue, resources, etc.)
       p.add("python train.py", name="train",
             depends_on=["preprocess"], job_type="gpu")

       # Only [defaults] — no matching tool or type
       p.add("echo done", name="notify", depends_on=["train"])

Keyword arguments override whatever comes from config:

.. code-block:: python

   # [types.gpu] sets cpu=8, but we want 16 for this step
   p.add("python big_train.py", name="train", job_type="gpu", cpu=16)

Per-job dependency types
^^^^^^^^^^^^^^^^^^^^^^^^

By default every dependency uses ``AFTEROK`` (run only if all parents
succeed).  You can set a different dependency type per step:

.. code-block:: python

   from hpc_runner import DependencyType, Pipeline

   with Pipeline("robust") as p:
       p.add("python train.py", name="train")

       # Only runs if train succeeds
       p.add("python evaluate.py", name="evaluate",
             depends_on=["train"],
             dependency_type=DependencyType.AFTEROK)

       # Runs regardless of success/failure (cleanup, notifications, etc.)
       p.add("python notify.py", name="notify",
             depends_on=["train"],
             dependency_type=DependencyType.AFTERANY)

Available types: ``AFTEROK``, ``AFTERANY``, ``AFTER``, ``AFTERNOTOK``.

Choosing a scheduler
^^^^^^^^^^^^^^^^^^^^

By default the scheduler is auto-detected at submit time.  You can pin it at
construction:

.. code-block:: python

   from hpc_runner import Pipeline, get_scheduler

   sge = get_scheduler("sge")

   with Pipeline("build", scheduler=sge) as p:
       p.add("make build", name="build")

Or pass it to ``submit()`` directly:

.. code-block:: python

   p = Pipeline("build")
   p.add("make build", name="build")
   p.submit(scheduler=sge)

Handling submission failures
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

If a scheduler error interrupts submission partway through, the successfully
submitted jobs are preserved.  Call ``submit()`` again to retry only the
remaining jobs:

.. code-block:: python

   from hpc_runner import SchedulerError

   p = Pipeline("etl")
   p.add("python extract.py", name="extract")
   p.add("python transform.py", name="transform", depends_on=["extract"])
   p.add("python load.py", name="load", depends_on=["transform"])

   try:
       p.submit()
   except SchedulerError:
       # extract submitted, transform failed — fix the issue, then:
       p.submit()   # skips extract, retries transform and load


Array jobs
----------

Use ``JobArray`` when you want a scheduler array job (SGE: ``qsub -t``):

.. code-block:: python

   from hpc_runner import Job, JobArray

   base = Job("python work.py", name="work", cpu=2, time="0:30:00")
   array = JobArray(job=base, start=1, end=100, max_concurrent=10)
   array_result = array.submit()

   statuses = array_result.wait()
   print("completed:", sum(1 for s in statuses.values() if s.name == "COMPLETED"))


Errors
------

Every error hpc-runner raises on purpose is an ``HPCToolsError``; anything
else is a bug. All classes are importable from ``hpc_runner``.

.. code-block:: text

   HPCToolsError
   ├── ConfigError                bad config or unknown scheduler name (.path, .line)
   ├── SchedulerError             anything from a scheduler
   │   ├── SchedulerUnavailableError   scheduler binary not on PATH
   │   ├── SchedulerCommandError       command exited non-zero (.argv, .returncode, .stdout, .stderr)
   │   ├── SchedulerTimeoutError       command did not finish in time (.argv, .timeout)
   │   ├── SubmissionError             scheduler rejected the job, or its reply had no job id
   │   │                               (message is the scheduler's stderr; .stderr, .script, .argv)
   │   ├── JobNotFoundError            the scheduler does not know this job id
   │   ├── NotSupportedError           this scheduler cannot honour the requested feature
   │   ├── JobFailedError              from JobResult.result() (.status, .job_id, .exit_code)
   │   └── AccountingNotAvailable      job history (qacct/sacct) is not available
   └── PipelineError              pipeline misuse ("no jobs", "already submitted")
       ├── DuplicateStepError         step name already used
       ├── UnknownStepError           depends_on names an unknown step
       ├── DependencyCycleError       the steps form a cycle
       └── NotSubmittedError          wait() before (all of) the pipeline was submitted

.. code-block:: python

   from hpc_runner import Job, SubmissionError

   try:
       Job("python train.py", queue="gpu.q").submit()
   except SubmissionError as e:
       print("rejected:", e)        # first line of the scheduler's stderr
       print(e.stderr)              # full stderr
       print(e.script)              # the rendered job script

Every scheduler command is logged at ``DEBUG`` on the ``hpc_runner.schedulers``
logger. hpc-runner never configures logging itself; call
``logging.basicConfig(level=logging.DEBUG)`` to see the commands.
