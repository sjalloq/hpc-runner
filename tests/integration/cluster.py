"""Live-cluster handle for the integration suite (docs/dev/testbed/TESTBED_PLAN.md section 4.2).

Runs inside the testbed's ``login`` container as user ``tester``: scheduler
CLIs are on PATH, ``sudo -u alice`` works passwordless, and node control goes
through ``sudo docker stop|start|kill <container>`` (docs/dev/testbed/TESTBED_REVIEW.md D-T9);
a container is named ``<compose project>-<service>``, see :meth:`TestbedCluster.container`.

One class per scheduler (``testbed/<scheduler>/``), registered in
:data:`CLUSTERS`; the fixture picks the class from ``--scheduler``. What every
scheduler shares (users, ``run``, ``wait_for``, node control, the ``ready.sh``/
``clean.sh`` contract, hpc-runner objects) is :class:`TestbedCluster`; a
scheduler's class names its probe command and says how to dump a job's state.

A test in ``tests/integration/`` itself runs on every scheduler, so it names
no scheduler command, flag or queue: it asks the cluster (the section "the
scheduler's own words" of :class:`Cluster`). :class:`SGECluster` and
:class:`SlurmCluster` answer each; a class that has not been given an answer
raises ``NotImplementedError`` naming the member.
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
from typing import ClassVar, Protocol

from hpc_runner.core.config import HPCConfig, load_config
from hpc_runner.core.host import Host
from hpc_runner.core.result import JobStatus
from hpc_runner.schedulers import get_scheduler
from hpc_runner.schedulers.base import BaseScheduler

#: Where the compose stack mounts the repository inside every container.
SRC_ROOT = Path("/src")
#: Shared scratch volume, mode 1777; the fixture makes one directory per test.
WORK_ROOT = Path("/work")
#: Each SGE flavour's accounting file (shared volume; truncating needs root).
#: OCS writes JSON lines to ``accounting.jsonl``; ``qacct`` reads either.
ACCOUNTING_FILES = {
    "soge": Path("/var/lib/gridengine/default/common/accounting"),
    "ocs": Path("/opt/ocs/default/common/accounting.jsonl"),
}
#: The stack's readiness probe (docs/dev/testbed/TESTBED_PLAN.md section 3); exit 0 when ready.
READY_SCRIPT = Path("/opt/testbed/ready.sh")
#: The stack's per-test cleanup: delete every job, wait for an empty queue, remove a workdir.
CLEAN_SCRIPT = Path("/opt/testbed/clean.sh")
READY_TIMEOUT = 90.0
READY_POLL = 2.0

#: How long :meth:`TestbedCluster.wait_accounted` waits. The SGE qmaster
#: flushes accounting within 1 s on the default stack; allow more.
ACCOUNTING_TIMEOUT = 30.0

#: The user the test process runs as; ``run()`` adds ``sudo -u`` for the others.
TEST_USER = "tester"
USERS: tuple[str, ...] = ("alice", "bob", "tester")


class ClusterError(AssertionError):
    """A cluster command failed or a wait timed out; the message has the evidence."""


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

    def pause_node(self, name: str) -> None: ...

    def unpause_node(self, name: str) -> None: ...

    def stop_master(self) -> None: ...

    def start_master(self) -> None: ...

    def accounting_truncate(self) -> None: ...

    def wait_ready(self, timeout: float = READY_TIMEOUT) -> None: ...

    def clean(self, workdir: Path | None = None) -> None: ...

    def config(self) -> HPCConfig: ...

    def scheduler(self) -> BaseScheduler: ...

    @property
    def exit_dir(self) -> Path: ...

    def host(self) -> Host: ...

    # -- the scheduler's own words, for the tests every scheduler runs --------

    @property
    def array_task_var(self) -> str:
        """The environment variable holding an array task's index inside the job."""
        ...

    def array_task_id(self, job_id: str, index: int) -> str:
        """The id of task *index* of array job *job_id*, as the scheduler writes it."""
        ...

    @property
    def hold_args(self) -> tuple[str, ...]:
        """Raw submit arguments that keep a job pending until :meth:`release_job`."""
        ...

    def release_job(self, job_id: str) -> None:
        """Let a job submitted with :attr:`hold_args` start."""
        ...

    def suspend_job(self, job_id: str) -> None: ...

    def resume_job(self, job_id: str) -> None: ...

    @property
    def default_queue(self) -> str:
        """The queue a job with no queue request runs in."""
        ...

    @property
    def other_queue(self) -> str:
        """A second queue; the testbed config's ``[tools.sort]`` entry names it."""
        ...

    @property
    def queue_slots(self) -> dict[str, int]:
        """Every queue and its slots: filling them all leaves nowhere for a job to start."""
        ...

    def wait_accounted(
        self, job_id: str, records: int = 1, timeout: float = ACCOUNTING_TIMEOUT
    ) -> None:
        """Wait until the scheduler's accounting has *records* records of *job_id*."""
        ...

    @property
    def submit_command(self) -> str:
        """The command hpc-runner submits a batch job with."""
        ...

    @property
    def listing_command(self) -> tuple[str, ...]:
        """How the argv of hpc-runner's active-jobs query begins."""
        ...

    @property
    def accounting_command(self) -> str:
        """The command hpc-runner reads a finished job with."""
        ...

    def submit_output(self, job_id: str) -> str:
        """Text the submit command prints when it accepts batch job *job_id*."""
        ...

    @property
    def directive_prefix(self) -> str:
        """What an option line of a batch script starts with."""
        ...

    @property
    def extra_output_lines(self) -> tuple[str, ...]:
        """Lines this cluster adds to every batch job's output file, none of them
        written by the job: one regular expression per line, in the order the
        lines appear. Empty when the cluster adds nothing."""
        ...


