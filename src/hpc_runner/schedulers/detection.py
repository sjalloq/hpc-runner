"""Auto-detection of available scheduler."""

import os
import shutil

from hpc_runner.core.exceptions import SchedulerError
from hpc_runner.schedulers import _subprocess


def _check_sge_via_qstat() -> bool:
    """Check if qstat is SGE by examining its help output."""
    try:
        result = _subprocess.run_command(["qstat", "-help"], timeout=5, check=False)
    except (SchedulerError, OSError):
        return False
    # SGE's qstat -help starts with "SGE" or "GE" version info
    output = (result.stdout or "") + (result.stderr or "")
    return "SGE" in output or "Grid Engine" in output


def detect_scheduler() -> str:
    """Auto-detect available scheduler.

    Order of precedence:
    1. HPC_SCHEDULER environment variable
    2. SGE (check for SGE_ROOT or qstat -help output)
    3. Slurm (check for sbatch)
    4. PBS (check for qsub with PBS_CONF_FILE)
    5. Local fallback
    """
    # Environment override
    if scheduler := os.environ.get("HPC_SCHEDULER"):
        return scheduler.lower()

    # Check for SGE (via SGE_ROOT or qstat help output)
    if shutil.which("qsub"):
        if os.environ.get("SGE_ROOT") or _check_sge_via_qstat():
            return "sge"

    # Check for Slurm
    if shutil.which("sbatch") and shutil.which("squeue"):
        return "slurm"

    # Check for PBS/Torque
    if shutil.which("qsub") and os.environ.get("PBS_CONF_FILE"):
        return "pbs"

    # Fallback to local
    return "local"
