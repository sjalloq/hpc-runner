"""Tests for Pipeline API."""

from unittest.mock import MagicMock, patch

import pytest

from hpc_runner.core.config import HPCConfig
from hpc_runner.core.exceptions import (
    DependencyCycleError,
    DuplicateStepError,
    NotSubmittedError,
    PipelineError,
    UnknownStepError,
)
from hpc_runner.workflow import DependencyType, Pipeline


class TestPipeline:
    """Tests for Pipeline class."""

    def test_create_pipeline(self):
        """Test creating a pipeline."""
        p = Pipeline("my_pipeline")
        assert p.name == "my_pipeline"
        assert len(p) == 0

    def test_add_job(self):
        """Test adding a job to pipeline."""
        p = Pipeline()
        job = p.add("echo hello", name="step1")

        assert len(p) == 1
        assert job.name == "step1"
        assert job.job.command == "echo hello"

    def test_add_job_auto_name(self):
        """Test auto-generated job name."""
        p = Pipeline()
        job = p.add("echo hello")

        assert job.name == "step_1"

    def test_add_job_with_kwargs(self):
        """Test adding job with extra kwargs."""
        p = Pipeline()
        job = p.add("echo hello", name="step1", cpu=4, mem="8G")

        assert job.job.cpu == 4
        assert job.job.mem == "8G"

    def test_add_job_with_dependencies(self):
        """Test adding job with dependencies."""
        p = Pipeline()
        step1 = p.add("echo step1", name="step1")
        step2 = p.add("echo step2", name="step2", depends_on=["step1"])

        assert len(step2.depends_on) == 1
        assert step2.depends_on[0] is step1

    def test_add_job_with_pipeline_job_dependency(self):
        """Test adding job with PipelineJob dependency."""
        p = Pipeline()
        step1 = p.add("echo step1", name="step1")
        step2 = p.add("echo step2", name="step2", depends_on=[step1])

        assert step2.depends_on[0] is step1

    def test_duplicate_name_raises(self):
        """Test that duplicate job names raise error."""
        p = Pipeline()
        p.add("echo hello", name="step1")

        with pytest.raises(DuplicateStepError, match="already exists"):
            p.add("echo world", name="step1")

    def test_unknown_dependency_raises(self):
        """Test that unknown dependency raises error."""
        p = Pipeline()
        p.add("echo hello", name="step1")

        with pytest.raises(UnknownStepError, match="Unknown dependency"):
            p.add("echo world", name="step2", depends_on=["nonexistent"])

    def test_topological_sort(self):
        """Test topological sorting of jobs."""
        p = Pipeline()
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
        p = Pipeline()

        # Create jobs first without dependencies
        p.add("echo step1", name="step1")
        p.add("echo step2", name="step2", depends_on=["step1"])

        # Manually create a cycle
        p._name_map["step1"].depends_on.append(p._name_map["step2"])

        with pytest.raises(DependencyCycleError, match="Circular dependency"):
            p._topological_sort()

    def test_get_job(self):
        """Test getting job by name."""
        p = Pipeline()
        step1 = p.add("echo hello", name="step1")

        assert p.get_job("step1") is step1
        assert p.get_job("nonexistent") is None

    def test_context_manager_auto_submits(self):
        """Context manager auto-submits on clean exit."""
        mock_scheduler = MagicMock()
        mock_scheduler.submit.return_value = MagicMock(job_id="1")

        with Pipeline("test", scheduler=mock_scheduler) as p:
            p.add("echo hello", name="step1")

        assert len(p) == 1
        mock_scheduler.submit.assert_called_once()
        assert p.results["step1"].job_id == "1"

    def test_context_manager_skips_submit_on_exception(self):
        """Context manager does NOT submit if the with-block raises."""
        mock_scheduler = MagicMock()

        with pytest.raises(ValueError, match="boom"):
            with Pipeline("test", scheduler=mock_scheduler) as p:
                p.add("echo hello", name="step1")
                raise ValueError("boom")

        mock_scheduler.submit.assert_not_called()

    def test_context_manager_skips_empty_pipeline(self):
        """Context manager does nothing for an empty pipeline."""
        mock_scheduler = MagicMock()

        with Pipeline("test", scheduler=mock_scheduler):
            pass

        mock_scheduler.submit.assert_not_called()

    def test_iteration(self):
        """Test iterating over pipeline jobs."""
        p = Pipeline()
        p.add("echo step1", name="step1")
        p.add("echo step2", name="step2")

        names = [job.name for job in p]
        assert "step1" in names
        assert "step2" in names

    def test_submit_sets_dependencies(self):
        """Test that submit sets job dependencies correctly."""
        p = Pipeline()
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
        assert len(second_job.dependencies) == 1
        assert second_job.dependencies[0].job_id == "123"

    def test_results_property(self):
        """results property returns submitted job results."""
        p = Pipeline()
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

        p = Pipeline("test", scheduler=mock_scheduler)
        p.add("echo hello", name="step1")
        p.submit()

        mock_scheduler.submit.assert_called_once()


