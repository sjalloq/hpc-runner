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

   from hpc_runner import Host, JobSpec, get_scheduler, load_config, resolve

   config = load_config()
   scheduler = get_scheduler(config=config)
   job = resolve(JobSpec("python train.py", cpu=4, mem="16G"), config, Host.capture())
   result = scheduler.submit(job)
   print(result.wait(), result.returncode)


The three stages
----------------

A job goes through three values, each with one job:

``JobSpec``
   Exactly what you asked for. A plain, mutable dataclass; ``None`` means
   "not specified". It reads no config and no environment.

``resolve(spec, config, host) -> Job``
   The only place merge rules live. Scalars: the spec wins, then the most
   specific config layer, then the package default. Lists append across
   layers (see :ref:`list-merging`); a spec list is one more layer. ``config`` and ``host`` are
   always explicit: ``host`` carries the submit-side facts (environment,
   working directory, user) so the result does not depend on hidden process
   state.

``Job``
   The resolved job: frozen, fully defaulted, ready for a scheduler to
   render. ``job.provenance`` records which layer supplied each value
   (``"spec"``, ``"tools.<name>"``, ``"tools.<name>.options.<key>"``,
   ``"types.<name>"``, ``"defaults"``, ``"package"`` or ``"host"``). For a
   list field it is a tuple of ``(item, layer)`` pairs, one per item.

.. code-block:: python

   from hpc_runner import Host, JobSpec, load_config, resolve

   spec = JobSpec(
       "python train.py",
       name="training_job",
       cpu=4,
       mem="16G",
       time="4:00:00",
       queue="gpu.q",
       project="ml",
       modules=["python/3.11", "cuda/12.0"],
       workdir="/path/to/project",
       stdout="train.out",
       stderr=None,   # None means "merge stderr into stdout"
   )
   job = resolve(spec, load_config(), Host.capture())
   print(job.cpu, job.provenance["cpu"])   # 4 spec

A list command is shell-quoted: ``JobSpec(["bash", "-c", "echo hi there"])``
runs ``bash -c 'echo hi there'``. A string command is used as written.

``venv`` has three states: ``None`` (default) captures ``$VIRTUAL_ENV`` from
the host, a path uses that venv, ``False`` uses none. ``resolve()`` checks
the venv has ``bin/activate``.

String values on a spec follow the same expansion rule as config
(:ref:`variable-expansion`): ``$VAR`` is resolved from ``host.env`` (an
undefined one raises ``ConfigError``), ``$$`` is a literal ``$`` and
``${hpc.cpus}``-style runtime values are rendered by the scheduler. The
command is not expanded.

Raw scheduler flags with no hpc-runner equivalent go in ``scheduler_args``,
keyed by scheduler. Each list is appended to config's ``scheduler_args``
for that scheduler, and only the active scheduler's list is used:

.. code-block:: python

   JobSpec("python train.py", scheduler_args={"sge": ["-l", "gpu=2"]})


Submitting jobs
---------------

Schedulers take their configuration explicitly. ``get_scheduler()`` with no
name auto-detects; with no ``config`` it calls ``load_config()`` afresh.

.. code-block:: python

   from hpc_runner import get_scheduler

   scheduler = get_scheduler("sge", config=config)
   result = scheduler.submit(job)

   # The script submit() would write, for review or tests:
   print(scheduler.preview(job))

Every scheduler has the same contract:
``submit(job, *, keep_script=False, verbose=False)``,
``preview(job, *, verbose=False)`` (``verbose`` shows module command output
in the job instead of only on failure), ``cancel(job_id)``,
``get_statuses(job_ids)`` (one bulk query, a dict of id to status),
``get_status(job_id)``, ``get_exit_code(job_id)`` and ``result(job_id)``.

Interactive or batch
^^^^^^^^^^^^^^^^^^^^

``interactive`` is a job attribute. From the API it defaults to ``False``
(batch: submit and return); a tool or type may set ``interactive = true`` in
config, and the spec wins over config. An interactive job runs attached
(``qrsh`` on SGE) and ``submit()`` returns once it has finished, with a
``JobResult`` whose ``job_id`` is ``None`` and whose ``exit_code`` is set.

.. code-block:: python

   result = scheduler.submit(resolve(JobSpec("make sim", interactive=True), config, host))
   print(result.exit_code)


Monitoring jobs
---------------

``submit()`` returns a ``JobResult`` handle which can be polled or waited on.

.. code-block:: python

   from hpc_runner import JobStatus

   while not result.is_complete:
       print(result.job_id, result.status)

   if result.status == JobStatus.COMPLETED:
       print("ok:", result.read_stdout(tail=20))
   else:
       print("failed:", result.read_stderr(tail=50))

