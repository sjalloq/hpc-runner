"""Job detail panel component."""

from __future__ import annotations

from typing import Any

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import Key
from textual.message import Message
from textual.widgets import Button, Static

from hpc_runner.core.job_info import JobInfo


class ButtonBar(Horizontal):
    """Horizontal container with arrow key navigation between buttons."""

    def on_key(self, event: Key) -> None:
        """Handle arrow key navigation between buttons."""
        if event.key not in ("left", "right"):
            return

        buttons = [btn for btn in self.query(Button).results(Button) if not btn.disabled]
        if not buttons:
            return

        focused = self.app.focused
        if not isinstance(focused, Button) or focused not in buttons:
            return

        idx = buttons.index(focused)
        if event.key == "right":
            next_idx = (idx + 1) % len(buttons)
        else:
            next_idx = (idx - 1) % len(buttons)

        buttons[next_idx].focus()
        event.prevent_default()
        event.stop()


class DetailPanel(Vertical):
    """Panel showing detailed information for selected job.

    Styles are defined in monitor.tcss, not DEFAULT_CSS.
    Arrow keys navigate between buttons.
    """

    class ViewLogs(Message):
        """Request to view job logs."""

        def __init__(self, job: JobInfo, stream: str) -> None:
            super().__init__()
            self.job = job
            self.stream = stream  # "stdout" or "stderr"

    class CancelJob(Message):
        """Request to cancel a job."""

        def __init__(self, job: JobInfo) -> None:
            super().__init__()
            self.job = job

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._job: JobInfo | None = None

    def compose(self) -> ComposeResult:
        """Create panel content."""
        yield Static("Select a job to view details", id="no-selection")
        with Vertical(id="detail-content", classes="hidden"):
            # Row 1: ID and Status
            with Horizontal(classes="detail-row"):
                yield Static("Job ID:", classes="detail-label")
                yield Static("", id="detail-id", classes="detail-value")
                yield Static("Status:", classes="detail-label")
                yield Static("", id="detail-status", classes="detail-value")

            # Row 2: Queue and Runtime
            with Horizontal(classes="detail-row"):
                yield Static("Queue:", classes="detail-label")
                yield Static("", id="detail-queue", classes="detail-value")
                yield Static("Runtime:", classes="detail-label")
                yield Static("", id="detail-runtime", classes="detail-value")

            # Row 3: Resources and Node
            with Horizontal(classes="detail-row"):
                yield Static("Resources:", classes="detail-label")
                yield Static("", id="detail-resources", classes="detail-value")
                yield Static("Node:", classes="detail-label")
                yield Static("", id="detail-node", classes="detail-value")

            # Row 4: User and Submit time
            with Horizontal(classes="detail-row"):
                yield Static("User:", classes="detail-label")
                yield Static("", id="detail-user", classes="detail-value")
                yield Static("Submitted:", classes="detail-label")
                yield Static("", id="detail-submitted", classes="detail-value")

            # Row 5: Output path
            with Horizontal(classes="detail-row"):
                yield Static("Output:", classes="detail-label")
                yield Static("", id="detail-output", classes="detail-value")

            # Buttons (styled via monitor.tcss)
            with ButtonBar(id="detail-buttons"):
                yield Button("View stdout", id="btn-stdout")
                yield Button("View stderr", id="btn-stderr")
                yield Button("Cancel", id="btn-cancel")

    def on_mount(self) -> None:
        """Set up the panel."""
        self.border_title = "Job Details"

    def update_job(self, job: JobInfo | None) -> None:
        """Update the panel with job details."""
        self._job = job

        no_selection = self.query_one("#no-selection", Static)
        detail_content = self.query_one("#detail-content", Vertical)

        if job is None:
            no_selection.remove_class("hidden")
            detail_content.add_class("hidden")
            self.border_title = "Job Details"
            return

        no_selection.add_class("hidden")
        detail_content.remove_class("hidden")

        # Update border title
        self.border_title = f"Job: {job.name}"

        # Update fields
        self.query_one("#detail-id", Static).update(job.job_id)
        self.query_one("#detail-status", Static).update(job.status.name)
        self.query_one("#detail-queue", Static).update(job.queue or "—")
        self.query_one("#detail-runtime", Static).update(job.runtime_display)
        self.query_one("#detail-resources", Static).update(job.resources_display)
        self.query_one("#detail-node", Static).update(job.node or "—")
        self.query_one("#detail-user", Static).update(job.user)

        # Format submit time
        if job.submit_time:
            submitted = job.submit_time.strftime("%Y-%m-%d %H:%M:%S")
        else:
            submitted = "—"
        self.query_one("#detail-submitted", Static).update(submitted)

        # Format output path
        if job.stdout_path:
            output = str(job.stdout_path)
            # Truncate if too long
            if len(output) > 50:
                output = "..." + output[-47:]
        else:
            output = "—"
        self.query_one("#detail-output", Static).update(output)

        # Update button states
        btn_stdout = self.query_one("#btn-stdout", Button)
        btn_stderr = self.query_one("#btn-stderr", Button)
        btn_cancel = self.query_one("#btn-cancel", Button)

        btn_stdout.disabled = job.stdout_path is None
        btn_stderr.disabled = job.stderr_path is None
        btn_cancel.disabled = not job.is_active

    @on(Button.Pressed, "#btn-stdout")
    def on_view_stdout(self, event: Button.Pressed) -> None:
        """Handle stdout button click."""
        if self._job:
            self.post_message(self.ViewLogs(self._job, "stdout"))

    @on(Button.Pressed, "#btn-stderr")
    def on_view_stderr(self, event: Button.Pressed) -> None:
        """Handle stderr button click."""
        if self._job:
            self.post_message(self.ViewLogs(self._job, "stderr"))

    @on(Button.Pressed, "#btn-cancel")
    def on_cancel_job(self, event: Button.Pressed) -> None:
        """Handle cancel button click."""
        if self._job:
            self.post_message(self.CancelJob(self._job))
