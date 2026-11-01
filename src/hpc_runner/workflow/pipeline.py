"""Pipeline API for job workflows with dependencies."""

from __future__ import annotations

import dataclasses
from collections import deque
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from hpc_runner.core.dependency import DependencyType, dependency_on
from hpc_runner.core.exceptions import (
    DependencyCycleError,
    DuplicateStepError,
    NotSubmittedError,
    PipelineError,
    UnknownStepError,
)
from hpc_runner.core.host import Host
from hpc_runner.core.resolve import resolve
from hpc_runner.core.result import ArrayJobResult, JobStatus, reduce_statuses, wait_for_statuses
from hpc_runner.core.spec import JobSpec

if TYPE_CHECKING:
    from hpc_runner.core.config import HPCConfig
    from hpc_runner.core.result import JobResult
    from hpc_runner.schedulers.base import BaseScheduler


@dataclass
class PipelineJob:
    """A job within a pipeline: the spec, resolved only at submit time."""

    spec: JobSpec
    name: str
    depends_on: list[PipelineJob] = field(default_factory=list)
    dependency_type: DependencyType = field(default=DependencyType.AFTEROK)
    result: JobResult | None = None


class Pipeline:
    """Workflow pipeline with job dependencies.

    Can be used as a context manager for automatic submission::

        with Pipeline("build-test", config=config) as p:
            build = p.add(JobSpec("make build", cpu=4), name="build")
            test = p.add("make test", name="test", depends_on=["build"])
            package = p.add("make package", name="package", depends_on=["test"])

        # Auto-submitted on exit; results available on the pipeline
        for name, result in p.results.items():
            print(f"{name}: {result.job_id}")
        statuses = p.wait()  # {step name: JobStatus}

    Or used without a context manager for explicit control::

        p = Pipeline("build-test", config=config)
        p.add(JobSpec("make build", cpu=4), name="build")
        results = p.submit(scheduler=my_scheduler)

    Each step's spec is resolved against *config* and *host* (default:
    ``Host.capture()`` at submit time) when the pipeline is submitted.
    """

    def __init__(
        self,
        name: str = "pipeline",
        *,
        config: HPCConfig,
        scheduler: BaseScheduler | None = None,
        host: Host | None = None,
    ) -> None:
        self.name = name
        self.config = config
        self.jobs: list[PipelineJob] = []
        self._name_map: dict[str, PipelineJob] = {}
        self._results: dict[str, JobResult] = {}
        self._results_view: Mapping[str, JobResult] = MappingProxyType(self._results)
        self._scheduler = scheduler
        self._host = host

    def add(
        self,
        spec_or_command: JobSpec | str,
        name: str | None = None,
        depends_on: list[str | PipelineJob] | None = None,
        *,
        dependency_type: DependencyType = DependencyType.AFTEROK,
    ) -> PipelineJob:
        """Add a job to the pipeline.

        Args:
            spec_or_command: The step's JobSpec, or a command string
            name: Step name (auto-generated if None)
            depends_on: List of step names or PipelineJob objects
            dependency_type: How this job depends on its parents
        """
        if name is None:
            name = f"step_{len(self.jobs) + 1}"

        if name in self._name_map:
            raise DuplicateStepError(f"Job name '{name}' already exists in pipeline")

        spec = JobSpec(spec_or_command) if isinstance(spec_or_command, str) else spec_or_command

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
            spec=spec,
            name=name,
            depends_on=dependencies,
            dependency_type=dependency_type,
        )
        self.jobs.append(pipeline_job)
        self._name_map[name] = pipeline_job

        return pipeline_job

    @property
    def results(self) -> Mapping[str, JobResult]:
        """Results of the submitted steps, by step name, in submission order.

        A read-only live view: the same object on every access, updated as
        steps are submitted. Steps not (yet) submitted are absent, so
        ``results[name]`` raises ``KeyError`` for them.
        """
        return self._results_view

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
            scheduler = get_scheduler(config=self.config)
        host = self._host if self._host is not None else Host.capture()

        results: dict[str, JobResult] = {}

        for pjob in self._topological_sort():
            # Skip already-submitted jobs (partial retry)
            if pjob.result is not None:
                results[pjob.name] = pjob.result
                continue

            # Inject dependencies into a copy of the spec; resolve now.
            dependencies = list(pjob.spec.dependencies)
            for parent in pjob.depends_on:
                parent_result = results[parent.name]
                if parent_result.job_id is None:
                    raise PipelineError(
                        f"step '{parent.name}' ran interactively and has no job id "
                        f"for '{pjob.name}' to depend on"
                    )
                # An array parent records its tasks for the per-task guard.
                dependencies.append(dependency_on(parent_result, pjob.dependency_type))
            spec = dataclasses.replace(
                pjob.spec, dependencies=dependencies, name=f"{self.name}_{pjob.name}"
            )
            job = resolve(spec, self.config, host)

            # Submit
            result = scheduler.submit(job)
            pjob.result = result
            results[pjob.name] = result
            self._results[pjob.name] = result

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

    def wait(
        self,
        poll_interval: float = 5.0,
        timeout: float | None = None,
        unknown_after: float = 60.0,
    ) -> dict[str, JobStatus]:
        """Wait for all submitted jobs to finish; return each step's final status.

        Each tick makes one ``get_statuses`` call per scheduler for every
        outstanding job (each task of an array step). *timeout* and
        *unknown_after* are as for :meth:`JobResult.wait`: a job UNKNOWN for
        *unknown_after* seconds stops being waited for.

        An array step's status is its tasks' common status when they agree;
        otherwise the first task (in index order) that did not complete.

        Returns:
            Dict mapping step names to their final :class:`JobStatus`.

        Raises:
            NotSubmittedError: If no jobs have been submitted.
            NotSubmittedError: If some jobs were never submitted (partial failure).
            TimeoutError: *timeout* elapsed first.
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

        schedulers: dict[str, BaseScheduler] = {}
        step_ids: dict[str, list[str]] = {}
        for name, result in submitted.items():
            ids = result.task_ids() if isinstance(result, ArrayJobResult) else [result.job_id]
            step_ids[name] = [job_id for job_id in ids if job_id is not None]
            for job_id in step_ids[name]:
                schedulers[job_id] = result.scheduler

        def poll(job_ids: list[str]) -> dict[str, JobStatus]:
            by_scheduler: dict[int, tuple[BaseScheduler, list[str]]] = {}
            for job_id in job_ids:
                scheduler = schedulers[job_id]
                by_scheduler.setdefault(id(scheduler), (scheduler, []))[1].append(job_id)
            statuses: dict[str, JobStatus] = {}
            for scheduler, ids in by_scheduler.values():
                statuses.update(scheduler.get_statuses(ids))
            return statuses

        final = wait_for_statuses(
            poll,
            list(schedulers),
            poll_interval=poll_interval,
            timeout=timeout,
            unknown_after=unknown_after,
            label=f"Pipeline {self.name}",
        )
        statuses: dict[str, JobStatus] = {}
        for name, result in submitted.items():
            ids = step_ids[name]
            if not ids:  # an interactive run: already finished
                statuses[name] = result.status
            else:
                statuses[name] = reduce_statuses(final[job_id] for job_id in ids)
        return statuses

    def get_job(self, name: str) -> PipelineJob:
        """The step called *name*.

        Raises:
            UnknownStepError: No step has that name.
        """
        try:
            return self._name_map[name]
        except KeyError:
            raise UnknownStepError(f"Unknown step: {name}") from None

    def __enter__(self) -> Pipeline:
        return self

    def __exit__(self, exc_type: type | None, *args: Any) -> None:
        if exc_type is None and self.jobs:
            self.submit()

    def __len__(self) -> int:
        return len(self.jobs)

    def __iter__(self) -> Iterator[PipelineJob]:
        return iter(self.jobs)
