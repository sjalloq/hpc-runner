"""Filter popup components for job tables."""

from __future__ import annotations

from textual import events, on
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option


class HelpPopup(Static, can_focus=True):
    """Centered help popup with keybindings."""

    HELP_TEXT = """\
 Keybindings

 q       Quit
 r       Refresh jobs
 u       Toggle user (me/all)
 /       Focus search
 s       Save screenshot
 Tab     Navigate panels
 ↑/↓ jk  Cycle filter options
 Enter   Open filter popup
 Esc     Close popup\
"""

    DEFAULT_CSS = """
    HelpPopup {
        layer: overlay;
        width: auto;
        height: auto;
        background: transparent;
        border: round $primary;
        border-title-color: $primary;
        padding: 0 1;
    }

    HelpPopup.hidden {
        display: none;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(self.HELP_TEXT, **kwargs)
        self.add_class("hidden")

    def on_mount(self) -> None:
        """Set up the popup."""
        self.border_title = "Help"

    def show_popup(self) -> None:
        """Show the popup centered on screen."""
        self.remove_class("hidden")
        # Calculate size based on content
        lines = self.HELP_TEXT.split("\n")
        width = max(len(line) for line in lines) + 4
        height = len(lines) + 2
        self.styles.width = width
        self.styles.height = height
        self.styles.offset = (
            (self.app.size.width - width) // 2,
            (self.app.size.height - height) // 2,
        )
        self.focus()

    def hide_popup(self) -> None:
        """Hide the popup."""
        self.add_class("hidden")

    @on(events.Key)
    def on_key(self, event: events.Key) -> None:
        """Hide on any key press."""
        self.hide_popup()
        event.stop()

    @on(events.Blur)
    def on_blur(self, event: events.Blur) -> None:
        """Hide on blur."""
        self.hide_popup()


class FilterPanelPopup(OptionList):
    """Popup for FilterPanel showing all options."""

    SCOPED_CSS = False

    DEFAULT_CSS = """
    FilterPanelPopup {
        layer: overlay;
        width: auto;
        height: auto;
        max-height: 10;
        min-width: 16;
        background: transparent;
        border: round $primary !important;
        padding: 0 1;
    }

    FilterPanelPopup > .option-list--option {
        background: transparent;
    }

    FilterPanelPopup > .option-list--option-highlighted {
        background: $primary;
        color: $background;
    }

    FilterPanelPopup.hidden {
        display: none;
    }
    """

    def __init__(
        self,
        options: list[tuple[str, str | None]],
        current_index: int = 0,
        on_select: callable = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._panel_options = options
        self._current_index = current_index
        self._on_select = on_select

    def on_mount(self) -> None:
        """Populate options."""
        self._refresh_options()

    def _refresh_options(self) -> None:
        """Refresh the option list."""
        self.clear_options()
        for label, value in self._panel_options:
            self.add_option(Option(f" {label} ", id=str(value) if value else "none"))
        if self._current_index < len(self._panel_options):
            self.highlighted = self._current_index

    def update_options(
        self,
        options: list[tuple[str, str | None]],
        current_index: int,
        on_select: callable = None,
    ) -> None:
        """Update options and current selection."""
        self._panel_options = options
        self._current_index = current_index
        if on_select is not None:
            self._on_select = on_select
        if self.is_mounted:
            self._refresh_options()

    def show_popup(self, region) -> None:
        """Show popup positioned relative to parent widget."""
        self.remove_class("hidden")
        self.styles.offset = (region.x, region.y + region.height)
        self.focus()

    def hide_popup(self) -> None:
        """Hide the popup."""
        self.add_class("hidden")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Handle option selection."""
        index = self.highlighted if self.highlighted is not None else 0
        self.hide_popup()
        if self._on_select is not None:
            self._on_select(index)

    @on(events.Key)
    def on_key_escape(self, event: events.Key) -> None:
        """Hide on escape."""
        if event.key == "escape":
            self.hide_popup()
            event.stop()

    @on(events.Blur)
    def on_blur(self, event: events.Blur) -> None:
        """Hide on blur."""
        self.hide_popup()


