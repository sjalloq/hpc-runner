"""Tests for scheduler detection."""

import os
from unittest.mock import patch

from hpc_runner.core.exceptions import SchedulerUnavailableError
from hpc_runner.schedulers.detection import detect_scheduler
from tests.fakes import patch_seam


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


def _only_qsub(cmd):
    return "/usr/bin/qsub" if cmd == "qsub" else None


class TestPBSDetection:
    """PBS: PBS_CONF_FILE, or `qsub --version` (through the seam) printing pbs_version."""

    def test_pbs_conf_file(self, clean_env):
        os.environ["PBS_CONF_FILE"] = "/etc/pbs.conf"
        with patch("shutil.which", side_effect=_only_qsub), patch_seam(lambda argv: (1, "", "")):
            assert detect_scheduler() == "pbs"

    def test_qsub_version_pbs(self, clean_env):
        def respond(argv):
            if argv == ["qsub", "--version"]:
                return 0, "pbs_version = 22.05.11\n", ""
            return 1, "", ""  # qstat -help: not SGE

        with patch("shutil.which", side_effect=_only_qsub), patch_seam(respond) as seam:
            assert detect_scheduler() == "pbs"
        assert ["qsub", "--version"] in [c.args[0] for c in seam.call_args_list]

    def test_qsub_version_not_pbs(self, clean_env):
        def respond(argv):
            return 0, "Torque 6.1\n", ""

        with patch("shutil.which", side_effect=_only_qsub), patch_seam(respond):
            assert detect_scheduler() == "local"

    def test_qsub_version_missing_binary(self, clean_env):
        def respond(argv):
            raise SchedulerUnavailableError(f"{argv[0]}: not found")

        with patch("shutil.which", side_effect=_only_qsub), patch_seam(respond):
            assert detect_scheduler() == "local"