@dataclass
class TestbedCluster:
    """What every scheduler's testbed shares; subclasses set :attr:`name` and the hooks."""

    flavour: str
    workdir: Path
    #: The scheduler: the ``testbed/<name>/`` directory and ``get_scheduler`` name.
    name: str = ""
    users: tuple[str, ...] = USERS
    #: A CLI that is on PATH in ``login`` exactly when this scheduler's stack is.
    probe_command: ClassVar[str] = ""
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
                    f"{self._dump(job_id)}"
                )
            if time.monotonic() >= deadline:
                raise ClusterError(
                    f"job {job_id} is {status.name}, not {_names(wanted)}, after {timeout}s\n"
                    f"{self._dump(job_id)}"
                )
            time.sleep(poll)
            status = scheduler.get_status(job_id)
        return status

    def dump_commands(self, job_id: str) -> list[list[str]]:
        """The commands whose output explains *job_id*'s state in a failure message."""
        raise NotImplementedError

    def _dump(self, job_id: str) -> str:
        out = []
        for argv in self.dump_commands(job_id):
            proc = self.run(argv, check=False)
            header = f"--- {' '.join(argv)} (exit {proc.returncode}) ---"
            out.append(f"{header}\n{proc.stdout}{proc.stderr}")
        return "".join(out)

    # -- node control (docs/dev/testbed/TESTBED_REVIEW.md D-T9) ------------------------------

    @cached_property
    def docker_available(self) -> bool:
        """Whether the Docker socket is mounted (``sudo docker info`` succeeds)."""
        proc = self.run(["sudo", "docker", "info"], check=False)
        return proc.returncode == 0

    def container(self, service: str) -> str:
        """The Docker container of *service* (``exec1``): ``<compose project>-<service>``.

        The project is in ``HPC_TESTBED_PROJECT``, set on ``login`` by every
        stack's compose file; the prefix is what lets two stacks be up at once.
        """
        return f"{os.environ['HPC_TESTBED_PROJECT']}-{service}"

    def kill_node(self, name: str) -> None:
        self.run(["sudo", "docker", "kill", self.container(name)])

    def start_node(self, name: str) -> None:
        self.run(["sudo", "docker", "start", self.container(name)])

    def pause_node(self, name: str) -> None:
        self.run(["sudo", "docker", "pause", self.container(name)])

    def unpause_node(self, name: str) -> None:
        self.run(["sudo", "docker", "unpause", self.container(name)])

    def stop_master(self) -> None:
        self.run(["sudo", "docker", "stop", self.container("master")])

    def start_master(self) -> None:
        self.run(["sudo", "docker", "start", self.container("master")])

    def accounting_truncate(self) -> None:
        raise NotImplementedError

    # -- the ready.sh / clean.sh contract (testbed/README.md) ---------------------

    def poll_ready(
        self, timeout: float = READY_TIMEOUT, poll: float = READY_POLL
    ) -> tuple[bool, str]:
        """Run ``ready.sh`` until it passes or *timeout* elapses; return (ready, last output).

        A missing probe command or probe script means "no cluster" at once;
        only a present-but-failing probe is polled.
        """
        if shutil.which(self.probe_command) is None:
            return False, f"{self.probe_command} not on PATH"
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

    def wait_ready(self, timeout: float = READY_TIMEOUT) -> None:
        """Poll ``ready.sh`` until the stack is back (after a master or node restart).

        Raises:
            ClusterError: Not ready within *timeout*; the message is the probe's output.
        """
        ready, output = self.poll_ready(timeout)
        if not ready:
            raise ClusterError(f"cluster not ready after {timeout}s:\n{output}")

    def clean(self, workdir: Path | None = None) -> None:
        """Delete every job, wait for an empty queue, and remove *workdir* (``clean.sh``)."""
        argv = [str(CLEAN_SCRIPT)] + ([str(workdir)] if workdir else [])
        self.run(argv, timeout=120)

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
        env["HPC_RUNNER_CONFIG"] = str(self.config_path)
        env.setdefault("HPC_SCRIPT_DIR", str(WORK_ROOT / "cache" / "scripts"))
        env.setdefault("HPC_EXIT_DIR", str(WORK_ROOT / "cache" / "exit"))
        return Host(env=env, cwd=self.workdir, user=TEST_USER)

    # -- the scheduler's own words: each scheduler's class answers ------------

    def _unanswered(self, member: str) -> NotImplementedError:
        return NotImplementedError(f"{type(self).__name__}.{member}")

    @property
    def array_task_var(self) -> str:
        raise self._unanswered("array_task_var")

    def array_task_id(self, job_id: str, index: int) -> str:
        raise self._unanswered("array_task_id")

    @property
    def hold_args(self) -> tuple[str, ...]:
        raise self._unanswered("hold_args")

    def release_job(self, job_id: str) -> None:
        raise self._unanswered("release_job")

    def suspend_job(self, job_id: str) -> None:
        raise self._unanswered("suspend_job")

    def resume_job(self, job_id: str) -> None:
        raise self._unanswered("resume_job")

    @property
    def default_queue(self) -> str:
        raise self._unanswered("default_queue")

    @property
    def other_queue(self) -> str:
        raise self._unanswered("other_queue")

    @property
    def queue_slots(self) -> dict[str, int]:
        raise self._unanswered("queue_slots")

    def accounting_records(self, job_id: str) -> int:
        """How many records the scheduler's accounting has of *job_id* now."""
        raise self._unanswered("accounting_records")

    def wait_accounted(
        self, job_id: str, records: int = 1, timeout: float = ACCOUNTING_TIMEOUT
    ) -> None:
        """Poll :meth:`accounting_records` until it reaches *records*."""
        deadline = time.monotonic() + timeout
        while (seen := self.accounting_records(job_id)) < records:
            if time.monotonic() >= deadline:
                raise ClusterError(
                    f"accounting has {seen} of {records} record(s) of job {job_id} after {timeout}s"
                )
            time.sleep(0.5)

    @property
    def submit_command(self) -> str:
        raise self._unanswered("submit_command")

    @property
    def listing_command(self) -> tuple[str, ...]:
        raise self._unanswered("listing_command")

    @property
    def accounting_command(self) -> str:
        raise self._unanswered("accounting_command")

    def submit_output(self, job_id: str) -> str:
        raise self._unanswered("submit_output")

    @property
    def directive_prefix(self) -> str:
        raise self._unanswered("directive_prefix")

    @property
    def extra_output_lines(self) -> tuple[str, ...]:
        raise self._unanswered("extra_output_lines")