class FilterPanel(Static, can_focus=True):
    """A focusable filter panel that cycles through options with arrow keys.

    Press Enter to open a popup showing all options.
    """

    class FilterChanged(Message):
        """Emitted when filter value changes."""

        def __init__(self, filter_type: str, value: str | None) -> None:
            super().__init__()
            self.filter_type = filter_type
            self.value = value

    def __init__(
        self,
        filter_type: str,
        options: list[tuple[str, str | None]],
        title: str = "",
        **kwargs,
    ) -> None:
        """Initialize filter panel.

        Args:
            filter_type: Type of filter (e.g., "status", "queue")
            options: List of (display_label, value) tuples
            title: Border title
        """
        super().__init__("All", **kwargs)
        self._filter_type = filter_type
        self._options = options
        self._current_index = 0
        self._title = title
        self._popup: FilterPanelPopup | None = None

    def on_mount(self) -> None:
        """Set border title and create popup on mount."""
        self.border_title = self._title
        self._popup = FilterPanelPopup(
            self._options,
            self._current_index,
            on_select=self._on_popup_select,
            id=f"{self._filter_type}-popup",
        )
        self._popup.add_class("hidden")
        self.app.mount(self._popup)

    def _on_popup_select(self, index: int) -> None:
        """Handle popup selection callback."""
        self._current_index = index
        self._update_display()
        self.focus()

    def on_key(self, event: events.Key) -> None:
        """Handle arrow keys to cycle through options, Enter to open popup."""
        if event.key == "down" or event.key == "j":
            self._current_index = (self._current_index + 1) % len(self._options)
            self._update_display()
            event.stop()
        elif event.key == "up" or event.key == "k":
            self._current_index = (self._current_index - 1) % len(self._options)
            self._update_display()
            event.stop()
        elif event.key == "enter" or event.key == "space":
            self._show_popup()
            event.stop()

    def _show_popup(self) -> None:
        """Show the options popup."""
        if self._popup is not None:
            self._popup.update_options(
                self._options,
                self._current_index,
                on_select=self._on_popup_select,
            )
            self._popup.show_popup(self.region)

    def _update_display(self) -> None:
        """Update the displayed value and emit change event."""
        label, value = self._options[self._current_index]
        self.update(label)
        self.post_message(self.FilterChanged(self._filter_type, value))

    def set_options(self, options: list[tuple[str, str | None]]) -> None:
        """Update available options."""
        self._options = options
        if self._current_index >= len(options):
            self._current_index = 0
            self._update_display()

    def get_value(self) -> str | None:
        """Get current filter value."""
        return self._options[self._current_index][1]


class FilterStatusLine(Horizontal):
    """Filter status bar with bordered panels.

    Shows: [Job Status] [Queue] [Search.....................]
    Tab into Status/Queue and use up/down arrows to change.
    """

    class SearchChanged(Message):
        """Emitted when search value changes."""

        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    STATUS_OPTIONS: list[tuple[str, str | None]] = [
        ("All", None),
        ("Running", "running"),
        ("Pending", "pending"),
        ("Held", "held"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(id="filter-status", **kwargs)
        self._search: str = ""
        self._queues: list[str] = []

    def compose(self):
        """Create the status line widgets."""
        yield FilterPanel(
            "status",
            self.STATUS_OPTIONS,
            title="Status",
            id="status-panel",
        )

        yield FilterPanel(
            "queue",
            [("All", None)],
            title="Queue",
            id="queue-panel",
        )

        with Vertical(id="search-container") as search_container:
            search_container.border_title = "Search"
            yield Input(placeholder="", id="search-input")

    def on_input_changed(self, event: Input.Changed) -> None:
        """Handle search input changes."""
        if event.input.id == "search-input":
            self._search = event.value
            self.post_message(self.SearchChanged(event.value))

    def focus_search(self) -> None:
        """Focus the search input."""
        self.query_one("#search-input", Input).focus()

    def update_queues(self, queues: list[str]) -> None:
        """Update available queue options."""
        self._queues = queues
        options: list[tuple[str, str | None]] = [("All", None)]
        options.extend((q, q) for q in queues)
        self.query_one("#queue-panel", FilterPanel).set_options(options)

    def update_search(self, value: str) -> None:
        """Update search display (only if different to avoid loops)."""
        if self._search != value:
            self._search = value
            self.query_one("#search-input", Input).value = value
