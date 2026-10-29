"""Tests for the D1 list merge rule."""

import pytest

from hpc_runner import Edit, Set
from hpc_runner.core.config import HPCConfig
from hpc_runner.core.merge import compose, merge_list, resource_key
from hpc_runner.core.resources import ResourceSet
from tests.fakes import make_job


def _r(name, value):
    return {"name": name, "value": value}


class TestMergeList:
    def test_plain_list_appends_and_dedupes(self):
        assert merge_list(["a", "b"], ["c", "a", "d"]) == ["a", "b", "c", "d"]

    def test_dedupes_within_one_layer(self):
        assert merge_list([], ["a", "b", "a"]) == ["a", "b"]

    def test_set_replaces(self):
        assert merge_list(["a", "b"], Set(["x", "y"])) == ["x", "y"]

    def test_edit_removes_then_adds(self):
        assert merge_list(["a", "b", "c"], Edit(remove=["b"], add=["d", "a"])) == ["a", "c", "d"]

    def test_edit_either_key_optional(self):
        assert merge_list(["a", "b"], Edit(remove=["a"])) == ["b"]
        assert merge_list(["a"], Edit(add=["b"])) == ["a", "b"]

    def test_edit_remove_missing_is_noop(self):
        assert merge_list(["a"], Edit(remove=["zzz"])) == ["a"]

    def test_resources_keyed_by_name_later_wins(self):
        acc = [_r("gpu", 1), _r("scratch", "10G")]
        merged = merge_list(acc, [_r("tmp", 1), _r("gpu", 2)], key=resource_key)
        # One gpu, value 2, in the position of the first occurrence.
        assert merged == [_r("gpu", 2), _r("scratch", "10G"), _r("tmp", 1)]

    def test_resources_edit_removes_by_name(self):
        acc = [_r("gpu", 1), _r("scratch", "10G")]
        merged = merge_list(acc, Edit(remove=[_r("gpu", 99)]), key=resource_key)
        assert merged == [_r("scratch", "10G")]

    def test_string_layer_value_rejected(self):
        with pytest.raises(TypeError):
            merge_list([], "gcc/12")
        with pytest.raises(TypeError):
            Set("gcc/12")

    def test_no_dedupe_for_argv(self):
        assert merge_list(["-l", "a"], ["-l", "b"], dedupe=False) == ["-l", "a", "-l", "b"]
        assert merge_list(["-l", "a", "-l"], Edit(remove=["-l"]), dedupe=False) == ["a"]

    def test_acc_not_mutated(self):
        acc = ["a"]
        merge_list(acc, ["b"])
        assert acc == ["a"]


class TestCompose:
    """compose(a, b) applied once == a then b applied in turn."""

    CASES = [
        (["a"], ["b"]),
        (["a"], Set(["x"])),
        (["a", "p"], Edit(remove=["p"], add=["q"])),
        (Set(["x"]), ["y"]),
        (Set(["x", "p"]), Edit(remove=["p"])),
        (Edit(remove=["p"], add=["q"]), ["r"]),
        (Edit(remove=["p"], add=["q", "s"]), Edit(remove=["q", "t"], add=["u"])),
        (Edit(add=["q"]), Set(["z"])),
    ]

    @pytest.mark.parametrize("dedupe", [True, False])
    @pytest.mark.parametrize("first, second", CASES)
    def test_equivalent_to_sequential(self, first, second, dedupe):
        for acc in ([], ["p", "t", "base"], ["q", "s"]):
            sequential = merge_list(merge_list(acc, first, dedupe=dedupe), second, dedupe=dedupe)
            composed = compose(first, second, dedupe=dedupe)
            assert merge_list(acc, composed, dedupe=dedupe) == sequential


class TestSpecLayer:
    """A JobSpec list is one more layer: it appends exactly like config."""

    def test_spec_modules_append(self):
        cfg = HPCConfig(defaults={"modules": ["gcc/12"]}, tools={"python": {"modules": ["py"]}})
        job = make_job("python x.py", config=cfg, modules=["cuda/12", "gcc/12"])
        assert job.modules == ("gcc/12", "py", "cuda/12")

    def test_spec_set_replaces(self):
        cfg = HPCConfig(defaults={"modules": ["gcc/12", "py"]})
        job = make_job(config=cfg, modules=Set(["only/1"]))
        assert job.modules == ("only/1",)

    def test_spec_edit(self):
        cfg = HPCConfig(defaults={"modules": ["gcc/12", "py/3.11"]})
        job = make_job(config=cfg, modules=Edit(remove=["py/3.11"], add=["py/3.12"]))
        assert job.modules == ("gcc/12", "py/3.12")

    def test_spec_resources_keyed(self):
        cfg = HPCConfig(defaults={"resources": [_r("gpu", 1), _r("scratch", "1G")]})
        job = make_job(config=cfg, resources=ResourceSet().add("gpu", 2))
        assert [(r.name, r.value) for r in job.resources] == [("gpu", 2), ("scratch", "1G")]

    def test_spec_resources_set(self):
        cfg = HPCConfig(defaults={"resources": [_r("scratch", "1G")]})
        job = make_job(config=cfg, resources=Set([_r("gpu", 1)]))
        assert [r.name for r in job.resources] == ["gpu"]

    def test_type_layer_resource_overrides_default(self):
        cfg = HPCConfig(
            defaults={"resources": [_r("gpu", 1)]},
            types={"big": {"resources": [_r("gpu", 2)]}},
        )
        job = make_job(type="big", config=cfg)
        assert [(r.name, r.value) for r in job.resources] == [("gpu", 2)]
        assert job.provenance["resources"][0][1] == "types.big"
