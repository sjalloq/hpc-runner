"""Tests for Pipeline API."""

from itertools import count
from unittest.mock import MagicMock

import pytest

from hpc_runner.core.config import HPCConfig
from hpc_runner.core.dependency import Dependency
from hpc_runner.core.exceptions import (
    DependencyCycleError,
    DuplicateStepError,
    NotSubmittedError,
    PipelineError,
    UnknownStepError,
)
from hpc_runner.core.spec import JobSpec
from hpc_runner.core.units import Memory
from hpc_runner.workflow import DependencyType, Pipeline
from tests.fakes import make_host


def _pipeline(name: str = "pipeline", config: HPCConfig | None = None, **kwargs) -> Pipeline:
    return Pipeline(name, config=config or HPCConfig(), host=make_host(), **kwargs)


def _submitted_jobs(p: Pipeline) -> list:
    """Submit *p* to a mock scheduler; return the resolved Jobs it received."""
    ids = count(1)
    scheduler = MagicMock()
    scheduler.submit.side_effect = lambda job: MagicMock(job_id=str(next(ids)), job=job)
    p.submit(scheduler=scheduler)
    return [c.args[0] for c in scheduler.submit.call_args_list]


class TestPipeline:
    """Tests for Pipeline class."""

    def test_create_pipeline(self):
        """Test creating a pipeline."""
        p = _pipeline("my_pipeline")
        assert p.name == "my_pipeline"
        assert len(p) == 0

    def test_add_job(self):
        """Test adding a job to pipeline."""
        p = _pipeline()
        job = p.add("echo hello", name="step1")

        assert len(p) == 1
        assert job.name == "step1"
        assert job.spec.command == "echo hello"
        assert job.result is None

    def test_add_job_auto_name(self):
        """Test auto-generated job name."""
        p = _pipeline()
        job = p.add("echo hello")

        assert job.name == "step_1"

    def test_add_job_spec(self):
        """A JobSpec is stored as given and resolved at submit."""
        p = _pipeline()
        spec = JobSpec("echo hello", cpu=4, mem="8G")
        step = p.add(spec, name="step1")

        assert step.spec is spec
        (job,) = _submitted_jobs(p)
        assert job.cpu == 4
        assert job.mem == Memory.parse("8G")

    def test_add_job_with_dependencies(self):
        """Test adding job with dependencies."""
        p = _pipeline()
        step1 = p.add("echo step1", name="step1")
        step2 = p.add("echo step2", name="step2", depends_on=["step1"])

        assert len(step2.depends_on) == 1
        assert step2.depends_on[0] is step1

    def test_add_job_with_pipeline_job_dependency(self):
        """Test adding job with PipelineJob dependency."""
        p = _pipeline()
        step1 = p.add("echo step1", name="step1")
        step2 = p.add("echo step2", name="step2", depends_on=[step1])

        assert step2.depends_on[0] is step1

    def test_duplicate_name_raises(self):
        """Test that duplicate job names raise error."""
        p = _pipeline()
        p.add("echo hello", name="step1")

        with pytest.raises(DuplicateStepError, match="already exists"):
            p.add("echo world", name="step1")

    def test_unknown_dependency_raises(self):
        """Test that unknown dependency raises error."""
        p = _pipeline()
        p.add("echo hello", name="step1")

        with pytest.raises(UnknownStepError, match="Unknown dependency"):
            p.add("echo world", name="step2", depends_on=["nonexistent"])

    def test_topological_sort(self):
        """Test topological sorting of jobs."""
        p = _pipeline()
        p.add("echo step3", name="step3", depends_on=[])
        p.add("echo step1", name="step1", depends_on=[])
        p.add("echo step2", name="step2", depends_on=["step1", "step3"])

        sorted_jobs = p._topological_sort()

        # step1 and step3 should come before step2
        step2_idx = next(i for i, j in enumerate(sorted_jobs) if j.name == "step2")
        step1_idx = next(i for i, j in enumerate(sorted_jobs) if j.name == "step1")
        step3_idx = next(i for i, j in enumerate(sorted_jobs) if j.name == "step3")

        assert step1_idx < step2_idx
        assert step3_idx < step2_idx

    def test_circular_dependency_raises(self):
        """Test that circular dependencies raise error."""
        p = _pipeline()

        # Create jobs first without dependencies
        p.add("echo step1", name="step1")
        p.add("echo step2", name="step2", depends_on=["step1"])

        # Manually create a cycle
        p._name_map["step1"].depends_on.append(p._name_map["step2"])

        with pytest.raises(DependencyCycleError, match="Circular dependency"):
            p._topological_sort()

    def test_get_job(self):
        """Test getting job by name."""
        p = _pipeline()
        step1 = p.add("echo hello", name="step1")

        assert p.get_job("step1") is step1
        with pytest.raises(UnknownStepError, match="nonexistent"):
            p.get_job("nonexistent")

    def test_context_manager_auto_submits(self):
        """Context manager auto-submits on clean exit."""
        mock_scheduler = MagicMock()
        mock_scheduler.submit.return_value = MagicMock(job_id="1")

        with _pipeline("test", scheduler=mock_scheduler) as p:
            p.add("echo hello", name="step1")

        assert len(p) == 1
        mock_scheduler.submit.assert_called_once()
        assert p.results["step1"].job_id == "1"

    def test_context_manager_skips_submit_on_exception(self):
        """Context manager does NOT submit if the with-block raises."""
        mock_scheduler = MagicMock()

        with pytest.raises(ValueError, match="boom"):
            with _pipeline("test", scheduler=mock_scheduler) as p:
                p.add("echo hello", name="step1")
                raise ValueError("boom")

        mock_scheduler.submit.assert_not_called()

    def test_context_manager_skips_empty_pipeline(self):
        """Context manager does nothing for an empty pipeline."""
        mock_scheduler = MagicMock()

        with _pipeline("test", scheduler=mock_scheduler):
            pass

        mock_scheduler.submit.assert_not_called()

    def test_iteration(self):
        """Test iterating over pipeline jobs."""
        p = _pipeline()
        p.add("echo step1", name="step1")
        p.add("echo step2", name="step2")

        names = [job.name for job in p]
        assert "step1" in names
        assert "step2" in names

    def test_submit_sets_dependencies(self):
        """Test that submit sets job dependencies correctly."""
        p = _pipeline()
        p.add("echo step1", name="step1")
        p.add("echo step2", name="step2", depends_on=["step1"])

        # Create mock scheduler
        mock_scheduler = MagicMock()
        mock_result1 = MagicMock()
        mock_result1.job_id = "123"
        mock_result2 = MagicMock()
        mock_result2.job_id = "456"

        mock_scheduler.submit.side_effect = [mock_result1, mock_result2]

        p.submit(scheduler=mock_scheduler)

        # Check that step2's job has step1's result as dependency
        calls = mock_scheduler.submit.call_args_list
        assert len(calls) == 2

        # The second job should have dependencies set
        second_job = calls[1][0][0]
        assert second_job.dependencies == (Dependency("123", DependencyType.AFTEROK),)

    def test_results_property(self):
        """results property returns submitted job results."""
        p = _pipeline()
        p.add("echo step1", name="step1")
        p.add("echo step2", name="step2")

        # Before submission — empty
        assert p.results == {}

        mock_scheduler = MagicMock()
        mock_scheduler.submit.side_effect = [
            MagicMock(job_id="1"),
            MagicMock(job_id="2"),
        ]
        p.submit(scheduler=mock_scheduler)

        assert len(p.results) == 2
        assert p.results["step1"].job_id == "1"
        assert p.results["step2"].job_id == "2"

    def test_constructor_scheduler_used_by_submit(self):
        """Scheduler passed to __init__ is used by submit()."""
        mock_scheduler = MagicMock()
        mock_scheduler.submit.return_value = MagicMock(job_id="1")

        p = _pipeline("test", scheduler=mock_scheduler)
        p.add("echo hello", name="step1")
        p.submit()

        mock_scheduler.submit.assert_called_once()


