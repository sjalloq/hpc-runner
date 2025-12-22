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
        async with app.run_test() as pilot:
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
        async with app.run_test() as pilot:
            table = app.query_one(JobTable)

            # Check column count
            assert len(table.columns) == 6

            # Check column keys
            column_keys = [col.key for col in table.columns.values()]
            assert "job_id" in column_keys
            assert "name" in column_keys
            assert "queue" in column_keys
            assert "status" in column_keys
            assert "runtime" in column_keys
            assert "resources" in column_keys

    @pytest.mark.asyncio
    async def test_update_jobs(self):
        """Test updating table with jobs."""
        from textual.app import App, ComposeResult

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield JobTable(id="jobs")

        app = TestApp()
        async with app.run_test() as pilot:
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
        async with app.run_test() as pilot:
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

            def on_job_table_job_selected(
                self, event: JobTable.JobSelected
            ) -> None:
                messages_received.append(event)

        app = TestApp()
        async with app.run_test() as pilot:
            table = app.query_one(JobTable)
            table.update_jobs([
                make_job("12345", "first_job"),
                make_job("12346", "second_job"),
            ])

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
        async with app.run_test() as pilot:
            table = app.query_one(JobTable)

            # Check status formatting
            assert table._format_status(JobStatus.RUNNING) == "RUNNING"
            assert table._format_status(JobStatus.PENDING) == "PENDING"
            assert table._format_status(JobStatus.COMPLETED) == "COMPLETE"
            assert table._format_status(JobStatus.FAILED) == "FAILED"
            assert table._format_status(JobStatus.CANCELLED) == "CANCEL"

    @pytest.mark.asyncio
    async def test_empty_queue_displayed_as_dash(self):
        """Test that None queue is displayed as dash."""
        from textual.app import App, ComposeResult

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield JobTable(id="jobs")

        app = TestApp()
        async with app.run_test() as pilot:
            table = app.query_one(JobTable)

            # Add job with no queue
            job = make_job("12345", queue=None)
            table.update_jobs([job])

            # Get row data - queue column (index 2) should be "—"
            row = table.get_row_at(0)
            assert row[2] == "—"
