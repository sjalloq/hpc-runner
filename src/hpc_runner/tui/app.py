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
from textual.containers import HorizontalGroup
from textual.reactive import reactive
from textual.theme import Theme
from textual.widgets import Header, Static, TabbedContent, TabPane

from hpc_runner.schedulers import get_scheduler
from hpc_runner.tui.components import JobTable
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

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield Header()
        with TabbedContent(id="tabs"):
            with TabPane("Active", id="active-tab"):
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
            yield Static("Toggle User", classes="footer-label")

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

        # Fetch initial data
        self._refresh_active_jobs()

    def _on_refresh_timer(self) -> None:
        """Called by the refresh timer - triggers data fetch."""
        if self.auto_refresh_enabled:
            self._refresh_active_jobs()

    async def action_quit(self) -> None:
        """Quit the application."""
        self.exit()

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
            table = self.query_one("#active-jobs", JobTable)
            table.update_jobs(jobs)

            # Update subtitle with job count
            count = len(jobs)
            filter_text = "my" if self.user_filter == "me" else "all"
            self.sub_title = (
                f"{self._user}@{self._hostname} ({self._scheduler.name}) "
                f"· {count} {filter_text} job{'s' if count != 1 else ''}"
            )
        except Exception as e:
            self.notify(f"Error: {e}", severity="error", timeout=3)

    def action_toggle_user(self) -> None:
        """Toggle between showing current user's jobs and all jobs."""
        self.user_filter = "all" if self.user_filter == "me" else "me"

    def watch_user_filter(self, old_value: str, new_value: str) -> None:
        """React to user filter changes."""
        self.notify(f"Filter: {new_value}", timeout=1)
        # Trigger refresh with new filter
        self._refresh_active_jobs()
