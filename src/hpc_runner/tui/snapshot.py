"""TUI Snapshot utility for visual review after edits.

Usage:
    python -m hpc_runner.tui.snapshot

This captures a screenshot and reports key visual properties to catch
regressions like solid backgrounds when transparency is expected.
"""

import asyncio
import sys
from pathlib import Path

from .app import HpcMonitorApp


def _is_transparent(color) -> bool:
    """Check if a color is transparent."""
    if color is None:
        return True
    if color.a == 0:
        return True
    if hasattr(color, "ansi") and color.ansi == -1:
        return True
    return False


def _color_hex(color) -> str:
    """Convert color to hex string for display."""
    if color is None:
        return "None"
    if hasattr(color, "ansi") and color.ansi == -1:
        return "ANSI_DEFAULT (transparent)"
    if color.a == 0:
        return f"transparent (a=0)"
    return f"#{color.r:02x}{color.g:02x}{color.b:02x}"


async def capture_and_review() -> bool:
    """Capture snapshot and review visual properties.

    Returns:
        True if all checks pass, False otherwise.
    """
    from textual.containers import HorizontalGroup
    from textual.widgets import Tab, Header, TabbedContent, Static

    app = HpcMonitorApp()
    all_passed = True

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()

        # Save screenshot
        screenshot_path = Path("tui_snapshot.svg")
        app.save_screenshot(str(screenshot_path))

        print("=" * 60)
        print("TUI SNAPSHOT REVIEW")
        print("=" * 60)
        print(f"\nScreenshot saved to: {screenshot_path.absolute()}")
        print(f"Theme: {app.theme}")
        print(f"ANSI mode: {app.ansi_color}")

        # Check Screen background
        print("\n--- Background Transparency ---")
        screen_bg = app.screen.styles.background
        screen_ok = _is_transparent(screen_bg)
        status = "✓ PASS" if screen_ok else "✗ FAIL"
        print(f"  Screen background: {_color_hex(screen_bg)} {status}")
        if not screen_ok:
            all_passed = False
            print("    ^ Should be transparent for terminal background to show")

        # Check Header
        header = app.query_one(Header)
        header_bg = header.styles.background
        header_ok = _is_transparent(header_bg)
        status = "✓ PASS" if header_ok else "✗ FAIL"
        print(f"  Header background: {_color_hex(header_bg)} {status}")
        if not header_ok:
            all_passed = False

        # Check custom footer (#footer HorizontalGroup)
        footer = app.query_one("#footer", HorizontalGroup)
        footer_bg = footer.styles.background
        footer_ok = _is_transparent(footer_bg)
        status = "✓ PASS" if footer_ok else "✗ FAIL"
        print(f"  Footer background: {_color_hex(footer_bg)} {status}")
        if not footer_ok:
            all_passed = False

        # Check footer children (all should be transparent)
        for child in footer.children:
            child_bg = child.styles.background
            child_ok = _is_transparent(child_bg)
            status = "✓ PASS" if child_ok else "✗ FAIL"
            child_classes = " ".join(child.classes) if child.classes else "(no class)"
            print(f"    Footer child ({child_classes}): {_color_hex(child_bg)} {status}")
            if not child_ok:
                all_passed = False

        # Check TabbedContent
        tabbed = app.query_one(TabbedContent)
        tabbed_bg = tabbed.styles.background
        tabbed_ok = _is_transparent(tabbed_bg)
        status = "✓ PASS" if tabbed_ok else "✗ FAIL"
        print(f"  TabbedContent background: {_color_hex(tabbed_bg)} {status}")
        if not tabbed_ok:
            all_passed = False

        # Check tabs
        print("\n--- Tab Styling ---")
        tabs = app.query(Tab)
        for tab in tabs:
            is_active = tab.has_class("-active")
            bg = tab.styles.background

            if is_active:
                # Active tab should have primary color (#88C0D0)
                active_ok = bg is not None and bg.r == 136 and bg.g == 192 and bg.b == 208
                status = "✓ PASS" if active_ok else "✗ FAIL"
                print(f"  Active tab '{tab.label.plain}': {_color_hex(bg)} {status}")
                if not active_ok:
                    all_passed = False
                    print("    ^ Should be #88c0d0 (muted teal)")
            else:
                # Inactive tab should be transparent
                inactive_ok = _is_transparent(bg)
                status = "✓ PASS" if inactive_ok else "✗ FAIL"
                print(f"  Inactive tab '{tab.label.plain}': {_color_hex(bg)} {status}")
                if not inactive_ok:
                    all_passed = False

        # Summary
        print("\n" + "=" * 60)
        if all_passed:
            print("RESULT: ✓ All visual checks passed")
        else:
            print("RESULT: ✗ Some checks failed - review needed!")
        print("=" * 60)

    return all_passed


def main():
    """Run snapshot review."""
    passed = asyncio.run(capture_and_review())
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
