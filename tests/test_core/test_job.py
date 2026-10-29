"""Tests for the resolved, frozen Job."""

import dataclasses

import pytest

from hpc_runner.core.job import RENDER_ORDER, Job
from tests.fakes import make_job


class TestJobValue:
    def test_frozen(self):
        job = make_job()
        with pytest.raises(dataclasses.FrozenInstanceError):
            job.cpu = 4  # type: ignore[misc]

    def test_mappings_are_read_only(self):
        job = make_job(env_vars={"A": "1"}, scheduler_args={"sge": ["-l", "x=1"]})
        with pytest.raises(TypeError):
            job.env_vars["B"] = "2"  # type: ignore[index]
        with pytest.raises(TypeError):
            job.scheduler_args["sge"] = ()  # type: ignore[index]
        with pytest.raises(TypeError):
            job.provenance["cpu"] = "spec"  # type: ignore[index]

    def test_lists_are_tuples(self):
        job = make_job(modules=["a"], modules_path=["/m"], env_unset=["X"])
        assert job.modules == ("a",)
        assert job.modules_path == ("/m",)
        assert job.env_unset == ("X",)
        assert job.scheduler_args == {}
        assert job.dependencies == ()

    def test_replace_makes_a_new_job(self):
        job = make_job(cpu=2)
        other = dataclasses.replace(job, cpu=8)
        assert job.cpu == 2
        assert other.cpu == 8

    def test_merge_output_default(self):
        job = make_job()
        assert job.stderr is None
        assert job.merge_output is True

    def test_separate_stderr(self):
        job = make_job(stderr="error.log")
        assert job.merge_output is False

    def test_no_convenience_constructor(self):
        with pytest.raises(TypeError):
            Job("echo hi")  # type: ignore[misc]

    def test_no_submit_or_after(self):
        assert not hasattr(Job, "submit")
        assert not hasattr(Job, "after")


class TestRenderOrder:
    def test_every_entry_is_a_job_field(self):
        fields = {f.name for f in dataclasses.fields(Job)}
        assert set(RENDER_ORDER) <= fields

    def test_order(self):
        assert RENDER_ORDER[:4] == ("shell", "use_cwd", "inherit_env", "name")
        assert "project" in RENDER_ORDER
