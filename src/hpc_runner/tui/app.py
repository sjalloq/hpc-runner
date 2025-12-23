"""Main HPC Monitor TUI application.

Uses modern Textual patterns:
- Reactive attributes for automatic UI updates
- run_worker for async scheduler calls
- set_interval for auto-refresh
- Message-based event handling
"""

import os
import socket
from pathlib import Path
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import HorizontalGroup, Vertical
from textual.reactive import reactive
from textual.theme import Theme
from textual.widgets import Header, Static, TabbedContent, TabPane

from hpc_runner.core.job_info import JobInfo
from hpc_runner.schedulers import get_scheduler
from hpc_runner.tui.components import (
    FilterPanel,
    FilterStatusLine,
    HelpPopup,
    JobTable,
)
from hpc_runner.tui.providers import JobProvider


# Custom theme inspired by Nord color palette for a muted, professional look.
# NOTE: We intentionally do NOT set 'background' or 'foreground' here.
# This allows the terminal's own colors to show through (transparency).
# The theme only defines accent colors used for highlights and status.
HPC_MONITOR_THEME = Theme(
    name="hpc-monitor",
    primary="#88C0D0",  # Muted teal (not bright blue)
    secondary="#81A1C1",  # Lighter blue-gray
    accent="#B48EAD",  # Muted purple
    success="#A3BE8C",  # Muted green
    warning="#EBCB8B",  # Muted yellow
    error="#BF616A",  # Muted red
    surface="#3B4252",  # For elevated surfaces
    panel="#434C5E",  # Panel accents
    dark=True,
)


