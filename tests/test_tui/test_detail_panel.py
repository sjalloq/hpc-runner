"""Tests for DetailPanel and ButtonBar components."""

import pytest

from hpc_runner.core.job_info import JobInfo
from hpc_runner.core.result import JobStatus
from hpc_runner.tui.components.detail_panel import ButtonBar, DetailPanel
from textual.app import App, ComposeResult
from textual.widgets import Button


def make_job(
    job_id: str = "12345",
    name: str = "test_job",
    status: JobStatus = JobStatus.RUNNING,
) -> JobInfo:
    """Helper to create JobInfo objects for testing."""
    return JobInfo(
        job_id=job_id,
        name=name,
        user="testuser",
        status=status,
    )


class TestButtonBar:
    """Tests for ButtonBar arrow key navigation."""

    @pytest.mark.asyncio
    async def test_right_arrow_moves_focus_forward(self):
        """Test that right arrow moves focus to next button."""

        class TestApp(App):
            def compose(self) -> ComposeResult:
                with ButtonBar(id="buttons"):
                    yield Button("First", id="btn-1")
                    yield Button("Second", id="btn-2")
                    yield Button("Third", id="btn-3")

        app = TestApp()
        async with app.run_test() as pilot:
            # Focus first button
            btn1 = app.query_one("#btn-1", Button)
            btn1.focus()
            await pilot.pause()
            assert app.focused == btn1

            # Press right arrow
            await pilot.press("right")
            await pilot.pause()

            # Focus should be on second button
            btn2 = app.query_one("#btn-2", Button)
            assert app.focused == btn2

    @pytest.mark.asyncio
    async def test_left_arrow_moves_focus_backward(self):
        """Test that left arrow moves focus to previous button."""

        class TestApp(App):
            def compose(self) -> ComposeResult:
                with ButtonBar(id="buttons"):
                    yield Button("First", id="btn-1")
                    yield Button("Second", id="btn-2")
                    yield Button("Third", id="btn-3")

        app = TestApp()
        async with app.run_test() as pilot:
            # Focus second button
            btn2 = app.query_one("#btn-2", Button)
            btn2.focus()
            await pilot.pause()

            # Press left arrow
            await pilot.press("left")
            await pilot.pause()

            # Focus should be on first button
            btn1 = app.query_one("#btn-1", Button)
            assert app.focused == btn1

    @pytest.mark.asyncio
    async def test_right_arrow_wraps_around(self):
        """Test that right arrow wraps from last to first button."""

        class TestApp(App):
            def compose(self) -> ComposeResult:
                with ButtonBar(id="buttons"):
                    yield Button("First", id="btn-1")
                    yield Button("Second", id="btn-2")

        app = TestApp()
        async with app.run_test() as pilot:
            # Focus last button
            btn2 = app.query_one("#btn-2", Button)
            btn2.focus()
            await pilot.pause()

            # Press right arrow
            await pilot.press("right")
            await pilot.pause()

            # Focus should wrap to first button
            btn1 = app.query_one("#btn-1", Button)
            assert app.focused == btn1

    @pytest.mark.asyncio
    async def test_left_arrow_wraps_around(self):
        """Test that left arrow wraps from first to last button."""

        class TestApp(App):
            def compose(self) -> ComposeResult:
                with ButtonBar(id="buttons"):
                    yield Button("First", id="btn-1")
                    yield Button("Second", id="btn-2")

        app = TestApp()
        async with app.run_test() as pilot:
            # Focus first button
            btn1 = app.query_one("#btn-1", Button)
            btn1.focus()
            await pilot.pause()

            # Press left arrow
            await pilot.press("left")
            await pilot.pause()

            # Focus should wrap to last button
            btn2 = app.query_one("#btn-2", Button)
            assert app.focused == btn2

    @pytest.mark.asyncio
    async def test_skips_disabled_buttons(self):
        """Test that arrow navigation skips disabled buttons."""

        class TestApp(App):
            def compose(self) -> ComposeResult:
                with ButtonBar(id="buttons"):
                    yield Button("First", id="btn-1")
                    yield Button("Second", id="btn-2", disabled=True)
                    yield Button("Third", id="btn-3")

        app = TestApp()
        async with app.run_test() as pilot:
            # Focus first button
            btn1 = app.query_one("#btn-1", Button)
            btn1.focus()
            await pilot.pause()

            # Press right arrow - should skip disabled btn-2
            await pilot.press("right")
            await pilot.pause()

            # Focus should be on third button (skipping disabled second)
            btn3 = app.query_one("#btn-3", Button)
            assert app.focused == btn3


