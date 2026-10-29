"""Tests for the Memory and Duration value types (D10)."""

import pytest

from hpc_runner.core.units import Duration, Memory

G = 2**30
M = 2**20


class TestMemoryParse:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("16G", 16 * G),
            ("16GB", 16 * G),
            ("16GiB", 16 * G),
            ("16g", 16 * G),
            ("16gib", 16 * G),
            ("16Gb", 16 * G),
            ("4096M", 4096 * M),
            ("4096MB", 4096 * M),
            ("1.5G", 1536 * M),
            ("512K", 512 * 1024),
            ("512KiB", 512 * 1024),
            ("2T", 2 * 2**40),
            ("2TB", 2 * 2**40),
            ("1048576", 1048576),
            (" 8G ", 8 * G),
        ],
    )
    def test_accepted(self, text, expected):
        assert Memory.parse(text) == Memory(expected)

    def test_fraction_rounds_up_to_the_byte(self):
        assert Memory.parse("0.1K") == Memory(103)  # 102.4

    @pytest.mark.parametrize(
        "text", ["", "G", "16 G", "16X", "16GG", "-1G", "1.5", "16iB", "0", "0G", "1e3M"]
    )
    def test_rejected(self, text):
        with pytest.raises(ValueError, match="invalid memory"):
            Memory.parse(text)


class TestMemoryRender:
    @pytest.mark.parametrize(
        ("text", "sge"),
        [
            ("16G", "16G"),
            ("2T", "2T"),
            ("1024G", "1T"),
            ("4096M", "4G"),
            ("1.5G", "1536M"),
            ("512K", "512K"),
            ("1000", "1M"),  # not a whole K: rounded up in M
        ],
    )
    def test_sge(self, text, sge):
        assert Memory.parse(text).to_sge() == sge

    def test_sge_not_whole_kib_rounds_up_in_m(self):
        assert Memory(M + 1).to_sge() == "2M"

    def test_slurm_mb_rounds_up(self):
        assert Memory.parse("16G").to_slurm_mb() == 16384
        assert Memory.parse("1.5G").to_slurm_mb() == 1536
        assert Memory(M + 1).to_slurm_mb() == 2
        assert Memory(1).to_slurm_mb() == 1

    def test_pbs(self):
        assert Memory.parse("16G").to_pbs() == "16gb"
        assert Memory.parse("1.5G").to_pbs() == "1536mb"
        assert Memory.parse("512K").to_pbs() == "512kb"

    def test_str(self):
        assert str(Memory.parse("16G")) == "16 GiB (17179869184 bytes)"
        assert str(Memory.parse("1.5G")) == "1536 MiB (1610612736 bytes)"
        assert str(Memory(1000)) == "1000 bytes"


class TestMemoryDivide:
    def test_per_slot(self):
        assert Memory.parse("16G") / 4 == Memory.parse("4G")

    def test_rounds_up_to_the_byte(self):
        assert Memory(10) / 3 == Memory(4)
        assert (Memory.parse("1G") / 3).to_sge() == "342M"

    def test_by_one_is_unchanged(self):
        assert Memory.parse("16G") / 1 == Memory.parse("16G")


class TestDurationParse:
    @pytest.mark.parametrize(
        ("text", "seconds"),
        [
            ("4:00:00", 4 * 3600),
            ("04:30:15", 4 * 3600 + 30 * 60 + 15),
            ("100:00:00", 100 * 3600),
            ("30:00", 30 * 60),
            ("90:00", 90 * 60),  # MM:SS, leading field unbounded
            ("1-02:03:04", 86400 + 2 * 3600 + 3 * 60 + 4),
            ("1-02:03", 86400 + 2 * 3600 + 3 * 60),
            ("2-12", 2 * 86400 + 12 * 3600),
            ("90s", 90),
            ("30m", 30 * 60),
            ("4h", 4 * 3600),
            ("2d", 2 * 86400),
            ("1h30m", 90 * 60),
            ("1d2h", 26 * 3600),
            ("1d2h3m4s", 93784),
            (" 4h ", 4 * 3600),
        ],
    )
    def test_accepted(self, text, seconds):
        assert Duration.parse(text) == Duration(seconds)

    @pytest.mark.parametrize("text", ["90", "3600", "0"])
    def test_bare_integer_is_ambiguous(self, text):
        with pytest.raises(ValueError, match="ambiguous: use 90s or 90m"):
            Duration.parse(text)

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "4x",
            "30m1h",  # out of order
            "1h 30m",
            "1:60:00",
            "1:00:60",
            "10:60",
            "1-24:00",
            "1:2:3:4",
            "1-",
            "0s",
            "0:00:00",
            "4H",
        ],
    )
    def test_rejected(self, text):
        with pytest.raises(ValueError, match="invalid time"):
            Duration.parse(text)


class TestDurationRender:
    def test_sge_hours_unbounded(self):
        assert Duration.parse("4h").to_sge() == "4:00:00"
        assert Duration.parse("2d").to_sge() == "48:00:00"
        assert Duration.parse("90s").to_sge() == "0:01:30"

    def test_slurm(self):
        assert Duration.parse("4h").to_slurm() == "0-04:00:00"
        assert Duration.parse("1d2h3m4s").to_slurm() == "1-02:03:04"

    def test_pbs(self):
        assert Duration.parse("4h").to_pbs() == "04:00:00"
        assert Duration.parse("2d").to_pbs() == "48:00:00"

    def test_str(self):
        assert str(Duration.parse("4h")) == "4:00:00 (14400 s)"