class TestPipelineConfig:
    """Steps are resolved against the pipeline's config at submit time."""

    def test_add_job_loads_defaults(self):
        p = _pipeline(config=HPCConfig(defaults={"cpu": 2, "mem": "8G"}))
        p.add("echo hello", name="step1")
        (job,) = _submitted_jobs(p)
        assert job.cpu == 2
        assert job.mem == Memory.parse("8G")

    def test_add_job_with_tool(self):
        config = HPCConfig(
            defaults={"cpu": 1},
            tools={"python": {"cpu": 4, "modules": ["python/3.11"]}},
        )
        p = _pipeline(config=config)
        p.add("python script.py", name="step1")
        (job,) = _submitted_jobs(p)
        assert job.cpu == 4
        assert job.modules == ("python/3.11",)

    def test_add_job_with_type(self):
        config = HPCConfig(defaults={"cpu": 1}, types={"gpu": {"queue": "gpu", "cpu": 8}})
        p = _pipeline(config=config)
        p.add(JobSpec("python train.py", type="gpu"), name="train")
        (job,) = _submitted_jobs(p)
        assert job.queue == "gpu"
        assert job.cpu == 8

    def test_spec_overrides_config(self):
        p = _pipeline(config=HPCConfig(defaults={"cpu": 1, "mem": "4G"}))
        p.add(JobSpec("echo hello", cpu=16), name="step1")
        (job,) = _submitted_jobs(p)
        assert job.cpu == 16
        assert job.mem == Memory.parse("4G")

    def test_job_name_prefixed_with_pipeline_name(self):
        p = _pipeline("my_pipe")
        p.add("echo hello", name="step1")
        (job,) = _submitted_jobs(p)
        assert job.name == "my_pipe_step1"

    def test_spec_is_not_mutated(self):
        p = _pipeline()
        p.add("echo 1", name="a")
        spec = JobSpec("echo 2")
        p.add(spec, name="b", depends_on=["a"])
        _submitted_jobs(p)
        assert spec.dependencies == []
        assert spec.name is None

    def test_host_used_for_resolution(self):
        p = Pipeline("p", config=HPCConfig(), host=make_host(env={"V": "from-host"}))
        p.add(JobSpec("echo hi", env_vars={"X": "$V"}), name="s")
        (job,) = _submitted_jobs(p)
        assert job.env_vars["X"] == "from-host"


