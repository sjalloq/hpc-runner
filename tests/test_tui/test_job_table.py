"""Tests for JobTable component."""

import pytest

from hpc_runner.core.job_info import JobInfo
from hpc_runner.core.result import JobStatus
from hpc_runner.tui.components.job_table import JobTable


def make_job(
    job_id: str,
    name: str = "test_job",
    user: str = "testuser",
    status: JobStatus = JobStatus.RUNNING,
    queue: str | None = "batch.q",
    cpu: int | None = 4,
) -> JobInfo:
    """Helper to create JobInfo objects for testing."""
    return JobInfo(
        job_id=job_id,
        name=name,
        user=user,
        status=status,
        queue=queue,
        cpu=cpu,
    )


class TestJobTable:
    """Tests for JobTable widget."""

    @pytest.mark.asyncio
    async def test_table_renders(self):
        """Test that table renders without error."""
        from textual.app import App, ComposeResult

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield JobTable(id="jobs")

        app = TestApp()
        async with app.run_test():
            table = app.query_one(JobTable)
            assert table is not None
            assert table.is_empty

    @pytest.mark.asyncio
    async def test_table_columns(self):
        """Test that table has correct columns."""
        from textual.app import App, ComposeResult

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield JobTable(id="jobs")

        app = TestApp()
        async with app.run_test():
            table = app.query_one(JobTable)

            # Check column count
            assert len(table.columns) == 7

            # Check column keys
            column_keys = [col.key for col in table.columns.values()]
            assert "job_id" in column_keys
            assert "name" in column_keys
            assert "user" in column_keys
            assert "queue" in column_keys
            assert "status" in column_keys
            assert "runtime" in column_keys
            assert "slots" in column_keys

    @pytest.mark.asyncio
    async def test_update_jobs(self):
        """Test updating table with jobs."""
        from textual.app import App, ComposeResult

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield JobTable(id="jobs")

        app = TestApp()
        async with app.run_test():
            table = app.query_one(JobTable)

            # Add some jobs
            jobs = [
                make_job("12345", "job1", status=JobStatus.RUNNING),
                make_job("12346", "job2", status=JobStatus.PENDING),
                make_job("12347", "job3", status=JobStatus.COMPLETED),
            ]
            table.update_jobs(jobs)

            assert table.job_count == 3
            assert not table.is_empty
            assert table.row_count == 3

    @pytest.mark.asyncio
    async def test_update_jobs_clears_previous(self):
        """Test that update_jobs clears previous data."""
        from textual.app import App, ComposeResult

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield JobTable(id="jobs")

        app = TestApp()
        async with app.run_test():
            table = app.query_one(JobTable)

            # Add initial jobs
            table.update_jobs([make_job("111"), make_job("222")])
            assert table.job_count == 2

            # Update with new jobs
            table.update_jobs([make_job("333")])
            assert table.job_count == 1

    @pytest.mark.asyncio
    async def test_job_selected_message(self):
        """Test that JobSelected message is emitted on row highlight."""
        from textual.app import App, ComposeResult

        messages_received = []

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield JobTable(id="jobs")

            def on_job_table_job_selected(self, event: JobTable.JobSelected) -> None:
                messages_received.append(event)

        app = TestApp()
        async with app.run_test() as pilot:
            table = app.query_one(JobTable)
            table.update_jobs(
                [
                    make_job("12345", "first_job"),
                    make_job("12346", "second_job"),
                ]
            )

            # Focus and navigate
            table.focus()
            await pilot.pause()

            # Move down to trigger highlight
            await pilot.press("down")
            await pilot.pause()

            # Should have received at least one message
            assert len(messages_received) >= 1
            # Last message should be for second job
            assert messages_received[-1].job_id == "12346"

    @pytest.mark.asyncio
    async def test_status_formatting(self):
        """Test that status values are formatted correctly."""
        from textual.app import App, ComposeResult

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield JobTable(id="jobs")

        app = TestApp()
        async with app.run_test():
            table = app.query_one(JobTable)

            # Check status formatting
            assert table._format_status(JobStatus.RUNNING) == "RUNNING"
            assert table._format_status(JobStatus.PENDING) == "PENDING"
            assert table._format_status(JobStatus.COMPLETED) == "COMPLETE"
            assert table._format_status(JobStatus.FAILED) == "FAILED"
            assert table._format_status(JobStatus.CANCELLED) == "CANCEL"
            assert table._format_status(JobStatus.SKIPPED) == "SKIPPED"

    @pytest.mark.asyncio
    async def test_empty_queue_displayed_as_dash(self):
        """Test that None queue is displayed as dash."""
        from textual.app import App, ComposeResult

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield JobTable(id="jobs")

        app = TestApp()
        async with app.run_test():
            table = app.query_one(JobTable)

            # Add job with no queue
            job = make_job("12345", queue=None)
            table.update_jobs([job])

            # Get row data - queue column (index 3, after user) should be "—"
            row = table.get_row_at(0)
            assert row[3] == "—"

    @pytest.mark.asyncio
    async def test_no_horizontal_scrollbar_at_sufficient_width(self):
        """Test that no horizontal scrollbar appears when terminal is wide enough.

        The minimum width is calculated as:
        - Fixed columns: 64 chars (ID:10 + User:14 + Queue:12 + Status:10 + Runtime:12 + Slots:6)
        - Name column minimum: 15 chars
        - Column padding: 7 columns × 2 chars = 14 chars
        - Container overhead: 8 chars
        Total minimum: 64 + 15 + 14 + 8 = 101 chars
        """
        from textual.app import App, ComposeResult

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield JobTable(id="jobs")

        # Test at various widths above minimum (101)
        for width in [120, 160, 200]:
            app = TestApp()
            async with app.run_test(size=(width, 25)) as pilot:
                table = app.query_one(JobTable)

                # Add a job to ensure table has content
                table.update_jobs([make_job("12345", "test_job")])
                await pilot.pause()

                # Check no horizontal scrollbar
                assert not table.show_horizontal_scrollbar, (
                    f"Unexpected horizontal scrollbar at width {width}"
                )

    @pytest.mark.asyncio
    async def test_name_column_width_calculation(self):
        """Test that name column width expands beyond the minimum."""
        from textual.app import App, ComposeResult

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield JobTable(id="jobs")

        # At width 120, name should expand beyond its minimum width
        app = TestApp()
        async with app.run_test(size=(120, 25)) as pilot:
            table = app.query_one(JobTable)
            await pilot.pause()

            assert table._name_col_width >= table.NAME_COL_MIN

    @pytest.mark.asyncio
    async def test_long_job_name_truncated(self):
        """Test that long job names are truncated with ellipsis."""
        from textual.app import App, ComposeResult

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield JobTable(id="jobs")

        app = TestApp()
        async with app.run_test(size=(120, 25)):
            table = app.query_one(JobTable)

            # Create a job with a very long name
            long_name = "a" * 100
            table.update_jobs([make_job("12345", long_name)])

            # Get the displayed name from the row
            row = table.get_row_at(0)
            displayed_name = row[1]  # Name is second column

            # Should be truncated (shorter than original)
            assert len(displayed_name) < len(long_name)
            # Should end with ellipsis
            assert displayed_name.endswith("…")
