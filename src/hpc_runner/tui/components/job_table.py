"""Job table widget for displaying HPC jobs."""

from textual.message import Message
from textual.widgets import DataTable

from hpc_runner.core.job_info import JobInfo
from hpc_runner.core.result import JobStatus


class JobTable(DataTable):
    """DataTable for displaying HPC jobs.

    Displays job information in a tabular format with columns for
    ID, Name, Queue, Status, Runtime, and Resources.

    Messages:
        JobSelected: Emitted when a job row is highlighted/selected.
    """

    class JobSelected(Message):
        """Message sent when a job is selected in the table."""

        def __init__(self, job_id: str, job_info: JobInfo | None = None) -> None:
            self.job_id = job_id
            self.job_info = job_info
            super().__init__()

    # Column definitions: (key, label, width)
    COLUMNS = [
        ("job_id", "ID", 8),
        ("name", "Name", 20),
        ("queue", "Queue", 12),
        ("status", "Status", 10),
        ("runtime", "Runtime", 10),
        ("resources", "Resources", 12),
    ]

    def __init__(
        self,
        *,
        show_cursor: bool = True,
        zebra_stripes: bool = True,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Initialize the job table.

        Args:
            show_cursor: Whether to show the cursor/selection.
            zebra_stripes: Whether to alternate row colors.
            id: Widget ID.
            classes: CSS classes.
        """
        super().__init__(
            show_cursor=show_cursor,
            zebra_stripes=zebra_stripes,
            cursor_type="row",
            id=id,
            classes=classes,
        )
        self._jobs: dict[str, JobInfo] = {}

    def on_mount(self) -> None:
        """Set up columns when table is mounted."""
        for key, label, width in self.COLUMNS:
            self.add_column(label, key=key, width=width)

    def update_jobs(self, jobs: list[JobInfo]) -> None:
        """Update the table with a new list of jobs.

        Args:
            jobs: List of JobInfo objects to display.
        """
        # Clear existing data
        self.clear()
        self._jobs.clear()

        # Add new rows
        for job in jobs:
            self._jobs[job.job_id] = job
            self.add_row(
                job.job_id,
                job.name,
                job.queue or "—",
                self._format_status(job.status),
                job.runtime_display,
                job.resources_display,
                key=job.job_id,
            )

    def _format_status(self, status: JobStatus) -> str:
        """Format status for display with color hints.

        The actual coloring is done via CSS classes, but we return
        a clean status string here.
        """
        status_map = {
            JobStatus.RUNNING: "RUNNING",
            JobStatus.PENDING: "PENDING",
            JobStatus.COMPLETED: "COMPLETE",
            JobStatus.FAILED: "FAILED",
            JobStatus.CANCELLED: "CANCEL",
            JobStatus.TIMEOUT: "TIMEOUT",
            JobStatus.UNKNOWN: "UNKNOWN",
        }
        return status_map.get(status, str(status.name))

    def on_data_table_row_highlighted(
        self, event: DataTable.RowHighlighted
    ) -> None:
        """Handle row highlight - emit JobSelected message."""
        if event.row_key is not None:
            job_id = str(event.row_key.value)
            job_info = self._jobs.get(job_id)
            self.post_message(self.JobSelected(job_id, job_info))

    def get_selected_job(self) -> JobInfo | None:
        """Get the currently selected job.

        Returns:
            The selected JobInfo, or None if nothing selected.
        """
        if self.cursor_row is not None and self.cursor_row >= 0:
            try:
                row_key = self.get_row_at(self.cursor_row)
                if row_key:
                    # row_key is a tuple of cell values, first is job_id
                    job_id = str(row_key[0])
                    return self._jobs.get(job_id)
            except Exception:
                pass
        return None

    @property
    def job_count(self) -> int:
        """Get the number of jobs in the table."""
        return len(self._jobs)

    @property
    def is_empty(self) -> bool:
        """Check if the table is empty."""
        return len(self._jobs) == 0
