"""Tests for hpc_runner.core.timeutil."""

from datetime import datetime, timedelta

import pytest

from hpc_runner.core.timeutil import parse_since


class TestParseSince:
    """Tests for parse_since()."""

    def test_relative_seconds(self):
        before = datetime.now()
        result = parse_since("90s")
        after = datetime.now()

        assert before - timedelta(seconds=91) < result < after - timedelta(seconds=89)

    def test_relative_minutes(self):
        before = datetime.now()
        result = parse_since("30m")
        after = datetime.now()

        expected = timedelta(minutes=30)
        assert (
            before - expected - timedelta(seconds=1)
            < result
            < after - expected + timedelta(seconds=1)
        )

    def test_relative_hours(self):
        before = datetime.now()
        result = parse_since("2h")
        after = datetime.now()

        expected = timedelta(hours=2)
        assert (
            before - expected - timedelta(seconds=1)
            < result
            < after - expected + timedelta(seconds=1)
        )

    def test_relative_days(self):
        before = datetime.now()
        result = parse_since("1d")
        after = datetime.now()

        expected = timedelta(days=1)
        assert (
            before - expected - timedelta(seconds=1)
            < result
            < after - expected + timedelta(seconds=1)
        )

    def test_absolute_iso_datetime(self):
        result = parse_since("2026-02-23T18:00:00")
        assert result == datetime(2026, 2, 23, 18, 0, 0)

    def test_absolute_iso_with_space(self):
        result = parse_since("2026-02-23 18:00")
        assert result == datetime(2026, 2, 23, 18, 0)

    def test_absolute_date_only(self):
        result = parse_since("2026-02-23")
        assert result == datetime(2026, 2, 23, 0, 0, 0)

    def test_invalid_value(self):
        with pytest.raises(ValueError, match="Invalid --since value"):
            parse_since("foobar")

    def test_invalid_unit(self):
        with pytest.raises(ValueError, match="Invalid --since value"):
            parse_since("10x")

    def test_whitespace_stripped(self):
        result = parse_since("  2026-02-23T18:00:00  ")
        assert result == datetime(2026, 2, 23, 18, 0, 0)
