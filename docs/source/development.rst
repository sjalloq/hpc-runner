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
  ``local``) plus detection and the registry. ``_subprocess.run_command``
  is the only place a scheduler CLI is run; tests patch it, never
  ``subprocess``.
- ``src/hpc_runner/cli/``: the ``hpc`` command group.
- ``src/hpc_runner/tui/``: ``hpc monitor``.
- ``src/hpc_runner/templates/`` and ``schedulers/*/templates/``: the Jinja2
  job scripts.
- ``docs/source/``: this documentation. ``docs/dev/``: planning, design and
  review notes, one folder per piece of work; not built by Sphinx.

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

The docs are tested too: every Python snippet in the README and the API
guide compiles (those marked ``# doctest: local`` run against the local
scheduler), every TOML snippet validates, and the configuration and CLI
references must list every config key and every ``hpc run`` option.

Integration tests against SGE
-----------------------------

``tests/integration/`` runs against a real Grid Engine in Docker: a
qmaster, two execution hosts and a login container, all in ``testbed/``.

.. code-block:: bash

   make -C testbed sge-up      # build the image and start the cluster (~20 s)
   make -C testbed sge-test    # run the suite inside the login container
   make -C testbed sge-shell   # a shell there, for qstat/qacct by hand
   make -C testbed sge-down    # remove containers, network and volumes

``PY=3.11`` selects the Python the suite runs under and
``PYTEST_ARGS="-k submit -x"`` passes options through. The same job runs
in CI for every push. ``testbed/README.md`` describes the cluster and the
``cluster`` fixture; ``docs/dev/testbed/`` has the design and the review.
Tests that pin a known bug are marked ``xfail`` with the bug's id.

Documentation
-------------

.. code-block:: bash

   make -C docs html SPHINXOPTS="-W --keep-going"   # warnings are errors

The site at https://sjalloq.github.io/hpc-runner/ is rebuilt from ``main``
by the ``docs`` workflow.

Releases
--------

The version comes from the git tag (``hatch-vcs``). Pushing a tag ``vX.Y.Z``
on a green ``main`` runs the ``publish`` workflow, which builds and uploads
to PyPI. There are no merge commits: work is fast-forwarded onto ``main``.