``wait(poll_interval=5.0, timeout=None, unknown_after=60.0)`` blocks until
the job reaches a terminal status (``COMPLETED``, ``FAILED``, ``CANCELLED``,
``TIMEOUT`` or ``SKIPPED``; ``JobStatus.is_terminal``). If the scheduler
reports ``UNKNOWN`` continuously for ``unknown_after`` seconds (for example
the job has aged out of accounting), ``wait()`` returns ``UNKNOWN`` rather
than polling forever; ``timeout`` raises ``TimeoutError``.
``ArrayJobResult.wait()`` takes the same arguments, returns each task's
status by index, and polls all tasks with one ``get_statuses`` call per
tick; ``task_status(n)`` is that task's own status.

``SKIPPED`` means the job did not run because a dependency was not
satisfied (see below).

Or wait and get the exit code in one call; a job that does not finish
``COMPLETED`` (including ``SKIPPED``) raises ``JobFailedError``, which
carries the status:

.. code-block:: python

   exit_code = result.result()

Cancel a job:

.. code-block:: python

   result.cancel()

Reconnect to a job by id, without the ``Job`` that created it (``job`` is
``None`` on the handle):

.. code-block:: python

   result = scheduler.result("12345")
   print(result.status)


Configuration-driven jobs
-------------------------

``resolve()`` applies ``[defaults]``, then the matched ``[tools.<name>]``
section, then the spec. The tool name is auto-detected from the command
(first word, path stripped). Set ``type`` on the spec to use a
``[types.*]`` entry instead: its listed ``tools`` then the type's own keys
(this skips tool auto-detection). See :doc:`configuration` for the chain.

If the tool has ``[tools.<name>.options]`` entries defined in the config, the
**full command string** (not just the first word) is used to match
argument-specific overrides.  See :ref:`tool-option-specialisation` for the
matching rules.

.. code-block:: python

   from hpc_runner import Host, JobSpec, load_config, resolve

   config = load_config("./hpc-runner.toml")
   host = Host.capture()

   # Auto-detects "python" from command -> looks up [tools.python]
   job = resolve(JobSpec("python train.py", cpu=8), config, host)

   # [defaults], then the tools [types.gpu] lists, then [types.gpu]; override cpu
   job = resolve(JobSpec("python train.py", type="gpu", cpu=8), config, host)

   # No matching tool -- just [defaults]
   job = resolve(JobSpec("echo hello"), config, host)

   # With [tools.fusesoc.options."--tool slang"] in config, the command
   # arguments trigger option-specific overrides automatically:
   job = resolve(JobSpec("fusesoc run --tool slang core:v:n"), config, host)

A list set on the spec (``modules``, ``modules_path``, ``resources``, ...)
appends to the config's list, exactly like a config layer. Use ``Set`` to
replace it or ``Edit`` to remove items:

.. code-block:: python

   from hpc_runner import Edit, JobSpec, Set

   JobSpec("python train.py", modules=["cuda/12"])          # config's + cuda/12
   JobSpec("python train.py", modules=Set(["python/3.12"]))  # only python/3.12
   JobSpec("python train.py", modules=Edit(remove=["python/3.11"], add=["python/3.12"]))


Job dependencies
----------------

``JobSpec.after()`` records a dependency on a submitted job's result. Each
call appends, with its own type (``AFTEROK`` by default):

.. code-block:: python

   from hpc_runner import DependencyType, JobSpec

   r1 = scheduler.submit(resolve(JobSpec("python preprocess.py", name="pre"), config, host))
   spec = JobSpec("python train.py", name="train").after(r1)
   spec.after(other_result, type=DependencyType.AFTERANY)
   r2 = scheduler.submit(resolve(spec, config, host))

The types are ``AFTEROK``, ``AFTERANY``, ``AFTERNOTOK`` and ``AFTER``
(start-after). Depending on an ``ArrayJobResult`` records the array's task
indices, so the dependent waits on every task. ``parse_dependency_string``
(in ``hpc_runner.core.dependency``) turns the command line's
``TYPE:ID[:ID...][,TYPE:ID...]`` grammar (a bare ``ID`` is afterok) into the
same ``Dependency`` list.

On SGE, ``-hold_jid`` is afterany; afterok and afternotok are emulated by an
exit-code guard in the dependent's script (``[schedulers.sge]
emulate_afterok``), and ``AFTER`` raises ``NotSupportedError``. A dependent
whose dependency is not satisfied exits with the reserved code 113 and
reports ``JobStatus.SKIPPED``; ``returncode`` is 113. See :doc:`sge`.

``Pipeline.wait(poll_interval=5.0, timeout=None, unknown_after=60.0)`` polls
every step with one ``get_statuses`` call per tick.


Pipelines (multi-step workflows)
--------------------------------

