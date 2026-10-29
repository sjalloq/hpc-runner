"""Tests for scheduler detection."""

import os
from unittest.mock import patch

import pytest

from hpc_runner.schedulers.detection import detect_scheduler


class TestSchedulerDetection:
    """Tests for scheduler auto-detection."""

    def test_env_override(self, clean_env):
        """Test HPC_SCHEDULER environment variable override."""
        os.environ["HPC_SCHEDULER"] = "sge"
        assert detect_scheduler() == "sge"

    def test_env_override_case_insensitive(self, clean_env):
        """Test that env var is case-insensitive."""
        os.environ["HPC_SCHEDULER"] = "SGE"
        assert detect_scheduler() == "sge"

    def test_detect_sge(self, clean_env):
        """Test SGE detection."""
        os.environ["SGE_ROOT"] = "/opt/sge"

        with patch("shutil.which") as mock_which:
            mock_which.side_effect = lambda cmd: "/usr/bin/qsub" if cmd == "qsub" else None
            assert detect_scheduler() == "sge"

    def test_detect_slurm(self, clean_env):
        """Test Slurm detection."""
        with patch("shutil.which") as mock_which:

            def which_side_effect(cmd):
                if cmd in ("sbatch", "squeue"):
                    return f"/usr/bin/{cmd}"
                return None

            mock_which.side_effect = which_side_effect
            assert detect_scheduler() == "slurm"

    def test_fallback_to_local(self, clean_env):
        """Test fallback to local scheduler."""
        with patch("shutil.which") as mock_which:
            mock_which.return_value = None
            assert detect_scheduler() == "local"

    def test_sge_takes_precedence_over_slurm(self, clean_env):
        """Test that SGE detection comes before Slurm."""
        os.environ["SGE_ROOT"] = "/opt/sge"

        with patch("shutil.which") as mock_which:
            mock_which.return_value = "/usr/bin/cmd"  # All commands exist
            assert detect_scheduler() == "sge"
