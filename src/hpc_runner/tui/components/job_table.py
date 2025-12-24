"""Job table widget for displaying HPC jobs."""

from textual.events import Resize
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

    # Column definitions: (key, label, fixed_width)
    # Fixed columns have set widths; "name" gets remaining space
    FIXED_COLUMNS = [
        ("job_id", "ID", 10),
        ("user", "User", 14),
        ("queue", "Queue", 12),
        ("status", "Status", 10),
        ("runtime", "Runtime", 12),
        ("slots", "Slots", 6),
    ]
    # Name column expands to fill remaining space
    NAME_COL_MIN = 15  # Minimum width for name column
    # Overhead: border(2) + CSS padding(4) + scrollbar(1) + column spacing(~14 for 7 cols)
    TABLE_OVERHEAD = 21

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
        self._name_col_width = 20  # Default, will be recalculated

    def _calculate_name_width(self, available_width: int) -> int:
        """Calculate the width for the name column based on available space."""
        # Sum of fixed column widths
        fixed_total = sum(w for _, _, w in self.FIXED_COLUMNS)
        remaining = available_width - fixed_total - self.TABLE_OVERHEAD
        return max(self.NAME_COL_MIN, remaining)

    def on_mount(self) -> None:
        """Set up columns when table is mounted."""
        self.border_title = "Jobs"
        # Widget size is 0 on mount, use app console size instead
        console_width = self.app.console.size.width
        self._name_col_width = self._calculate_name_width(console_width)
        self._setup_columns()

    def on_resize(self, event: Resize) -> None:
        """Handle resize events to adjust column widths."""
        new_width = self._calculate_name_width(event.size.width)
        if new_width != self._name_col_width:
            self._name_col_width = new_width
            # Columns are already set up, just refresh the data
            # The truncation happens in update_jobs/add_row

    def _setup_columns(self) -> None:
        """Set up the table columns."""
        # Add ID column first
        self.add_column("ID", key="job_id", width=10)
        # Add Name column with calculated width
        self.add_column("Name", key="name", width=self._name_col_width)
        # Add remaining fixed columns
        for key, label, width in self.FIXED_COLUMNS[1:]:  # Skip job_id
            self.add_column(label, key=key, width=width)

    def _truncate_name(self, name: str) -> str:
        """Truncate job name to fit in the name column."""
        if len(name) <= self._name_col_width:
            return name
        # Truncate and add ellipsis
        return name[: self._name_col_width - 1] + "…"

    def update_jobs(self, jobs: list[JobInfo]) -> None:
        """Update the table with a new list of jobs.

        Args:
            jobs: List of JobInfo objects to display.
        """
        # Save current selection to restore after update
        selected_job_id: str | None = None
        if self.cursor_row is not None and self.cursor_row >= 0:
            try:
                row_key = self.get_row_at(self.cursor_row)
                if row_key:
                    selected_job_id = str(row_key[0])
            except Exception:
                pass

        # Clear existing data
        self.clear()
        self._jobs.clear()

        # Add new rows
        for job in jobs:
            self._jobs[job.job_id] = job
            self.add_row(
                job.job_id,
                self._truncate_name(job.name),
                job.user,
                job.queue or "—",
                self._format_status(job.status),
                job.runtime_display,
                str(job.cpu) if job.cpu is not None else "—",
                key=job.job_id,
            )

        # Restore selection if the job still exists
        if selected_job_id and selected_job_id in self._jobs:
            try:
                self.move_cursor(row=self._get_row_index(selected_job_id))
            except Exception:
                pass

    def _get_row_index(self, job_id: str) -> int | None:
        """Get the row index for a job ID."""
        for idx, row_key in enumerate(self.rows.keys()):
            if str(row_key.value) == job_id:
                return idx
        return None

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
