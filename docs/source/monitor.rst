Job monitor
===========

``hpc monitor`` opens a terminal dashboard of the scheduler's jobs, built
with `Textual <https://textual.textualize.io/>`_. It uses the same
scheduler detection and config as ``hpc run``.

.. code-block:: bash

   hpc monitor
   hpc --scheduler sge monitor

The screen has an **Active** tab (pending and running jobs, refreshed on a
timer) and a **Completed** tab (recent accounting records), a filter line
and a detail panel for the selected job.

Keys
----

.. list-table::
   :header-rows: 1
   :widths: 15 85

   * - Key
     - Action
   * - ``q``
     - Quit.
   * - ``r``
     - Refresh now.
   * - ``u``
     - Toggle between your jobs and everyone's.
   * - ``/``
     - Focus the search box; the table filters as you type.
   * - ``Enter``
     - Open the full details of the selected job (``qstat -j`` fields,
       output paths).
   * - ``?``
     - Show the key help popup.
   * - ``s``
     - Save a screenshot (SVG) to the current directory.
   * - ``Tab``
     - Move focus between the table, the filter line and the tab bar;
       the arrow keys on the tab bar switch between Active and Completed.