class TestPipelinePerJobDependencyType:
    """Tests for per-job dependency_type."""

    def test_default_dependency_type(self):
        """PipelineJob defaults to AFTEROK."""
        p = _pipeline()
        job = p.add("echo hello", name="step1")

        assert job.dependency_type == DependencyType.AFTEROK

    def test_custom_dependency_type(self):
        """Per-job dependency_type is set via add()."""
        p = _pipeline()
        p.add("echo step1", name="step1")
        step2 = p.add(
            "echo step2",
            name="step2",
            depends_on=["step1"],
            dependency_type=DependencyType.AFTERANY,
        )

        assert step2.dependency_type == DependencyType.AFTERANY

    def test_submit_uses_per_job_dependency_type(self):
        """submit() applies each job's own dependency_type."""
        p = _pipeline()
        p.add("echo step1", name="step1")
        p.add(
            "echo step2",
            name="step2",
            depends_on=["step1"],
            dependency_type=DependencyType.AFTERANY,
        )

        mock_scheduler = MagicMock()
        mock_scheduler.submit.side_effect = [MagicMock(job_id="1"), MagicMock(job_id="2")]

        p.submit(scheduler=mock_scheduler)

        second_job = mock_scheduler.submit.call_args_list[1][0][0]
        assert second_job.dependencies == (Dependency("1", DependencyType.AFTERANY),)

    def test_mixed_dependency_types(self):
        """Different jobs can have different dependency types."""
        p = _pipeline()
        p.add("echo step1", name="step1")
        p.add(
            "echo step2",
            name="step2",
            depends_on=["step1"],
            dependency_type=DependencyType.AFTEROK,
        )
        p.add(
            "echo cleanup",
            name="cleanup",
            depends_on=["step1"],
            dependency_type=DependencyType.AFTERANY,
        )

        mock_scheduler = MagicMock()
        results = [MagicMock(job_id=str(i)) for i in range(3)]
        mock_scheduler.submit.side_effect = results

        p.submit(scheduler=mock_scheduler)

        calls = mock_scheduler.submit.call_args_list
        assert calls[1][0][0].dependencies[0].type is DependencyType.AFTEROK
        assert calls[2][0][0].dependencies[0].type is DependencyType.AFTERANY


