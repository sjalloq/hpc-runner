"""Tests for ArrayRange."""

import pytest

from hpc_runner.core.array import ArrayRange


class TestParse:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("1-100", ArrayRange(1, 100)),
            ("1-100:10", ArrayRange(1, 100, 10)),
            ("1-100%5", ArrayRange(1, 100, 1, 5)),
            ("1-100:10%5", ArrayRange(1, 100, 10, 5)),
            ("7", ArrayRange(7, 7)),
        ],
    )
    def test_valid(self, text, expected):
        assert ArrayRange.parse(text) == expected

    @pytest.mark.parametrize("text", ["", "a-b", "1-", "1-10:", "1,2,3", "10-1", "1-10:0"])
    def test_invalid(self, text):
        with pytest.raises(ValueError):
            ArrayRange.parse(text)


class TestProperties:
    def test_range_str_round_trips(self):
        for text in ("1-100", "1-100:10", "1-100%5", "1-100:10%5"):
            assert ArrayRange.parse(text).range_str == text

    def test_indices_is_a_range(self):
        assert ArrayRange(1, 10, 3).indices == range(1, 11, 3)
        assert list(ArrayRange(1, 10, 3).indices) == [1, 4, 7, 10]

    def test_count(self):
        assert ArrayRange(1, 100).count == 100
        assert ArrayRange(1, 100, 10).count == 10

    def test_frozen(self):
        import dataclasses

        with pytest.raises(dataclasses.FrozenInstanceError):
            ArrayRange(1, 2).end = 5  # type: ignore[misc]
