"""Live-cluster handle for the integration suite (TESTBED_PLAN.md section 4.2).

Runs inside the testbed's ``login`` container as user ``tester``: scheduler
CLIs are on PATH, ``sudo -u alice`` works passwordless, and node control goes
through ``sudo docker stop|start|kill <container>`` (TESTBED_REVIEW.md D-T9).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Protocol

from hpc_runner.core.config import HPCConfig, load_config
from hpc_runner.core.host import Host
from hpc_runner.core.result import JobStatus
from hpc_runner.schedulers import get_scheduler
from hpc_runner.schedulers.base import BaseScheduler

#: Where the compose stack mounts the repository inside every container.
SRC_ROOT = Path("/src")
#: Shared scratch volume, mode 1777; the fixture makes one directory per test.
WORK_ROOT = Path("/work")
#: The qmaster's accounting file (shared volume; truncating needs root).
ACCOUNTING_FILE = Path("/var/lib/gridengine/default/common/accounting")
#: The stack's readiness probe (TESTBED_PLAN.md section 3); exit 0 when ready.
READY_SCRIPT = Path("/opt/testbed/ready.sh")
READY_TIMEOUT = 90.0
READY_POLL = 2.0

#: The user the test process runs as; ``run()`` adds ``sudo -u`` for the others.
TEST_USER = "tester"
USERS: tuple[str, ...] = ("alice", "bob", "tester")


class ClusterError(AssertionError):
    """A cluster command failed or a wait timed out; the message has the evidence."""


def poll_ready(timeout: float = READY_TIMEOUT, poll: float = READY_POLL) -> tuple[bool, str]:
    """Run ``ready.sh`` until it passes or *timeout* elapses; return (ready, last output).

    A missing ``qstat`` or probe script means "no cluster" at once; only a
    present-but-failing probe is polled.
    """
    if shutil.which("qstat") is None:
        return False, "qstat not on PATH"
    if not READY_SCRIPT.exists():
        return False, f"{READY_SCRIPT} not found"
    deadline = time.monotonic() + timeout
    while True:
        proc = subprocess.run([str(READY_SCRIPT)], capture_output=True, text=True, timeout=60)
        output = proc.stdout + proc.stderr
        if proc.returncode == 0:
            return True, output
        if time.monotonic() >= deadline:
            return False, output
        time.sleep(poll)


class Cluster(Protocol):
    """What a test may ask of the scheduler stack it runs against."""

    name: str
    flavour: str
    workdir: Path
    users: tuple[str, ...]

    @property
    def docker_available(self) -> bool:
        """Whether node control (``sudo docker ...``) works from login (D-T9)."""
        ...

    @property
    def hpc_cli(self) -> Path:
        """The ``hpc`` console script of the interpreter running the suite (D-T31)."""
        ...

    def run(
        self,
        argv: Sequence[str],
        *,
        user: str = TEST_USER,
        check: bool = True,
        timeout: float = 60,
    ) -> subprocess.CompletedProcess[str]: ...

    def wait_for(
        self,
        job_id: str,
        state: JobStatus | set[JobStatus],
        timeout: float = 60,
        poll: float = 0.5,
    ) -> JobStatus: ...

    def kill_node(self, name: str) -> None: ...

    def start_node(self, name: str) -> None: ...

    def stop_master(self) -> None: ...

    def start_master(self) -> None: ...

    def accounting_truncate(self) -> None: ...

    def wait_ready(self, timeout: float = READY_TIMEOUT) -> None: ...

    def config(self) -> HPCConfig: ...

    def scheduler(self) -> BaseScheduler: ...

    @property
    def exit_dir(self) -> Path: ...

    def host(self) -> Host: ...


@dataclass
class SGECluster:
    """An SGE testbed of one *flavour* (``soge``, ...), driven from ``login``."""

    flavour: str
    workdir: Path
    name: str = "sge"
    users: tuple[str, ...] = USERS
    _config: HPCConfig | None = field(default=None, init=False, repr=False)
    _scheduler: BaseScheduler | None = field(default=None, init=False, repr=False)

    @property
    def config_path(self) -> Path:
        return SRC_ROOT / "testbed" / self.name / self.flavour / "hpc-runner-testbed.toml"

    # -- commands ---------------------------------------------------------

    def run(
        self,
        argv: Sequence[str],
        *,
        user: str = TEST_USER,
        check: bool = True,
        timeout: float = 60,
    ) -> subprocess.CompletedProcess[str]:
        """Run *argv* as *user* (``sudo -u`` for anyone but ``tester``)."""
        cmd = list(argv)
        if user != TEST_USER:
            cmd = ["sudo", "-u", user, *cmd]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if check and proc.returncode != 0:
            raise ClusterError(
                f"{' '.join(cmd)} exited {proc.returncode}\n"
                f"--- stdout ---\n{proc.stdout}--- stderr ---\n{proc.stderr}"
            )
        return proc

    def wait_for(
        self,
        job_id: str,
        state: JobStatus | set[JobStatus],
        timeout: float = 60,
        poll: float = 0.5,
    ) -> JobStatus:
        """Poll ``get_status`` until it reports one of *state*; return that status.

        Fails at once when the job reaches a terminal status that is not
        wanted (waiting for COMPLETED on a job that FAILED should not burn
        the timeout), and on *timeout* otherwise; both messages carry the
        current ``qstat -f`` and ``qstat -j`` output.
        """
        wanted = {state} if isinstance(state, JobStatus) else state
        scheduler = self.scheduler()
        deadline = time.monotonic() + timeout
        status = scheduler.get_status(job_id)
        while status not in wanted:
            if status.is_terminal:
                raise ClusterError(
                    f"job {job_id} ended {status.name}, wanted {_names(wanted)}\n"
                    f"{self._qstat_dump(job_id)}"
                )
            if time.monotonic() >= deadline:
                raise ClusterError(
                    f"job {job_id} is {status.name}, not {_names(wanted)}, after {timeout}s\n"
                    f"{self._qstat_dump(job_id)}"
                )
            time.sleep(poll)
            status = scheduler.get_status(job_id)
        return status

    def _qstat_dump(self, job_id: str) -> str:
        out = []
        for argv in (["qstat", "-f"], ["qstat", "-j", job_id]):
            proc = self.run(argv, check=False)
            header = f"--- {' '.join(argv)} (exit {proc.returncode}) ---"
            out.append(f"{header}\n{proc.stdout}{proc.stderr}")
        return "".join(out)

    # -- node control (TESTBED_REVIEW.md D-T9) ------------------------------

    @cached_property
    def docker_available(self) -> bool:
        """Whether the Docker socket is mounted (``sudo docker info`` succeeds)."""
        proc = self.run(["sudo", "docker", "info"], check=False)
        return proc.returncode == 0

    def kill_node(self, name: str) -> None:
        self.run(["sudo", "docker", "kill", name])

    def start_node(self, name: str) -> None:
        self.run(["sudo", "docker", "start", name])

    def stop_master(self) -> None:
        self.run(["sudo", "docker", "stop", "master"])

    def start_master(self) -> None:
        self.run(["sudo", "docker", "start", "master"])

    def accounting_truncate(self) -> None:
        self.run(["sudo", "truncate", "-s", "0", str(ACCOUNTING_FILE)])

    def wait_ready(self, timeout: float = READY_TIMEOUT) -> None:
        """Poll ``ready.sh`` until the stack is back (after a master or node restart).

        Raises:
            ClusterError: Not ready within *timeout*; the message is the probe's output.
        """
        ready, output = poll_ready(timeout)
        if not ready:
            raise ClusterError(f"cluster not ready after {timeout}s:\n{output}")

    @property
    def hpc_cli(self) -> Path:
        """The ``hpc`` console script beside the running interpreter."""
        return Path(sys.executable).parent / "hpc"

    # -- hpc-runner objects ---------------------------------------------------

    def config(self) -> HPCConfig:
        if self._config is None:
            self._config = load_config(self.config_path)
        return self._config

    def scheduler(self) -> BaseScheduler:
        if self._scheduler is None:
            self._scheduler = get_scheduler(self.name, config=self.config())
        return self._scheduler

    @property
    def exit_dir(self) -> Path:
        """Where jobs leave their exit-code sentinels (``HPC_EXIT_DIR``)."""
        return Path(self.host().env["HPC_EXIT_DIR"])

    def host(self) -> Host:
        """The submitting host: this process's environment with the testbed's HPC_* set."""
        env = dict(os.environ)
        env["HPC_SCHEDULER"] = self.name
        env["HPC_RUNNER_CONFIG"] = str(self.config_path)
        env.setdefault("HPC_SCRIPT_DIR", str(WORK_ROOT / "cache" / "scripts"))
        env.setdefault("HPC_EXIT_DIR", str(WORK_ROOT / "cache" / "exit"))
        return Host(env=env, cwd=self.workdir, user=TEST_USER)


def _names(states: set[JobStatus]) -> str:
    return "/".join(sorted(s.name for s in states))