class TestPipelinePartialSubmission:
    """Tests for partial submission recovery."""

    def test_submit_empty_pipeline_raises(self):
        """Submitting an empty pipeline raises."""
        p = _pipeline()

        with pytest.raises(PipelineError, match="no jobs to submit"):
            p.submit(scheduler=MagicMock())

    def test_submit_already_submitted_raises(self):
        """Submitting a fully-submitted pipeline raises."""
        p = _pipeline()
        p.add("echo hello", name="step1")

        mock_scheduler = MagicMock()
        mock_scheduler.submit.return_value = MagicMock(job_id="1")
        p.submit(scheduler=mock_scheduler)

        with pytest.raises(PipelineError, match="already been submitted"):
            p.submit(scheduler=mock_scheduler)

    def test_partial_failure_can_retry(self):
        """After a partial failure, submit() retries only unsubmitted jobs."""
        p = _pipeline()
        p.add("echo step1", name="step1")
        p.add("echo step2", name="step2", depends_on=["step1"])
        p.add("echo step3", name="step3", depends_on=["step2"])

        mock_scheduler = MagicMock()
        mock_result1 = MagicMock(job_id="1")

        # First submit: step1 succeeds, step2 fails
        mock_scheduler.submit.side_effect = [mock_result1, RuntimeError("submit failed")]

        with pytest.raises(RuntimeError, match="submit failed"):
            p.submit(scheduler=mock_scheduler)

        # step1 has a result, step2 and step3 do not
        assert p.get_job("step1").result is not None
        assert p.get_job("step2").result is None
        assert p.get_job("step3").result is None

        # Retry: step1 is skipped, step2 and step3 are submitted
        mock_result2 = MagicMock(job_id="2")
        mock_result3 = MagicMock(job_id="3")
        mock_scheduler.submit.side_effect = [mock_result2, mock_result3]
        mock_scheduler.submit.reset_mock()

        results = p.submit(scheduler=mock_scheduler)

        assert len(results) == 3
        assert mock_scheduler.submit.call_count == 2  # only step2 and step3

    def test_wait_raises_on_no_submission(self):
        """wait() raises if nothing was submitted."""
        p = _pipeline()
        p.add("echo hello", name="step1")

        with pytest.raises(NotSubmittedError, match="has not been submitted"):
            p.wait()

    def test_wait_raises_on_partial_submission(self):
        """wait() raises if some jobs were never submitted."""
        p = _pipeline()
        p.add("echo step1", name="step1")
        p.add("echo step2", name="step2", depends_on=["step1"])

        mock_scheduler = MagicMock()
        mock_scheduler.submit.side_effect = [MagicMock(job_id="1"), RuntimeError("fail")]

        with pytest.raises(RuntimeError):
            p.submit(scheduler=mock_scheduler)

        with pytest.raises(NotSubmittedError, match="partial failure"):
            p.wait()


class TestDependencyType:
    """Tests for DependencyType enum."""

    def test_dependency_types(self):
        """Test dependency type values."""
        assert str(DependencyType.AFTEROK) == "afterok"
        assert str(DependencyType.AFTERANY) == "afterany"
        assert str(DependencyType.AFTER) == "after"
        assert str(DependencyType.AFTERNOTOK) == "afternotok"


class TestPipelineInteractiveParent:
    def test_interactive_parent_has_no_id_to_depend_on(self):
        p = _pipeline()
        p.add(JobSpec("echo 1", interactive=True), name="a")
        p.add("echo 2", name="b", depends_on=["a"])
        scheduler = MagicMock()
        scheduler.submit.return_value = MagicMock(job_id=None)
        with pytest.raises(PipelineError, match="no job id"):
            p.submit(scheduler=scheduler)