class TestDetailPanel:
    """Tests for DetailPanel widget."""

    @pytest.mark.asyncio
    async def test_panel_renders(self):
        """Test that panel renders without error."""

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield DetailPanel(id="detail")

        app = TestApp()
        async with app.run_test() as pilot:
            panel = app.query_one(DetailPanel)
            assert panel is not None

    @pytest.mark.asyncio
    async def test_no_selection_message_shown_initially(self):
        """Test that 'no selection' message is shown when no job selected."""

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield DetailPanel(id="detail")

        app = TestApp()
        async with app.run_test() as pilot:
            panel = app.query_one(DetailPanel)
            no_selection = panel.query_one("#no-selection")
            assert "hidden" not in no_selection.classes

    @pytest.mark.asyncio
    async def test_update_job_shows_details(self):
        """Test that update_job shows job details."""

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield DetailPanel(id="detail")

        app = TestApp()
        async with app.run_test() as pilot:
            panel = app.query_one(DetailPanel)
            job = make_job("12345", "my_test_job")
            panel.update_job(job)
            await pilot.pause()

            # No selection should be hidden
            no_selection = panel.query_one("#no-selection")
            assert "hidden" in no_selection.classes

            # Detail content should be visible
            detail_content = panel.query_one("#detail-content")
            assert "hidden" not in detail_content.classes

    @pytest.mark.asyncio
    async def test_update_job_none_shows_no_selection(self):
        """Test that update_job(None) shows no selection message."""

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield DetailPanel(id="detail")

        app = TestApp()
        async with app.run_test() as pilot:
            panel = app.query_one(DetailPanel)

            # First set a job
            panel.update_job(make_job())
            await pilot.pause()

            # Then clear it
            panel.update_job(None)
            await pilot.pause()

            # No selection should be visible again
            no_selection = panel.query_one("#no-selection")
            assert "hidden" not in no_selection.classes

    @pytest.mark.asyncio
    async def test_cancel_button_disabled_for_completed_job(self):
        """Test that cancel button is disabled for completed jobs."""

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield DetailPanel(id="detail")

        app = TestApp()
        async with app.run_test() as pilot:
            panel = app.query_one(DetailPanel)
            job = make_job(status=JobStatus.COMPLETED)
            panel.update_job(job)
            await pilot.pause()

            btn_cancel = panel.query_one("#btn-cancel", Button)
            assert btn_cancel.disabled

    @pytest.mark.asyncio
    async def test_cancel_button_enabled_for_running_job(self):
        """Test that cancel button is enabled for running jobs."""

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield DetailPanel(id="detail")

        app = TestApp()
        async with app.run_test() as pilot:
            panel = app.query_one(DetailPanel)
            job = make_job(status=JobStatus.RUNNING)
            panel.update_job(job)
            await pilot.pause()

            btn_cancel = panel.query_one("#btn-cancel", Button)
            assert not btn_cancel.disabled

    @pytest.mark.asyncio
    async def test_view_logs_message_emitted(self):
        """Test that ViewLogs message is emitted when button clicked."""
        messages_received = []

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield DetailPanel(id="detail")

            def on_detail_panel_view_logs(self, event: DetailPanel.ViewLogs) -> None:
                messages_received.append(event)

        app = TestApp()
        async with app.run_test() as pilot:
            panel = app.query_one(DetailPanel)
            job = make_job()
            # Set stdout_path so button is enabled
            job.stdout_path = "/tmp/test.out"
            panel.update_job(job)
            await pilot.pause()

            # Click stdout button
            btn_stdout = panel.query_one("#btn-stdout", Button)
            btn_stdout.press()
            await pilot.pause()

            assert len(messages_received) == 1
            assert messages_received[0].stream == "stdout"
            assert messages_received[0].job.job_id == "12345"

    @pytest.mark.asyncio
    async def test_cancel_job_message_emitted(self):
        """Test that CancelJob message is emitted when button clicked."""
        messages_received = []

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield DetailPanel(id="detail")

            def on_detail_panel_cancel_job(self, event: DetailPanel.CancelJob) -> None:
                messages_received.append(event)

        app = TestApp()
        async with app.run_test() as pilot:
            panel = app.query_one(DetailPanel)
            job = make_job(status=JobStatus.RUNNING)
            panel.update_job(job)
            await pilot.pause()

            # Click cancel button
            btn_cancel = panel.query_one("#btn-cancel", Button)
            btn_cancel.press()
            await pilot.pause()

            assert len(messages_received) == 1
            assert messages_received[0].job.job_id == "12345"
