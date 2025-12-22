"""Tests for HPC Monitor TUI."""

import pytest
from textual.color import Color
from hpc_runner.tui.app import HpcMonitorApp, HPC_MONITOR_THEME


class TestHpcMonitorApp:
    """Tests for the HPC Monitor TUI application."""

    @pytest.mark.asyncio
    async def test_app_renders(self):
        """Basic test that the app renders without error."""
        app = HpcMonitorApp()
        async with app.run_test(size=(80, 24)) as pilot:
            from textual.widgets import Tab

            # Check tabs exist
            tabs = app.query(Tab)
            assert len(tabs) == 2

            # Check tab labels
            tab_labels = [tab.label.plain for tab in tabs]
            assert "Active" in tab_labels
            assert "Completed" in tab_labels

    @pytest.mark.asyncio
    async def test_app_theme_applied(self):
        """Test that custom theme is applied."""
        app = HpcMonitorApp()
        async with app.run_test(size=(80, 24)) as pilot:
            # Theme should be applied
            assert app.theme == "hpc-monitor"

    @pytest.mark.asyncio
    async def test_active_tab_uses_theme_primary(self):
        """Test that active tab uses theme's primary color (muted teal)."""
        app = HpcMonitorApp()
        async with app.run_test(size=(80, 24)) as pilot:
            from textual.widgets import Tab

            # Find the active tab
            active_tabs = [t for t in app.query(Tab) if t.has_class("-active")]
            assert len(active_tabs) == 1

            active_tab = active_tabs[0]
            bg = active_tab.styles.background

            # Active tab should have the theme's primary color (#88C0D0 = muted teal)
            # RGB values for #88C0D0 are (136, 192, 208)
            assert bg is not None
            assert bg.r == 136, f"Expected red=136, got {bg.r}"
            assert bg.g == 192, f"Expected green=192, got {bg.g}"
            assert bg.b == 208, f"Expected blue=208, got {bg.b}"


class TestTransparentBackgrounds:
    """Tests to ensure backgrounds are transparent (blend with terminal).

    The Rovr aesthetic requires transparent backgrounds so the terminal's
    own background color shows through. This is critical for the "minimal
    chrome" design philosophy.
    """

    def _is_transparent(self, color: Color | None) -> bool:
        """Check if a color is transparent (None, alpha=0, or ANSI default).

        ANSI colors with ansi=-1 indicate "use terminal default" which is
        effectively transparent - the terminal's background shows through.
        """
        if color is None:
            return True
        # Color with alpha=0 is fully transparent
        if color.a == 0:
            return True
        # ANSI default color (ansi=-1) means use terminal's background
        if hasattr(color, "ansi") and color.ansi == -1:
            return True
        return False

    @pytest.mark.asyncio
    async def test_header_background_transparent(self):
        """Header should have transparent background."""
        app = HpcMonitorApp()
        async with app.run_test(size=(80, 24)) as pilot:
            from textual.widgets import Header

            header = app.query_one(Header)
            bg = header.styles.background
            assert self._is_transparent(bg), (
                f"Header background should be transparent, got {bg}"
            )

    @pytest.mark.asyncio
    async def test_footer_background_transparent(self):
        """Footer should have transparent background.

        We use a custom footer (HorizontalGroup with id='footer') instead of
        Textual's built-in Footer widget, as the built-in Footer uses internal
        Rich styles that don't respect CSS transparency or ANSI mode.
        """
        app = HpcMonitorApp()
        async with app.run_test(size=(80, 24)) as pilot:
            from textual.containers import HorizontalGroup

            # Custom footer is a HorizontalGroup with id="footer"
            footer = app.query_one("#footer", HorizontalGroup)
            bg = footer.styles.background
            assert self._is_transparent(bg), (
                f"Footer background should be transparent, got {bg}"
            )

            # Also check all footer children are transparent
            for child in footer.children:
                child_bg = child.styles.background
                assert self._is_transparent(child_bg), (
                    f"Footer child background should be transparent, got {child_bg}"
                )

    @pytest.mark.asyncio
    async def test_tabbed_content_background_transparent(self):
        """TabbedContent container should have transparent background."""
        app = HpcMonitorApp()
        async with app.run_test(size=(80, 24)) as pilot:
            from textual.widgets import TabbedContent

            tabbed = app.query_one(TabbedContent)
            bg = tabbed.styles.background
            assert self._is_transparent(bg), (
                f"TabbedContent background should be transparent, got {bg}"
            )

    @pytest.mark.asyncio
    async def test_tab_pane_background_transparent(self):
        """TabPane content areas should have transparent background."""
        app = HpcMonitorApp()
        async with app.run_test(size=(80, 24)) as pilot:
            from textual.widgets import TabPane

            panes = app.query(TabPane)
            for pane in panes:
                bg = pane.styles.background
                assert self._is_transparent(bg), (
                    f"TabPane '{pane.id}' background should be transparent, got {bg}"
                )

    @pytest.mark.asyncio
    async def test_inactive_tab_background_transparent(self):
        """Inactive tabs should have transparent background."""
        app = HpcMonitorApp()
        async with app.run_test(size=(80, 24)) as pilot:
            from textual.widgets import Tab

            inactive_tabs = [t for t in app.query(Tab) if not t.has_class("-active")]
            assert len(inactive_tabs) > 0, "Should have at least one inactive tab"

            for tab in inactive_tabs:
                bg = tab.styles.background
                assert self._is_transparent(bg), (
                    f"Inactive tab '{tab.label.plain}' background should be "
                    f"transparent, got {bg}"
                )

    @pytest.mark.asyncio
    async def test_screen_background_not_solid(self):
        """Screen (root container) should not have a solid background.

        This is the KEY test for transparency. The theme must NOT set a
        background color, otherwise the Screen widget will have a solid
        fill that hides the terminal's own background.
        """
        app = HpcMonitorApp()
        async with app.run_test(size=(80, 24)) as pilot:
            screen_bg = app.screen.styles.background

            # Screen background should be transparent or unset
            # A solid color like #2E3440 means we broke transparency
            assert self._is_transparent(screen_bg), (
                f"Screen background should be transparent to show terminal "
                f"background, but got solid color: {screen_bg}"
            )