class TestPipelineConfig:
    """Tests for config-aware job creation in Pipeline."""

    def test_add_job_loads_defaults(self):
        """Jobs created via add() pick up [defaults] from config."""
        config = HPCConfig(defaults={"cpu": 2, "mem": "8G"})
        with patch("hpc_runner.core.config.get_config", return_value=config):
            p = Pipeline()
            job = p.add("echo hello", name="step1")

        assert job.job.cpu == 2
        assert job.job.mem == "8G"

    def test_add_job_with_tool(self):
        """Tool auto-detected from command picks up [tools.<name>] config."""
        config = HPCConfig(
            defaults={"cpu": 1},
            tools={"python": {"cpu": 4, "modules": ["python/3.11"]}},
        )
        with patch("hpc_runner.core.config.get_config", return_value=config):
            p = Pipeline()
            job = p.add("python script.py", name="step1")

        assert job.job.cpu == 4
        assert job.job.modules == ["python/3.11"]

    def test_add_job_with_job_type(self):
        """Jobs created with job_type= pick up [types.<name>] config."""
        config = HPCConfig(
            defaults={"cpu": 1},
            types={"gpu": {"queue": "gpu", "cpu": 8}},
        )
        with patch("hpc_runner.core.config.get_config", return_value=config):
            p = Pipeline()
            job = p.add("python train.py", name="train", job_type="gpu")

        assert job.job.queue == "gpu"
        assert job.job.cpu == 8

    def test_kwargs_override_config(self):
        """Explicit kwargs override values from config."""
        config = HPCConfig(defaults={"cpu": 1, "mem": "4G"})
        with patch("hpc_runner.core.config.get_config", return_value=config):
            p = Pipeline()
            job = p.add("echo hello", name="step1", cpu=16)

        assert job.job.cpu == 16
        assert job.job.mem == "4G"

    def test_job_name_prefixed_with_pipeline_name(self):
        """Job.name is always prefixed with the pipeline name."""
        p = Pipeline("my_pipe")
        job = p.add("echo hello", name="step1")

        assert job.job.name == "my_pipe_step1"


class TestPipelinePerJobDependencyType:
    """Tests for per-job dependency_type."""

    def test_default_dependency_type(self):
        """PipelineJob defaults to AFTEROK."""
        p = Pipeline()
        job = p.add("echo hello", name="step1")

        assert job.dependency_type == DependencyType.AFTEROK

    def test_custom_dependency_type(self):
        """Per-job dependency_type is set via add()."""
        p = Pipeline()
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
        p = Pipeline()
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
        assert second_job.dependency_type == "afterany"

    def test_mixed_dependency_types(self):
        """Different jobs can have different dependency types."""
        p = Pipeline()
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
        assert calls[1][0][0].dependency_type == "afterok"
        assert calls[2][0][0].dependency_type == "afterany"


class TestPipelinePartialSubmission:
    """Tests for partial submission recovery."""

    def test_submit_empty_pipeline_raises(self):
        """Submitting an empty pipeline raises."""
        p = Pipeline()

        with pytest.raises(PipelineError, match="no jobs to submit"):
            p.submit(scheduler=MagicMock())

    def test_submit_already_submitted_raises(self):
        """Submitting a fully-submitted pipeline raises."""
        p = Pipeline()
        p.add("echo hello", name="step1")

        mock_scheduler = MagicMock()
        mock_scheduler.submit.return_value = MagicMock(job_id="1")
        p.submit(scheduler=mock_scheduler)

        with pytest.raises(PipelineError, match="already been submitted"):
            p.submit(scheduler=mock_scheduler)

    def test_partial_failure_can_retry(self):
        """After a partial failure, submit() retries only unsubmitted jobs."""
        p = Pipeline()
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
        p = Pipeline()
        p.add("echo hello", name="step1")

        with pytest.raises(NotSubmittedError, match="has not been submitted"):
            p.wait()

    def test_wait_raises_on_partial_submission(self):
        """wait() raises if some jobs were never submitted."""
        p = Pipeline()
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