@dataclass
class SGECluster(TestbedCluster):
    """An SGE testbed of one *flavour* (``soge``, ...), driven from ``login``."""

    name: str = "sge"
    probe_command: ClassVar[str] = "qstat"

    def dump_commands(self, job_id: str) -> list[list[str]]:
        return [["qstat", "-f"], ["qstat", "-j", job_id]]

    def accounting_truncate(self) -> None:
        self.run(["sudo", "truncate", "-s", "0", str(ACCOUNTING_FILES[self.flavour])])

    # The names below match testbed/sge/<flavour>/cell/ (q-all.conf, q-short.conf).

    @property
    def array_task_var(self) -> str:
        return "SGE_TASK_ID"

    def array_task_id(self, job_id: str, index: int) -> str:
        return f"{job_id}.{index}"

    @property
    def hold_args(self) -> tuple[str, ...]:
        """A user hold: the job queues ``hqw``."""
        return ("-h",)

    def release_job(self, job_id: str) -> None:
        self.run(["qalter", "-h", "U", job_id])

    def suspend_job(self, job_id: str) -> None:
        self.run(["qmod", "-sj", job_id])

    def resume_job(self, job_id: str) -> None:
        self.run(["qmod", "-usj", job_id])

    @property
    def default_queue(self) -> str:
        return "all.q"

    @property
    def other_queue(self) -> str:
        return "short.q"

    @property
    def queue_slots(self) -> dict[str, int]:
        """``all.q`` is the only INTERACTIVE queue, but SGE schedules ``qrsh -now no
        <command>`` like a batch job, so it also runs in ``short.q`` (BATCH only)."""
        return {"all.q": 4, "short.q": 4}

    def accounting_records(self, job_id: str) -> int:
        """``qacct -j`` prints one ``jobnumber`` line per record (one per array task
        and per run of a rescheduled job)."""
        return self.run(["qacct", "-j", job_id], check=False).stdout.count("jobnumber")

    @property
    def submit_command(self) -> str:
        return "qsub"

    @property
    def listing_command(self) -> tuple[str, ...]:
        return ("qstat", "-xml")

    @property
    def accounting_command(self) -> str:
        return "qacct"

    def submit_output(self, job_id: str) -> str:
        return f"Your job {job_id}"

    @property
    def directive_prefix(self) -> str:
        return "#$"

    @property
    def extra_output_lines(self) -> tuple[str, ...]:
        return ()


