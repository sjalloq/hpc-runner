"""Job data provider for HPC Monitor TUI.

Wraps scheduler calls in async methods that run in a thread pool
to avoid blocking the UI.
"""

from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import TYPE_CHECKING

from hpc_runner.core.exceptions import AccountingNotAvailable
from hpc_runner.core.job_info import JobInfo
from hpc_runner.core.result import JobStatus

if TYPE_CHECKING:
    from hpc_runner.schedulers.base import BaseScheduler

logger = logging.getLogger(__name__)

# Shared thread pool for scheduler calls
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="hpc-provider")


class JobProvider:
    """Async provider for job data from HPC schedulers.

    Wraps synchronous scheduler calls in async methods that run in a
    thread pool, preventing UI blocking during data fetches.

    Attributes:
        scheduler: The HPC scheduler instance to query.
        current_user: The current username for filtering.
    """

    def __init__(self, scheduler: "BaseScheduler") -> None:
        """Initialize the job provider.

        Args:
            scheduler: The scheduler instance to use for queries.
        """
        self.scheduler = scheduler
        self.current_user = os.environ.get("USER", "unknown")

    async def get_active_jobs(
        self,
        user_filter: str = "me",
        status_filter: set[JobStatus] | None = None,
        queue_filter: str | None = None,
    ) -> list[JobInfo]:
        """Get active jobs asynchronously.

        Args:
            user_filter: "me" for current user only, "all" for all users.
            status_filter: Set of statuses to include. None = all.
            queue_filter: Queue name to filter by. None = all.

        Returns:
            List of JobInfo objects. Empty list on error.
        """
        # Determine user parameter
        user = self.current_user if user_filter == "me" else None

        try:
            # Run scheduler call in thread pool
            loop = asyncio.get_event_loop()
            jobs = await loop.run_in_executor(
                _executor,
                lambda: self.scheduler.list_active_jobs(
                    user=user,
                    status=status_filter,
                    queue=queue_filter,
                ),
            )
            return jobs
        except NotImplementedError:
            logger.warning(
                f"Scheduler {self.scheduler.name} does not implement list_active_jobs"
            )
            return []
        except Exception as e:
            logger.error(f"Error fetching active jobs: {e}")
            return []

    async def get_completed_jobs(
        self,
        user_filter: str = "me",
        since: datetime | None = None,
        until: datetime | None = None,
        exit_code: int | None = None,
        queue_filter: str | None = None,
        limit: int = 100,
    ) -> list[JobInfo]:
        """Get completed jobs asynchronously.

        Args:
            user_filter: "me" for current user only, "all" for all users.
            since: Only jobs completed after this time.
            until: Only jobs completed before this time.
            exit_code: Filter by exit code. None = all.
            queue_filter: Queue name to filter by. None = all.
            limit: Maximum number of jobs to return.

        Returns:
            List of JobInfo objects. Empty list on error.

        Raises:
            AccountingNotAvailable: If scheduler accounting is not enabled.
        """
        user = self.current_user if user_filter == "me" else None

        try:
            loop = asyncio.get_event_loop()
            jobs = await loop.run_in_executor(
                _executor,
                lambda: self.scheduler.list_completed_jobs(
                    user=user,
                    since=since,
                    until=until,
                    exit_code=exit_code,
                    queue=queue_filter,
                    limit=limit,
                ),
            )
            return jobs
        except AccountingNotAvailable:
            # Re-raise so caller can show appropriate message
            raise
        except NotImplementedError:
            logger.warning(
                f"Scheduler {self.scheduler.name} does not implement list_completed_jobs"
            )
            raise AccountingNotAvailable(
                f"Scheduler {self.scheduler.name} does not support job history"
            )
        except Exception as e:
            logger.error(f"Error fetching completed jobs: {e}")
            return []

    async def get_job_details(self, job_id: str) -> JobInfo | None:
        """Get detailed information for a single job.

        Args:
            job_id: The job ID to look up.

        Returns:
            JobInfo with details, or None if not found/error.
        """
        try:
            loop = asyncio.get_event_loop()
            job = await loop.run_in_executor(
                _executor,
                lambda: self.scheduler.get_job_details(job_id),
            )
            return job
        except Exception as e:
            logger.error(f"Error fetching job details for {job_id}: {e}")
            return None

    async def has_accounting(self) -> bool:
        """Check if job accounting/history is available.

        Returns:
            True if completed job history is available.
        """
        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                _executor,
                self.scheduler.has_accounting,
            )
        except Exception as e:
            logger.error(f"Error checking accounting availability: {e}")
            return False

    async def cancel_job(self, job_id: str) -> bool:
        """Cancel a job.

        Args:
            job_id: The job ID to cancel.

        Returns:
            True if cancellation succeeded.
        """
        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                _executor,
                lambda: self.scheduler.cancel(job_id),
            )
        except Exception as e:
            logger.error(f"Error cancelling job {job_id}: {e}")
            return False
