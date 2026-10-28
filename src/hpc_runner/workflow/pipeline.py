"""Pipeline API for job workflows with dependencies."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from hpc_runner.core.exceptions import (
    DependencyCycleError,
    DuplicateStepError,
    NotSubmittedError,
    PipelineError,
    UnknownStepError,
)
from hpc_runner.core.job import Job
from hpc_runner.workflow.dependency import DependencyType

if TYPE_CHECKING:
    from hpc_runner.core.result import JobResult
    from hpc_runner.schedulers.base import BaseScheduler


@dataclass
class PipelineJob:
    """A job within a pipeline."""

    job: Job
    name: str
    depends_on: list[PipelineJob] = field(default_factory=list)
    dependency_type: DependencyType = field(default=DependencyType.AFTEROK)
    result: JobResult | None = None


class Pipeline:
    """Workflow pipeline with job dependencies.

    Can be used as a context manager for automatic submission::

        with Pipeline("build-test") as p:
            build = p.add("make build", name="build", cpu=4)
            test = p.add("make test", name="test", depends_on=["build"])
            package = p.add("make package", name="package", depends_on=["test"])

        # Auto-submitted on exit; results available on the pipeline
        for name, result in p.results.items():
            print(f"{name}: {result.job_id}")
        p.wait()

    Or used without a context manager for explicit control::

        p = Pipeline("build-test")
        p.add("make build", name="build", cpu=4)
        results = p.submit(scheduler=my_scheduler)
    """

    def __init__(self, name: str = "pipeline", scheduler: BaseScheduler | None = None) -> None:
        self.name = name
        self.jobs: list[PipelineJob] = []
        self._name_map: dict[str, PipelineJob] = {}
        self._scheduler = scheduler

    def add(
        self,
        command: str,
        name: str | None = None,
        depends_on: list[str | PipelineJob] | None = None,
        *,
        job_type: str | None = None,
        dependency_type: DependencyType = DependencyType.AFTEROK,
        **job_kwargs: Any,
    ) -> PipelineJob:
        """Add a job to the pipeline.

        Args:
            command: Command to execute
            name: Job name (auto-generated if None)
            depends_on: List of job names or PipelineJob objects
            job_type: Job type for config lookup (e.g., "gpu")
            dependency_type: How this job depends on its parents
            **job_kwargs: Additional Job parameter overrides
        """
        if name is None:
            name = f"step_{len(self.jobs) + 1}"

        if name in self._name_map:
            raise DuplicateStepError(f"Job name '{name}' already exists in pipeline")

        job = Job(command, job_type=job_type, **job_kwargs)
        job.name = f"{self.name}_{name}"

        dependencies: list[PipelineJob] = []
        if depends_on:
            for dep in depends_on:
                if isinstance(dep, str):
                    if dep not in self._name_map:
                        raise UnknownStepError(f"Unknown dependency: {dep}")
                    dependencies.append(self._name_map[dep])
                else:
                    dependencies.append(dep)

        pipeline_job = PipelineJob(
            job=job,
            name=name,
            depends_on=dependencies,
            dependency_type=dependency_type,
        )
        self.jobs.append(pipeline_job)
        self._name_map[name] = pipeline_job

        return pipeline_job

    @property
    def results(self) -> dict[str, JobResult]:
        """Results for all submitted jobs.

        Returns:
            Dict mapping job names to results (only jobs that have been
            submitted are included).
        """
        return {pj.name: pj.result for pj in self.jobs if pj.result is not None}

    def submit(
        self,
        scheduler: BaseScheduler | None = None,
    ) -> dict[str, JobResult]:
        """Submit all jobs respecting dependencies.

        Jobs that were already submitted (from a previous partial attempt)
        are skipped.  Safe to call again after a partial failure.

        Args:
            scheduler: Scheduler to use (auto-detect if None).  Falls back
                to the scheduler passed to ``__init__``, then auto-detect.

        Returns:
            Dict mapping job names to results
        """
        from hpc_runner.schedulers import get_scheduler

        if not self.jobs:
            raise PipelineError("Pipeline has no jobs to submit")

        if all(pjob.result is not None for pjob in self.jobs):
            raise PipelineError("Pipeline has already been submitted")

        if scheduler is None:
            scheduler = self._scheduler
        if scheduler is None:
            scheduler = get_scheduler()

        results: dict[str, JobResult] = {}

        for pjob in self._topological_sort():
            # Skip already-submitted jobs (partial retry)
            if pjob.result is not None:
                results[pjob.name] = pjob.result
                continue

            # Set up dependencies
            if pjob.depends_on:
                dep_results = [results[d.name] for d in pjob.depends_on]
                pjob.job.dependencies = dep_results
                pjob.job.dependency_type = str(pjob.dependency_type)

            # Submit
            result = scheduler.submit(pjob.job)
            pjob.result = result
            results[pjob.name] = result

        return results

    def _topological_sort(self) -> list[PipelineJob]:
        """Sort jobs by dependency order (Kahn's algorithm)."""
        # Build in-degree map and forward adjacency list
        in_degree: dict[str, int] = {}
        dependents: dict[str, list[PipelineJob]] = {pj.name: [] for pj in self.jobs}

        for pj in self.jobs:
            in_degree[pj.name] = len(pj.depends_on)
            for dep in pj.depends_on:
                dependents[dep.name].append(pj)

        # Process jobs with no remaining dependencies
        queue = deque(pj for pj in self.jobs if in_degree[pj.name] == 0)
        result: list[PipelineJob] = []

        while queue:
            pj = queue.popleft()
            result.append(pj)

            for dependent in dependents[pj.name]:
                in_degree[dependent.name] -= 1
                if in_degree[dependent.name] == 0:
                    queue.append(dependent)

        if len(result) != len(self.jobs):
            raise DependencyCycleError("Circular dependency detected in pipeline")

        return result

    def wait(self, poll_interval: float = 5.0) -> dict[str, JobResult]:
        """Wait for all submitted jobs to complete.

        Returns:
            Dict mapping job names to results

        Raises:
            NotSubmittedError: If no jobs have been submitted.
            NotSubmittedError: If some jobs were never submitted (partial failure).
        """
        submitted = {pj.name: pj.result for pj in self.jobs if pj.result is not None}

        if not submitted:
            raise NotSubmittedError("Pipeline has not been submitted")

        not_submitted = [pj.name for pj in self.jobs if pj.result is None]
        if not_submitted:
            raise NotSubmittedError(
                f"Jobs never submitted (partial failure): {', '.join(not_submitted)}. "
                "Call submit() again to retry."
            )

        for pjob in self.jobs:
            if pjob.result:
                pjob.result.wait(poll_interval=poll_interval)

        return submitted

    def get_job(self, name: str) -> PipelineJob | None:
        """Get a job by name."""
        return self._name_map.get(name)

    def __enter__(self) -> Pipeline:
        return self

    def __exit__(self, exc_type: type | None, *args: Any) -> None:
        if exc_type is None and self.jobs:
            self.submit()

    def __len__(self) -> int:
        return len(self.jobs)

    def __iter__(self) -> Iterator[PipelineJob]:
        return iter(self.jobs)
