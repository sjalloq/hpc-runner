"""Tests for dependencies: the string grammar and JobSpec.after()."""

import pytest

from hpc_runner.core.array import ArrayRange
from hpc_runner.core.dependency import (
    Dependency,
    DependencyType,
    dependency_on,
    parse_dependency_string,
)
from hpc_runner.core.result import ArrayJobResult, JobResult
from hpc_runner.core.spec import JobSpec

OK = DependencyType.AFTEROK
ANY = DependencyType.AFTERANY
NOTOK = DependencyType.AFTERNOTOK


def _result(job_id: str) -> JobResult:
    return JobResult(scheduler=None, job_id=job_id)  # type: ignore[arg-type]


def _array(job_id: str, text: str) -> ArrayJobResult:
    return ArrayJobResult(scheduler=None, job_id=job_id, array=ArrayRange.parse(text))  # type: ignore[arg-type]


class TestParseDependencyString:
    def test_bare_id_is_afterok(self):
        assert parse_dependency_string("123") == [Dependency("123", OK)]

    def test_type_with_repeated_ids(self):
        assert parse_dependency_string("afterok:1:2") == [Dependency("1", OK), Dependency("2", OK)]

    def test_comma_separates_types(self):
        assert parse_dependency_string("afterok:1:2,afterany:3,afternotok:4") == [
            Dependency("1", OK),
            Dependency("2", OK),
            Dependency("3", ANY),
            Dependency("4", NOTOK),
        ]

    def test_bare_id_in_a_list(self):
        assert parse_dependency_string("5,afterany:6") == [
            Dependency("5", OK),
            Dependency("6", ANY),
        ]

    def test_after_parses(self):
        """'after' is grammar; a scheduler that cannot express it refuses it."""
        assert parse_dependency_string("after:9") == [Dependency("9", DependencyType.AFTER)]

    def test_unknown_type_lists_valid_ones(self):
        with pytest.raises(ValueError) as info:
            parse_dependency_string("afterbogus:1")
        message = str(info.value)
        assert "afterbogus" in message
        for name in ("afterok", "afterany", "after", "afternotok"):
            assert name in message

    @pytest.mark.parametrize(
        "text", ["", "afterok:", "afterok::1", "1,,2", "1 2", "afterok", "afterok:1,", ":1"]
    )
    def test_malformed(self, text):
        with pytest.raises(ValueError):
            parse_dependency_string(text)

    def test_string_form_has_no_tasks(self):
        assert parse_dependency_string("7")[0].tasks is None


class TestAfter:
    def test_mixed_types_accumulate(self):
        spec = JobSpec("x").after(_result("1"), type=OK).after(_result("2"), type=ANY)
        assert spec.dependencies == [Dependency("1", OK), Dependency("2", ANY)]

    def test_one_call_many_parents(self):
        spec = JobSpec("x").after(_result("1"), _result("2"), type=NOTOK)
        assert [d.type for d in spec.dependencies] == [NOTOK, NOTOK]

    def test_array_parent_records_tasks(self):
        spec = JobSpec("x").after(_array("7", "1-5:2"))
        assert spec.dependencies == [Dependency("7", OK, (1, 3, 5))]

    def test_dependency_on_plain_result(self):
        assert dependency_on(_result("3"), ANY) == Dependency("3", ANY, None)

    def test_dependency_on_interactive_rejected(self):
        with pytest.raises(ValueError):
            dependency_on(JobResult(scheduler=None, job_id=None, exit_code=0))  # type: ignore[arg-type]

    def test_str_is_value(self):
        assert str(NOTOK) == "afternotok"
