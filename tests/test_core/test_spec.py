"""Tests for JobSpec."""

from hpc_runner.core.dependency import Dependency, DependencyType
from hpc_runner.core.result import JobResult
from hpc_runner.core.spec import JobSpec


def _result(job_id: str) -> JobResult:
    return JobResult(scheduler=None, job_id=job_id)  # type: ignore[arg-type]


class TestJobSpec:
    def test_command_is_positional(self):
        spec = JobSpec("echo hi")
        assert spec.command == "echo hi"

    def test_everything_unspecified_by_default(self):
        spec = JobSpec("echo hi")
        assert spec.cpu is None
        assert spec.venv is None
        assert spec.interactive is None
        assert spec.modules is None
        assert spec.dependencies == []

    def test_mutable_for_incremental_building(self):
        spec = JobSpec("echo hi")
        spec.cpu = 4
        spec.modules = ["gcc/13"]
        assert spec.cpu == 4

    def test_list_command_kept_as_given(self):
        spec = JobSpec(["bash", "-c", "echo hello world"])
        assert spec.command == ["bash", "-c", "echo hello world"]


class TestAfter:
    def test_after_appends_afterok(self):
        spec = JobSpec("echo hi").after(_result("1"), _result("2"))
        assert spec.dependencies == [
            Dependency("1", DependencyType.AFTEROK),
            Dependency("2", DependencyType.AFTEROK),
        ]

    def test_after_returns_self(self):
        spec = JobSpec("echo hi")
        assert spec.after(_result("1")) is spec

    def test_types_accumulate_per_parent(self):
        spec = (
            JobSpec("echo hi").after(_result("1")).after(_result("2"), type=DependencyType.AFTERANY)
        )
        assert [d.type for d in spec.dependencies] == [
            DependencyType.AFTEROK,
            DependencyType.AFTERANY,
        ]

    def test_interactive_result_rejected(self):
        import pytest

        with pytest.raises(ValueError):
            JobSpec("echo hi").after(_result(None))  # type: ignore[arg-type]