#: ``sacct`` states of a job that has not ended; every other state is a record
#: of a finished run.
SACCT_LIVE_STATES = frozenset({"PENDING", "RUNNING", "SUSPENDED"})

#: What the 22.05 testbed's slurmstepd appends to every batch output file
#: (testbed/slurm/README.md, "Known output lines on 22.05").
SLURM_22_05_OUTPUT_LINES: tuple[str, ...] = (
    r"slurmstepd-\S+: error: common_file_write_uint32s: write pid \d+ to "
    r"/sys/fs/cgroup/cgroup\.procs failed: Device or resource busy",
    r"slurmstepd-\S+: error: Unable to move pid \d+ to init root cgroup /sys/fs/cgroup",
    r"slurmstepd-\S+: error: error unlocking cgroup "
    r"'/sys/fs/cgroup/system\.slice/\S+_slurmstepd\.scope' : Bad file descriptor",
)


def sacct_finished_rows(text: str) -> int:
    """How many rows of ``sacct -X -D -n -P --format=JobID,State`` are of a finished run.

    One row per array task and per run of a requeued job (``-D``); a row
    whose state is in :data:`SACCT_LIVE_STATES` is not counted. A state may
    carry more words (``CANCELLED by 1003``); the first decides.
    """
    count = 0
    for line in text.splitlines():
        _, _, state = line.partition("|")
        words = state.split()
        if words and words[0] not in SACCT_LIVE_STATES:
            count += 1
    return count


