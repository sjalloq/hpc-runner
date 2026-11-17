Development
===========

Setup
-----

.. code-block:: bash

   git clone https://github.com/sjalloq/hpc-runner
   cd hpc-runner
   source sourceme           # creates .venv and installs the dev extras
   source sourceme --clean   # rebuild it

Layout
------

- ``src/hpc_runner/core/``: ``JobSpec`` → ``resolve()`` → ``Job``, the
  config loader and schema, units, expansion, results.
- ``src/hpc_runner/schedulers/``: one package per scheduler (``sge``,
  ``local``) plus the registry. ``_subprocess.run_command``
  is the only place a scheduler CLI is run; tests patch it, never
  ``subprocess``.
- ``src/hpc_runner/cli/``: the ``hpc`` command group.
- ``src/hpc_runner/tui/``: ``hpc monitor``.
- ``src/hpc_runner/templates/`` and ``schedulers/*/templates/``: the Jinja2
  job scripts.
- ``docs/source/``: this documentation. ``docs/dev/``: planning, design and
  review notes, one folder per piece of work; not built by Sphinx.

Architecture
------------

A job is three values: ``JobSpec`` → ``resolve(spec, config, host)`` →
``Job``. Everything else renders, submits or watches a ``Job``.

Core (``src/hpc_runner/core/``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- ``JobSpec`` (``spec.py``): what the caller asked for, ``None`` meaning
  unspecified; it reads no config and no environment. ``after()`` appends
  per-parent dependencies.
- ``resolve()`` (``resolve.py``): the only place the merge rules live: the
  fixed layer chain, the list rule (``merge.py``: append, ``Set``, ``Edit``),
  ``$VAR`` and ``${hpc.*}`` expansion (``expand.py``), ``mem`` and ``time``
  parsing (``units.py``), and the provenance of every value.
- ``Job`` (``job.py``): frozen and fully resolved; schedulers render it and
  never mutate it.
- ``Host`` (``host.py``): submit-side facts (environment, cwd, user) passed
  explicitly to ``resolve``; ``Host.capture()`` snapshots the process.
- ``JobResult`` and ``ArrayJobResult`` (``result.py``): a handle with the
  status, ``wait()``, ``result()`` and the output paths recorded at submit;
  ``scheduler.result(job_id)`` rebuilds one from an id.
- ``JobStatus`` (``result.py``): PENDING, RUNNING, SUSPENDED, COMPLETED,
  FAILED, CANCELLED, TIMEOUT, KILLED (a resource-limit kill the scheduler
  does not name), SKIPPED, UNKNOWN. ``reduce_statuses()`` gives one status
  for several (an array, a pipeline step): active while any is, COMPLETED
  only when all are, otherwise the worst.
- ``ResourceSet`` (``resources.py``): named resources (gpu, licences, ...).
- ``HPCConfig`` (``config.py``, schema in ``schema.py``): typed, validated
  TOML (``config_version = 1``; unknown keys are errors) with two-level
  discovery, ``$HPC_RUNNER_CONFIG`` or ``<git-root>/hpc-runner.toml`` then
  ``./hpc-runner.toml`` as a local override. Always passed explicitly; there
  is no global cache.

Schedulers (``src/hpc_runner/schedulers/``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- ``BaseScheduler`` (``base.py``) defines ``submit(job)``, ``preview(job)``,
  ``cancel()``, ``get_statuses()`` (batched), ``get_exit_code()``,
  ``result(job_id)`` and ``task_job_id(base, index)``. The settings a backend
  accepts under ``[schedulers.<name>]`` are its ``CONFIG_KEYS``; the schema
  rejects anything else. The base class reads the top-level
  ``module_init_script`` for the shared environment block.
- Every backend declares ``BATCH_OPTIONS`` and ``INTERACTIVE_OPTIONS``: for
  each of ``SCHEDULER_OPTIONS`` (``core/job.py``) whether it maps the
  option, rejects it, or why there is nothing to map. ``check_options(job)``
  refuses a job that sets a rejected one.
  ``tests/unit/test_schedulers/test_option_table.py`` walks every cell, so
  a new option or backend fails the suite until its cells are filled in.
- ``run_command()`` (``_subprocess.py``) is the seam: the only place a
  scheduler CLI runs. It logs the argv, records a trace when one is active
  and turns failures into ``SchedulerError`` subclasses. Tests patch it
  (``tests/fakes.py``), never ``subprocess.run``.
- ``sge/``: ``parser.py`` holds every qstat, qacct and qsub parser;
  ``args.py`` is one ``SchedulerArg`` subclass per option
  (``core/descriptors.py``), each with ``to_args()`` and ``to_directive()``;
  ``scheduler.py`` is submit, status and cancel. The PE name and the
  resource names come from ``[schedulers.sge]``; nothing is hard-coded.
- ``local/``: runs the job as a subprocess on this machine with the same
  rendered environment.
- The scheduler is named, never detected: ``get_scheduler(name)``, else
  ``HPC_SCHEDULER``, else the config's top-level ``scheduler`` key; none is
  a ``ConfigError``. ``register_scheduler()`` adds a custom backend.

CLI (``src/hpc_runner/cli/``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

One entry point, ``hpc``, with ``run``, ``status``, ``cancel``, ``kill``,
``config``, ``monitor`` and ``trace``, styled with ``rich-click``.

- ``hpc run`` is interactive by default (``qrsh``): a hand-typed run wants a
  window to appear or a result to wait for. ``--batch`` submits and returns;
  arrays are always batch; ``--wait`` needs ``--batch``.
- Every short letter belongs to ``hpc run``. Raw scheduler flags travel in
  ``-S/--scheduler-args 'FLAGS'`` (repeatable, split with ``shlex``) and land
  on ``JobSpec.scheduler_args[<scheduler>]``; a flag ``hpc run`` does not
  define is a usage error. Options stop at the first plain word
  (``allow_interspersed_args=False``), so the command's own flags pass
  untouched; a command that starts with a dash needs ``--`` first. How the
  parsing got here: ``docs/dev/cli/RUN_ARG_PARSING.md``.
- Policy lives in config: which modules load, which paths are searched and
  whether the environment is inherited are ``[types.x]`` settings, not
  options. Per-run knobs (type, cpu, mem, time, queue, name, array, depend,
  stdout/stderr, directory, env, resource) stay on the command line.

Other packages
~~~~~~~~~~~~~~

- ``workflow/``: ``Pipeline``, a dependency graph of ``JobSpec`` steps that
  name each other; each is resolved and its dependencies injected at submit
  time. ``results`` is a read-only mapping and ``wait()`` returns statuses.
- ``templates/``: Jinja2 job scripts. Each scheduler template
  (``schedulers/sge/templates/batch.sh.j2`` and ``interactive.sh.j2``,
  ``schedulers/local/templates/job.sh.j2``) owns its directives and exec line
  and includes the shared ``templates/_environment.sh.j2``. Every
  interpolated value goes through the ``sh`` filter (``templates/engine.py``).
- ``trace/``: the seam records every scheduler call as JSON (``hpc --trace
  DIR`` or ``[trace] enabled = true``), with the job script on the call
  that submitted it (``note_script``); ``cli/main.py`` adds one record per
  command line (``Invocation``): argv, config files, output, error, exit
  code. Design in ``docs/dev/trace/DESIGN.md``.
- ``tui/``: ``HpcMonitorApp`` (``app.py``) behind ``hpc monitor``; every
  style is in ``styles/monitor.tcss``; ``snapshot.py`` renders a screen for
  review. Styling rules: ``docs/dev/tui/TEXTUAL_STYLING_COOKBOOK.md``.

Tests
-----

.. code-block:: bash

   pytest                                # unit suite, tests/unit
   pytest tests/unit/test_core           # one area
   mypy src/hpc_runner
   ruff check src/hpc_runner tests && ruff format src/hpc_runner tests

``tests/unit/`` runs without a scheduler; everything the schedulers would
run is faked at ``run_command``. ``tests/unit/golden/`` holds the rendered
job script for each scheduler and case, and every script rendered anywhere
in the suite is checked with ``bash -n``. After an intended template change:

.. code-block:: bash

   pytest tests/unit/test_golden.py --update-golden

``tests/unit/fixtures/sge/<flavour>/`` holds real scheduler output captured
from the testbed by ``hpc-testbed --scheduler sge --flavour <name> capture``
(``tests/integration/capture.py``; the scenarios and ids are in that
directory's ``manifest.json``), one directory per SGE flavour (``soge``,
``ocs``). The ``mock_sge_commands`` fixture (``tests/conftest.py``) answers
by whole argv from those files through ``make_sge_responder``
(``tests/fakes.py``), the parser tests read them directly, and every test
that uses them runs once per flavour through ``sge_fixtures``. No scheduler
text is typed from memory: a scenario the captures lack is a reason to
extend the capture. Regenerate a flavour against its running stack when a
scenario changes. The manifest also records the argv behind every file.

The docs are tested too: every Python snippet in the README and the API
guide compiles (those marked ``# doctest: local`` run against the local
scheduler), every TOML snippet validates, and the configuration and CLI
references must list every config key and every ``hpc run`` option.

Integration tests against SGE
-----------------------------

``tests/integration/`` runs against a real Grid Engine in Docker: a
qmaster, two execution hosts and a login container, all in ``testbed/``.

.. code-block:: bash

   hpc-testbed --scheduler sge up            # build the image and start the cluster (~20 s)
   hpc-testbed --scheduler sge pytest        # run the suite inside the login container
   hpc-testbed --scheduler sge shell         # a shell there, for qstat/qacct by hand
   hpc-testbed --scheduler sge down          # remove containers, network and volumes
   hpc-testbed --scheduler sge pytest --up   # up, pytest, down in one command

``hpc-testbed --scheduler sge pytest -- -k submit`` passes options through and
``--py 3.11`` picks the Python the suite runs under. One directory per
scheduler release lives under ``testbed/<scheduler>/<flavour>/`` (``sge/soge``
is Debian trixie gridengine 8.1.9, ``sge/ocs`` is Open Cluster Scheduler).
``--scheduler`` is required and names the scheduler; ``--flavour`` names the
release and defaults per scheduler (``soge``, Slurm ``24.11``, PBS
``openpbs``). A flavour never selects a scheduler.
The suite runs inside the ``login`` container through the ``cluster``
fixture (``tests/integration/cluster.py``); it skips when no scheduler is
reachable and ``--require-cluster`` fails instead. Tests that assert on one
scheduler's own output carry a ``scheduler`` marker; contract tests run on
every scheduler. CI runs the suite on every flavour for every push.
``testbed/README.md`` describes the stack and the fixture;
``docs/dev/testbed/`` has the design, the plan and the review, which logs
every divergence and bug as a ``D-T`` item. Tests that pin a known bug are
marked ``xfail`` with the bug's id. Nothing captured from a real work
cluster goes into the repository.

Documentation
-------------

.. code-block:: bash

   make -C docs html SPHINXOPTS="-W --keep-going"   # warnings are errors

The site at https://sjalloq.github.io/hpc-runner/ is rebuilt from ``main``
by the ``docs`` workflow.

Developer notes
---------------

Planning, design and review documents live under ``docs/dev/``, one folder
per piece of work, never at the repository root. They are working notes,
not this documentation, and Sphinx does not build them. Each piece of work
keeps a ``DECISIONS.md`` of numbered items (``D1``.. for the core
restructure, ``D-T`` for the testbed, ``D-TR`` for trace, ``D-S`` for Slurm)
recording what was decided and why, so a commit message can describe a
change without re-arguing it. Folders today: ``restructure/``, ``testbed/``,
``tui/``, ``cli/``, ``trace/``, ``test-review/``, ``slurm/``.

Releases
--------

The version comes from the git tag (``hatch-vcs``). Pushing a tag ``vX.Y.Z``
on a green ``main`` runs the ``publish`` workflow, which builds and uploads
to PyPI. There are no merge commits: work is fast-forwarded onto ``main``.