class HpcMonitorApp(App[None]):
    """Textual app for monitoring HPC jobs.

    Attributes:
        refresh_interval: Seconds between auto-refresh of job data.
        user_filter: Filter jobs by "me" (current user) or "all" users.
        auto_refresh_enabled: Whether auto-refresh is active.
    """

    TITLE = "hpc monitor"

    CSS_PATH: ClassVar[Path] = Path(__file__).parent / "styles" / "monitor.tcss"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("u", "toggle_user", "Toggle User"),
        Binding("/", "filter_search", "Search", show=False),
        Binding("s", "screenshot", "Screenshot", show=False),
        Binding("question_mark", "help", "Help", show=False),
    ]

    # Reactive attributes - changes automatically trigger watch methods
    user_filter: reactive[str] = reactive("me")
    auto_refresh_enabled: reactive[bool] = reactive(True)

    def __init__(self, refresh_interval: int = 10) -> None:
        """Initialize the HPC Monitor app.

        Args:
            refresh_interval: Seconds between auto-refresh cycles.
        """
        super().__init__()
        self._refresh_interval = refresh_interval
        self._user = os.environ.get("USER", "unknown")
        self._hostname = socket.gethostname().split(".")[0]  # Short hostname

        # Initialize scheduler and job provider
        self._scheduler = get_scheduler()
        self._job_provider = JobProvider(self._scheduler)

        # Store full job list for client-side filtering
        self._all_jobs: list[JobInfo] = []
        self._status_filter: str | None = None
        self._queue_filter: str | None = None
        self._search_filter: str = ""

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield Header()
        with TabbedContent(id="tabs"):
            with TabPane("Active", id="active-tab"):
                with Vertical(id="active-content"):
                    yield FilterStatusLine()
                    yield JobTable(id="active-jobs")
            with TabPane("Completed", id="completed-tab"):
                yield Static(
                    "Completed jobs will appear here", id="completed-placeholder"
                )
        # Custom footer for ANSI transparency (Textual's Footer doesn't respect it)
        with HorizontalGroup(id="footer"):
            yield Static(" q", classes="footer-key")
            yield Static("Quit", classes="footer-label")
            yield Static(" r", classes="footer-key")
            yield Static("Refresh", classes="footer-label")
            yield Static(" u", classes="footer-key")
            yield Static("User", classes="footer-label")
            yield Static(" /", classes="footer-key")
            yield Static("Search", classes="footer-label")
            yield Static(" ?", classes="footer-key")
            yield Static("Help", classes="footer-label")

    def on_mount(self) -> None:
        """Called when app is mounted - set up timers and initial data fetch."""
        # Register and apply custom theme for muted, professional aesthetic
        self.register_theme(HPC_MONITOR_THEME)
        self.theme = "hpc-monitor"

        # Enable ANSI color mode for transparent backgrounds
        # This allows the terminal's own background to show through
        self.ansi_color = True

        # Update header subtitle with user@hostname and scheduler info
        self.sub_title = f"{self._user}@{self._hostname} ({self._scheduler.name})"

        # Set up auto-refresh timer
        self._refresh_timer = self.set_interval(
            self._refresh_interval,
            self._on_refresh_timer,
            pause=False,  # Start immediately
        )

        # Mount help popup
        self._help_popup = HelpPopup(id="help-popup")
        self.mount(self._help_popup)

        # Fetch initial data
        self._refresh_active_jobs()

    def _on_refresh_timer(self) -> None:
        """Called by the refresh timer - triggers data fetch."""
        if self.auto_refresh_enabled:
            self._refresh_active_jobs()

    async def action_quit(self) -> None:
        """Quit the application."""
        self.exit()

    def action_screenshot(self) -> None:
        """Save a screenshot to the current directory."""
        path = self.save_screenshot(path="./")
        self.notify(f"Screenshot saved: {path}", timeout=3)

    def action_help(self) -> None:
        """Show help popup."""
        self._help_popup.show_popup()

    def action_refresh(self) -> None:
        """Manually trigger a data refresh."""
        self._refresh_active_jobs()

    def _refresh_active_jobs(self) -> None:
        """Fetch active jobs and update the table.

        Uses run_worker to run as a background task without blocking UI.
        The exclusive=True ensures only one refresh runs at a time.
        """
        self.run_worker(self._fetch_and_update_jobs, exclusive=True)

    async def _fetch_and_update_jobs(self) -> None:
        """Async coroutine to fetch jobs and update the table."""
        try:
            jobs = await self._job_provider.get_active_jobs(
                user_filter=self.user_filter,
            )
            # Store all jobs for client-side filtering
            self._all_jobs = jobs

            # Update queue filter with available queues
            queues = sorted(set(j.queue for j in jobs if j.queue))
            try:
                status_line = self.query_one(FilterStatusLine)
                status_line.update_queues(queues)
            except Exception:
                pass

            # Apply filters and update display
            self._apply_filters_and_display()

        except Exception as e:
            self.notify(f"Error: {e}", severity="error", timeout=3)

    def _apply_filters_and_display(self) -> None:
        """Apply current filters and update the job table."""
        filtered = self._all_jobs

        # Filter by status
        if self._status_filter:
            filtered = [
                j for j in filtered
                if j.status.name.lower() == self._status_filter.lower()
            ]

        # Filter by queue
        if self._queue_filter:
            filtered = [j for j in filtered if j.queue == self._queue_filter]

        # Filter by search (name or ID)
        if self._search_filter:
            search = self._search_filter.lower()
            filtered = [
                j for j in filtered
                if search in j.name.lower() or search in j.job_id.lower()
            ]

        # Update table
        table = self.query_one("#active-jobs", JobTable)
        table.update_jobs(filtered)

        # Update subtitle with counts
        total = len(self._all_jobs)
        shown = len(filtered)
        filter_text = "my" if self.user_filter == "me" else "all"
        if shown == total:
            self.sub_title = (
                f"{self._user}@{self._hostname} ({self._scheduler.name}) "
                f"· {total} {filter_text} job{'s' if total != 1 else ''}"
            )
        else:
            self.sub_title = (
                f"{self._user}@{self._hostname} ({self._scheduler.name}) "
                f"· {shown}/{total} {filter_text} jobs"
            )

    def action_toggle_user(self) -> None:
        """Toggle between showing current user's jobs and all jobs."""
        self.user_filter = "all" if self.user_filter == "me" else "me"

    def watch_user_filter(self, old_value: str, new_value: str) -> None:
        """React to user filter changes."""
        self.notify(f"Filter: {new_value}", timeout=1)
        # Trigger refresh with new filter
        self._refresh_active_jobs()

    def action_filter_search(self) -> None:
        """Focus the search input."""
        self.query_one(FilterStatusLine).focus_search()

    def on_filter_panel_filter_changed(
        self, event: FilterPanel.FilterChanged
    ) -> None:
        """Handle filter panel changes (arrow key navigation)."""
        if event.filter_type == "status":
            self._status_filter = event.value
        elif event.filter_type == "queue":
            self._queue_filter = event.value
        self._apply_filters_and_display()

    def on_filter_status_line_search_changed(
        self, event: FilterStatusLine.SearchChanged
    ) -> None:
        """Handle inline search changes."""
        self._search_filter = event.value
        self._apply_filters_and_display()