class TestPipelineDependenciesAndWait:
    def test_array_parent_dependency_records_tasks(self):
        from hpc_runner.core.result import ArrayJobResult, JobResult

        p = _pipeline()
        p.add(JobSpec("sim", array="1-3"), name="sims")
        p.add("merge", name="merge", depends_on=["sims"])
        scheduler = MagicMock()

        def submit(job):
            if job.array is not None:
                return ArrayJobResult(scheduler=scheduler, job_id="10", job=job, array=job.array)
            return JobResult(scheduler=scheduler, job_id="11", job=job)

        scheduler.submit.side_effect = submit
        p.submit(scheduler=scheduler)
        merge_job = scheduler.submit.call_args_list[1].args[0]
        assert merge_job.dependencies == (Dependency("10", DependencyType.AFTEROK, (1, 2, 3)),)

    def test_wait_one_get_statuses_per_tick(self):
        from hpc_runner.core.result import ArrayJobResult, JobResult, JobStatus

        p = _pipeline()
        p.add(JobSpec("sim", array="1-2"), name="sims")
        p.add("merge", name="merge", depends_on=["sims"])
        scheduler = MagicMock()
        scheduler.task_job_id.side_effect = lambda base, index: f"{base}.{index}"
        ticks = iter(
            [
                {"10.1": JobStatus.RUNNING, "10.2": JobStatus.COMPLETED, "11": JobStatus.PENDING},
                {"10.1": JobStatus.COMPLETED, "11": JobStatus.COMPLETED},
            ]
        )
        scheduler.get_statuses.side_effect = lambda ids: next(ticks)

        def submit(job):
            if job.array is not None:
                return ArrayJobResult(scheduler=scheduler, job_id="10", job=job, array=job.array)
            return JobResult(scheduler=scheduler, job_id="11", job=job)

        scheduler.submit.side_effect = submit
        p.submit(scheduler=scheduler)
        assert p.wait(poll_interval=0) == {
            "sims": JobStatus.COMPLETED,
            "merge": JobStatus.COMPLETED,
        }
        calls = [c.args[0] for c in scheduler.get_statuses.call_args_list]
        assert calls == [["10.1", "10.2", "11"], ["10.1", "11"]]
        scheduler.get_status.assert_not_called()

    def test_wait_timeout_and_unknown_after(self):
        from hpc_runner.core.result import JobResult, JobStatus

        p = _pipeline()
        p.add("a", name="a")
        scheduler = MagicMock()
        scheduler.submit.side_effect = lambda job: JobResult(scheduler, "1", job)
        p.submit(scheduler=scheduler)

        scheduler.get_statuses.side_effect = lambda ids: {"1": JobStatus.RUNNING}
        with pytest.raises(TimeoutError):
            p.wait(poll_interval=0, timeout=0)

        scheduler.get_statuses.side_effect = lambda ids: {"1": JobStatus.UNKNOWN}
        assert p.wait(poll_interval=0, unknown_after=0) == {"a": JobStatus.UNKNOWN}


class TestPipelineResultsAndStatuses:
    def _submitted(self, statuses):
        from hpc_runner.core.result import ArrayJobResult, JobResult

        p = _pipeline()
        p.add(JobSpec("sim", array="1-3"), name="sims")
        p.add("report", name="report")
        scheduler = MagicMock()
        scheduler.task_job_id.side_effect = lambda base, index: f"{base}.{index}"
        scheduler.get_statuses.side_effect = lambda ids: {i: statuses[i] for i in ids}

        def submit(job):
            if job.array is not None:
                return ArrayJobResult(scheduler=scheduler, job_id="10", job=job, array=job.array)
            return JobResult(scheduler=scheduler, job_id="11", job=job)

        scheduler.submit.side_effect = submit
        p.submit(scheduler=scheduler)
        return p

    def test_results_is_one_read_only_live_mapping(self):
        from collections.abc import Mapping

        p = _pipeline()
        p.add("a", name="a")
        view = p.results
        assert isinstance(view, Mapping) and len(view) == 0
        assert p.results is view
        with pytest.raises(TypeError):
            view["a"] = None  # type: ignore[index]
        scheduler = MagicMock()
        scheduler.submit.return_value = MagicMock(job_id="1")
        p.submit(scheduler=scheduler)
        assert view["a"].job_id == "1"

    def test_wait_returns_statuses(self):
        from hpc_runner.core.result import JobStatus

        done = JobStatus.COMPLETED
        p = self._submitted({"10.1": done, "10.2": done, "10.3": done, "11": JobStatus.FAILED})
        assert p.wait(poll_interval=0) == {"sims": done, "report": JobStatus.FAILED}

    def test_array_step_reports_first_task_not_completed(self):
        from hpc_runner.core.result import JobStatus

        done = JobStatus.COMPLETED
        p = self._submitted(
            {"10.1": done, "10.2": JobStatus.SKIPPED, "10.3": JobStatus.FAILED, "11": done}
        )
        assert p.wait(poll_interval=0)["sims"] is JobStatus.SKIPPED
