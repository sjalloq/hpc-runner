HPC Runner
==========

**hpc-runner** is a front end for submitting jobs to an HPC scheduler by
tool name or job type. The config file says what an Xcelium run, a lint or
an xterm needs (queue, slots, memory, licences, Environment Modules) and
``hpc run`` turns that into the scheduler's own submission, so the same
command gives every user the same tool versions.

Scheduler compatibility
-----------------------

.. list-table::
   :header-rows: 1
   :widths: 30 15 55

   * - Scheduler
     - Status
     - Notes
   * - Sun Grid Engine / Open Grid Scheduler
     - Supported
     - ``qsub``, ``qrsh``, ``qstat``, ``qacct``, ``qdel``. The integration
       suite runs against gridengine 8.1.9 in CI; see :doc:`sge`.
   * - Local
     - Supported
     - Runs the job on this machine with the same module environment, for a
       workstation without a scheduler.
   * - Slurm
     - Planned
     -
   * - PBS / Torque
     - Planned
     -
   * - LSF
     - Planned
     -

A scheduler is listed as supported once the integration suite passes
against it. Detection order and how to force one: :doc:`getting_started`.

.. toctree::
   :maxdepth: 2
   :caption: Contents:

   getting_started
   configuration
   cli
   sge
   programmatic_api
   monitor
   development