@dataclass
class SlurmCluster(TestbedCluster):
    """A Slurm testbed of one *flavour* (``24.11``, ...), driven from ``login``.

    ``accounting_truncate`` has no answer: the reason is in testbed/slurm/README.md.
    """

    name: str = "slurm"
    probe_command: ClassVar[str] = "sinfo"

    def dump_commands(self, job_id: str) -> list[list[str]]:
        return [["sinfo", "-N", "-l"], ["scontrol", "show", "job", job_id]]

    # The names below match testbed/slurm/common/conf/slurm.conf.

    @property
    def array_task_var(self) -> str:
        return "SLURM_ARRAY_TASK_ID"

    def array_task_id(self, job_id: str, index: int) -> str:
        return f"{job_id}_{index}"

    @property
    def hold_args(self) -> tuple[str, ...]:
        """A user hold: the job pends with reason ``JobHeldUser``."""
        return ("--hold",)

    def release_job(self, job_id: str) -> None:
        self.run(["scontrol", "release", job_id])

    def suspend_job(self, job_id: str) -> None:
        """As root: ``scontrol suspend`` is refused to the job's owner."""
        self.run(["scontrol", "suspend", job_id], user="root")

    def resume_job(self, job_id: str) -> None:
        self.run(["scontrol", "resume", job_id], user="root")

    @property
    def default_queue(self) -> str:
        return "all"

    @property
    def other_queue(self) -> str:
        return "short"

    @property
    def queue_slots(self) -> dict[str, int]:
        """Both partitions are the same two nodes of two CPUs each, so four
        one-CPU jobs in ``all`` leave nothing for ``short`` either."""
        return {"all": 4}

    def accounting_records(self, job_id: str) -> int:
        """Finished rows of ``sacct -X -D``: one per array task and per run of a
        requeued job (:func:`sacct_finished_rows`)."""
        argv = ["sacct", "-X", "-D", "-n", "-P", "-j", job_id, "--format=JobID,State"]
        return sacct_finished_rows(self.run(argv, check=False).stdout)

    @property
    def submit_command(self) -> str:
        return "sbatch"

    @property
    def listing_command(self) -> tuple[str, ...]:
        return ("squeue",)

    @property
    def accounting_command(self) -> str:
        return "sacct"

    def submit_output(self, job_id: str) -> str:
        return f"Submitted batch job {job_id}"

    @property
    def directive_prefix(self) -> str:
        return "#SBATCH"

    @property
    def extra_output_lines(self) -> tuple[str, ...]:
        return SLURM_22_05_OUTPUT_LINES if self.flavour == "22.05" else ()


@dataclass
class PBSCluster(TestbedCluster):
    """A PBS testbed of one *flavour* (``openpbs``), driven from ``login``."""

    name: str = "pbs"
    probe_command: ClassVar[str] = "pbsnodes"

    def dump_commands(self, job_id: str) -> list[list[str]]:
        return [["pbsnodes", "-a"], ["qstat", "-x", "-f", job_id]]


#: ``{scheduler: cluster class}``; a new scheduler adds its class here.
CLUSTERS: dict[str, type[TestbedCluster]] = {
    "sge": SGECluster,
    "slurm": SlurmCluster,
    "pbs": PBSCluster,
}


def _names(states: set[JobStatus]) -> str:
    return "/".join(sorted(s.name for s in states))