For larger workflows, use ``Pipeline`` to define steps and dependencies by
name. Each step is a ``JobSpec`` (or a command string), resolved against the
pipeline's ``config`` when the pipeline is submitted. When used as a context
manager, the pipeline auto-submits on exit and results are available via the
``results`` property.

.. code-block:: python

   from hpc_runner import JobSpec, Pipeline, load_config

   config = load_config()

   with Pipeline("ml", config=config) as p:
       p.add(JobSpec("python preprocess.py", cpu=8, mem="32G"), name="preprocess")
       p.add("python train.py", name="train", depends_on=["preprocess"])
       p.add("python evaluate.py", name="evaluate", depends_on=["train"])

   # Auto-submitted when the with-block exits cleanly.
   # Results are accessible on the pipeline object.
   for name, result in p.results.items():
       print(name, result.job_id, result.status)

   p.wait()

Without a context manager, call ``submit()`` explicitly:

.. code-block:: python

   p = Pipeline("ml", config=config)
   p.add("make build", name="build")
   p.add("make test", name="test", depends_on=["build"])

   results = p.submit()          # must call manually
   p.wait()

Each step's job name is ``<pipeline>_<step>``. The host defaults to
``Host.capture()`` at submit time; pass ``host=`` to fix it.

Config-aware pipeline jobs
^^^^^^^^^^^^^^^^^^^^^^^^^^

Every step picks up ``[defaults]`` and the tool auto-detected from its
command. Set ``type`` on the spec to pull in ``[types.*]`` config instead:

.. code-block:: python

   with Pipeline("ml", config=config) as p:
       # Auto-detects "python" -> picks up [tools.python] config
       p.add("python preprocess.py", name="preprocess")

       # Picks up [types.gpu] config (queue, resources, etc.)
       p.add(JobSpec("python train.py", type="gpu"), name="train",
             depends_on=["preprocess"])

       # Only [defaults] -- no matching tool or type
       p.add("echo done", name="notify", depends_on=["train"])

Spec fields override whatever comes from config:

.. code-block:: python

   # [types.gpu] sets cpu=8, but we want 16 for this step
   p.add(JobSpec("python big_train.py", type="gpu", cpu=16), name="train")

Per-job dependency types
^^^^^^^^^^^^^^^^^^^^^^^^

By default every dependency uses ``AFTEROK`` (run only if all parents
succeed).  You can set a different dependency type per step:

.. code-block:: python

   from hpc_runner import DependencyType, Pipeline

   with Pipeline("robust", config=config) as p:
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

   sge = get_scheduler("sge", config=config)

   with Pipeline("build", config=config, scheduler=sge) as p:
       p.add("make build", name="build")

Or pass it to ``submit()`` directly:

.. code-block:: python

   p = Pipeline("build", config=config)
   p.add("make build", name="build")
   p.submit(scheduler=sge)

Handling submission failures
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

If a scheduler error interrupts submission partway through, the successfully
submitted jobs are preserved.  Call ``submit()`` again to retry only the
remaining jobs:

.. code-block:: python

   from hpc_runner import SchedulerError

   p = Pipeline("etl", config=config)
   p.add("python extract.py", name="extract")
   p.add("python transform.py", name="transform", depends_on=["extract"])
   p.add("python load.py", name="load", depends_on=["transform"])

   try:
       p.submit()
   except SchedulerError:
       # extract submitted, transform failed -- fix the issue, then:
       p.submit()   # skips extract, retries transform and load


Array jobs
----------

Set ``array`` on the spec for a scheduler array job (SGE: ``qsub -t``). The
range is ``START-END[:STEP][%MAX_CONCURRENT]``. Array jobs are always batch;
``submit()`` returns an ``ArrayJobResult`` whose ``job_id`` is the base id:

.. code-block:: python

   spec = JobSpec("python work.py", name="work", cpu=2, time="0:30:00", array="1-100%10")
   array_result = scheduler.submit(resolve(spec, config, host))

   statuses = array_result.wait()          # {task index: JobStatus}
   print("completed:", sum(1 for s in statuses.values() if s.name == "COMPLETED"))

The local scheduler supports neither arrays nor dependencies and raises
``NotSupportedError`` rather than running something different.


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

   from hpc_runner import SubmissionError

   try:
       scheduler.submit(resolve(JobSpec("python train.py", queue="gpu.q"), config, host))
   except SubmissionError as e:
       print("rejected:", e)        # first line of the scheduler's stderr
       print(e.stderr)              # full stderr
       print(e.script)              # the rendered job script

Every scheduler command is logged at ``DEBUG`` on the ``hpc_runner.schedulers``
logger. hpc-runner never configures logging itself; call
``logging.basicConfig(level=logging.DEBUG)`` to see the commands.
