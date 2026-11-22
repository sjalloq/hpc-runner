"""The Slurm backend: what it renders and how it submits.

What ``sbatch`` prints comes from the captures in
tests/unit/fixtures/slurm/<flavour>/; every test that reads one runs once
per flavour.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from hpc_runner.core.config import HPCConfig, load_config
from hpc_runner.core.dependency import Dependency, DependencyType
from hpc_runner.core.exceptions import ConfigError, NotSupportedError, SubmissionError
from hpc_runner.core.result import ArrayJobResult
from hpc_runner.schedulers import get_scheduler, scheduler_config_keys
from hpc_runner.schedulers.base import BaseScheduler
from hpc_runner.schedulers.slurm import SlurmScheduler
from hpc_runner.schedulers.slurm.parser import parse_sbatch_output
from tests.fakes import FIXTURES_ROOT, SEAM, make_host, make_job, patch_seam

FLAVOURS = ("22.05", "24.11")


def _captured(flavour: str, name: str) -> dict[str, Any]:
    captured: dict[str, Any] = json.loads((FIXTURES_ROOT / "slurm" / flavour / name).read_text())
    return captured


def _answer(flavour: str, name: str) -> tuple[int, str, str]:
    captured = _captured(flavour, name)
    return captured["returncode"], captured["stdout"], captured["stderr"]


def _job_id(flavour: str, scenario: str) -> str:
    job_id: str = _captured(flavour, "manifest.json")["jobs"][scenario]
    return job_id


def _directives(script: str) -> list[str]:
    return [line for line in script.splitlines() if line.startswith("#SBATCH ")]


@pytest.fixture(params=FLAVOURS)
def flavour(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
def scheduler(monkeypatch, tmp_path) -> SlurmScheduler:
    monkeypatch.setenv("HPC_SCRIPT_DIR", str(tmp_path / "scripts"))
    return SlurmScheduler(HPCConfig())


def _slurm(**settings: Any) -> SlurmScheduler:
    return SlurmScheduler(HPCConfig(schedulers={"slurm": settings}))


class TestRegistry:
    def test_named_in_config(self, monkeypatch):
        monkeypatch.delenv("HPC_SCHEDULER", raising=False)
        assert isinstance(get_scheduler(config=HPCConfig(scheduler="slurm")), SlurmScheduler)

    def test_named_in_the_environment(self, monkeypatch):
        monkeypatch.setenv("HPC_SCHEDULER", "slurm")
        assert get_scheduler(config=HPCConfig(scheduler="local")).name == "slurm"


class TestConfigKeys:
    def test_the_keys_and_their_types(self):
        assert scheduler_config_keys("slurm") == {
            "memory_per": str,
            "kill_invalid_dependents": bool,
        }

    def test_defaults(self):
        scheduler = SlurmScheduler(HPCConfig())
        assert scheduler.memory_per == "job"
        assert scheduler.kill_invalid_dependents is True

    def test_section_is_validated(self, tmp_path):
        path = tmp_path / "hpc-runner.toml"
        path.write_text(
            "config_version = 1\n[schedulers.slurm]\n"
            'memory_per = "cpu"\nkill_invalid_dependents = false\n'
        )
        config = load_config(path)
        assert config.warnings == []
        assert config.schedulers["slurm"] == {
            "memory_per": "cpu",
            "kill_invalid_dependents": False,
        }

    @pytest.mark.parametrize(
        ("line", "message"),
        [
            (
                'parallel_environment = "smp"',
                "[schedulers.slurm] parallel_environment: unknown key",
            ),
            (
                'kill_invalid_dependents = "yes"',
                "[schedulers.slurm] kill_invalid_dependents: expected bool, got",
            ),
        ],
    )
    def test_bad_key_is_an_error(self, tmp_path, line, message):
        path = tmp_path / "hpc-runner.toml"
        path.write_text(f"config_version = 1\n[schedulers.slurm]\n{line}\n")
        with pytest.raises(ConfigError) as err:
            load_config(path)
        assert message in str(err.value)

    def test_bad_memory_per(self):
        with pytest.raises(ConfigError) as err:
            _slurm(memory_per="slot")
        assert str(err.value) == (
            "[schedulers.slurm] memory_per: expected one of 'job', 'cpu', got 'slot'"
        )


class TestBatchScript:
    def test_a_job_with_nothing_set(self, scheduler):
        script = scheduler.preview(make_job())
        assert script.startswith("#!/bin/bash\n")
        assert _directives(script) == [
            "#SBATCH --chdir=/work",
            "#SBATCH --export=ALL",
            "#SBATCH --job-name=alice_echo",
            "#SBATCH --cpus-per-task=1 --ntasks=1",
            "#SBATCH --output=/work/%x.%j.out",
        ]

    def test_every_option_in_order(self, scheduler):
        job = make_job(
            name="sim",
            cpu=4,
            mem="16G",
            time="4h",
            queue="short",
            project="proj",
            stderr="sim.err",
            array="1-10:2%3",
            dependencies=[Dependency("7")],
            licenses={"lic": 1},
            devices={"gpu": 2},
            features=["fast"],
            scheduler_args=["--nice=5", "-w", "exec1"],
        )
        assert _directives(scheduler.preview(job)) == [
            "#SBATCH --chdir=/work",
            "#SBATCH --export=ALL",
            "#SBATCH --job-name=sim",
            "#SBATCH --cpus-per-task=4 --ntasks=1",
            "#SBATCH --mem=16384",
            "#SBATCH --time=0-04:00:00",
            "#SBATCH --partition=short",
            "#SBATCH --account=proj",
            "#SBATCH --output=/work/%x.%A.%a.out",
            "#SBATCH --error=/work/sim.%a.err",
            "#SBATCH --array=1-10:2%3",
            "#SBATCH --dependency=afterok:7 --kill-on-invalid-dep=yes",
            "#SBATCH -L lic:1",
            "#SBATCH --gres=gpu:2",
            "#SBATCH -C fast",
            "#SBATCH --nice=5 -w exec1",
        ]

    def test_srun_variables_follow_the_environment_block(self, scheduler):
        lines = scheduler.preview(make_job(env_vars={"A": "1"})).splitlines()
        end = lines.index("# --- end hpc-runner environment ---")
        cpus = lines.index("export SRUN_CPUS_PER_TASK=$SLURM_CPUS_PER_TASK")
        export = lines.index("export SLURM_EXPORT_ENV=ALL")
        assert lines.index("export A=1") < end < cpus < export < lines.index("echo hello")

    def test_the_command_runs_last_and_its_exit_code_is_the_scripts(self, scheduler):
        assert scheduler.preview(make_job("make sim")).endswith("\nmake sim\nexit $?\n")


class TestCpu:
    def test_always_sent_and_1_when_unset(self, scheduler):
        assert "#SBATCH --cpus-per-task=1 --ntasks=1" in _directives(scheduler.preview(make_job()))

    def test_the_count_asked_for(self, scheduler):
        directives = _directives(scheduler.preview(make_job(cpu=8)))
        assert "#SBATCH --cpus-per-task=8 --ntasks=1" in directives

    def test_srun_gets_both_words(self, scheduler):
        argv = scheduler._build_srun_command(make_job(cpu=8, interactive=True), "/s.sh")
        assert argv[argv.index("--cpus-per-task=8") + 1] == "--ntasks=1"


class TestMemory:
    """``mem`` is per job, in whole megabytes rounded up; never 0."""

    @pytest.mark.parametrize(
        ("mem", "directive"),
        [
            ("16G", "#SBATCH --mem=16384"),
            ("1.5G", "#SBATCH --mem=1536"),
            ("300M", "#SBATCH --mem=300"),
            ("1025K", "#SBATCH --mem=2"),  # rounded up to the megabyte
            ("1K", "#SBATCH --mem=1"),  # never 0
            ("1", "#SBATCH --mem=1"),  # one byte
        ],
    )
    def test_per_job(self, scheduler, mem, directive):
        directives = _directives(scheduler.preview(make_job(cpu=4, mem=mem)))
        assert directive in directives
        assert not any("--mem-per-cpu" in d for d in directives)

    @pytest.mark.parametrize(
        ("cpu", "mem", "directive"),
        [
            (4, "16G", "#SBATCH --mem-per-cpu=4096"),
            (3, "1G", "#SBATCH --mem-per-cpu=342"),  # 341.33, rounded up
            (2, "301M", "#SBATCH --mem-per-cpu=151"),
            (None, "16G", "#SBATCH --mem-per-cpu=16384"),  # one CPU when cpu is unset
            (4, "1K", "#SBATCH --mem-per-cpu=1"),  # never 0
        ],
    )
    def test_per_cpu_divides_and_rounds_up(self, cpu, mem, directive):
        directives = _directives(_slurm(memory_per="cpu").preview(make_job(cpu=cpu, mem=mem)))
        assert directive in directives
        assert not any(d.startswith("#SBATCH --mem=") for d in directives)

    def test_no_mem_no_option(self, scheduler):
        assert "--mem" not in scheduler.preview(make_job())

    def test_srun_gets_the_divided_amount(self):
        job = make_job(cpu=2, mem="8G", interactive=True)
        argv = _slurm(memory_per="cpu")._build_srun_command(job, "/s.sh")
        assert "--mem-per-cpu=4096" in argv


class TestTime:
    @pytest.mark.parametrize(
        ("time", "directive"),
        [
            ("90s", "#SBATCH --time=0-00:01:30"),
            ("4h", "#SBATCH --time=0-04:00:00"),
            ("23:59:59", "#SBATCH --time=0-23:59:59"),
            ("24h", "#SBATCH --time=1-00:00:00"),  # the day boundary
            ("36:00:00", "#SBATCH --time=1-12:00:00"),
            ("1d2h3m4s", "#SBATCH --time=1-02:03:04"),
            ("10-00:00:00", "#SBATCH --time=10-00:00:00"),
        ],
    )
    def test_days_hours_minutes_seconds(self, scheduler, time, directive):
        assert directive in _directives(scheduler.preview(make_job(time=time)))


class TestExport:
    def _job(self, kind: str = "batch", **env: str) -> Any:
        return make_job(
            inherit_env=False,
            env_passthrough=list(env),
            host=make_host(env=env),
            interactive=kind == "interactive",
        )

    def test_inherit_env_is_all(self, scheduler):
        assert "#SBATCH --export=ALL" in _directives(scheduler.preview(make_job()))

    def test_no_inherit_env_is_none(self, scheduler):
        script = scheduler.preview(make_job(inherit_env=False))
        assert "#SBATCH --export=NONE" in _directives(script)

    def test_passthrough_is_one_list_of_name_and_value(self, scheduler):
        job = self._job(DISPLAY=":0", LANG="C.UTF-8", EMPTY="")
        directives = _directives(scheduler.preview(job))
        assert "#SBATCH --export=DISPLAY=:0,LANG=C.UTF-8,EMPTY=" in directives

    def test_passthrough_is_ignored_with_inherit_env(self, scheduler):
        job = make_job(env_passthrough=["DISPLAY"], host=make_host(env={"DISPLAY": ":0"}))
        assert "#SBATCH --export=ALL" in _directives(scheduler.preview(job))

    def test_srun_gets_the_same_value(self, scheduler):
        argv = scheduler._build_srun_command(self._job("interactive", DISPLAY=":0"), "/s.sh")
        assert "--export=DISPLAY=:0" in argv

    @pytest.mark.parametrize("kind", ["batch", "interactive"])
    def test_a_comma_in_a_value_is_an_error(self, scheduler, kind):
        job = self._job(kind, DISPLAY=":0", PATHS="a,b")
        message = (
            "env_passthrough: the value of PATHS contains ',' ('a,b'); "
            "--export=NAME=value,... separates variables with it"
        )
        with pytest.raises(SubmissionError) as err:
            scheduler.preview(job)
        assert str(err.value) == message
        with patch_seam(lambda argv: (0, "", "")) as seam, pytest.raises(SubmissionError):
            scheduler.submit(job)
        assert not seam.called


class TestDependencies:
    DEPENDENCIES = [
        Dependency("1"),
        Dependency("3", DependencyType.AFTERANY),
        Dependency("2", DependencyType.AFTEROK),
    ]

    def test_parents_of_one_type_share_an_element(self, scheduler):
        directives = _directives(scheduler.preview(make_job(dependencies=self.DEPENDENCIES)))
        assert "#SBATCH --dependency=afterok:1:2,afterany:3 --kill-on-invalid-dep=yes" in directives

    def test_without_the_key_the_dependent_is_not_killed(self):
        script = _slurm(kill_invalid_dependents=False).preview(
            make_job(dependencies=self.DEPENDENCIES)
        )
        assert "#SBATCH --dependency=afterok:1:2,afterany:3" in _directives(script)
        assert "--kill-on-invalid-dep" not in script

    def test_every_type_goes_under_its_own_name(self, scheduler):
        dependencies = [Dependency(str(n), kind) for n, kind in enumerate(DependencyType, 1)]
        directives = _directives(scheduler.preview(make_job(dependencies=dependencies)))
        assert (
            "#SBATCH --dependency=afterok:1,afterany:2,after:3,afternotok:4 "
            "--kill-on-invalid-dep=yes"
        ) in directives

    def test_an_array_parent_is_named_by_its_job_id(self, scheduler):
        job = make_job(dependencies=[Dependency("9", DependencyType.AFTEROK, (1, 2, 3))])
        assert "#SBATCH --dependency=afterok:9 --kill-on-invalid-dep=yes" in _directives(
            scheduler.preview(job)
        )

    def test_the_same_parent_twice_is_named_once(self, scheduler):
        job = make_job(dependencies=[Dependency("9"), Dependency("9")])
        assert "#SBATCH --dependency=afterok:9 --kill-on-invalid-dep=yes" in _directives(
            scheduler.preview(job)
        )

    def test_no_dependencies_no_option(self, scheduler):
        script = scheduler.preview(make_job())
        assert "--dependency" not in script and "--kill-on-invalid-dep" not in script

    def test_an_interactive_job_refuses_them(self, scheduler):
        job = make_job(interactive=True, dependencies=[Dependency("9")])
        with pytest.raises(NotSupportedError, match="interactive jobs cannot have dependencies"):
            scheduler.preview(job)
        with patch_seam(lambda argv: (0, "", "")) as seam, pytest.raises(NotSupportedError):
            scheduler.submit(job)
        assert not seam.called


class TestArray:
    def test_range_step_and_throttle(self, scheduler):
        assert "#SBATCH --array=1-10:2%3" in _directives(
            scheduler.preview(make_job(array="1-10:2%3"))
        )

    def test_without_a_throttle(self, scheduler):
        directives = _directives(scheduler.preview(make_job(array="1-4")))
        assert "#SBATCH --array=1-4" in directives

    def test_one_task(self, scheduler):
        assert "#SBATCH --array=7-7" in _directives(scheduler.preview(make_job(array="7")))

    def test_a_task_is_named_base_underscore_index(self, scheduler):
        assert scheduler.task_job_id("95", 3) == "95_3"


class TestRequests:
    """Licences, devices and features: several of a kind are one option."""

    FIELDS = {
        "licenses": {"xcelium": 1, "vcs": 2},
        "devices": {"gpu": 2, "fpga": 1},
        "features": ["bigmem", "avx2"],
    }

    def test_batch_directives(self, scheduler):
        directives = _directives(scheduler.preview(make_job(**self.FIELDS)))
        assert directives[-3:] == [
            "#SBATCH -L xcelium:1,vcs:2",
            "#SBATCH --gres=gpu:2,fpga:1",
            "#SBATCH -C bigmem&avx2",
        ]

    def test_requests_come_before_scheduler_args(self, scheduler):
        job = make_job(licenses={"xcelium": 1}, scheduler_args=["-L", "other:1"])
        assert _directives(scheduler.preview(job))[-2:] == [
            "#SBATCH -L xcelium:1",
            "#SBATCH -L other:1",
        ]

    def test_srun_carries_all_three(self, scheduler):
        job = make_job(interactive=True, scheduler_args=["--x11"], **self.FIELDS)
        argv = scheduler._build_srun_command(job, "/s.sh")
        assert argv[-7:] == [
            "-L",
            "xcelium:1,vcs:2",
            "--gres=gpu:2,fpga:1",
            "-C",
            "bigmem&avx2",
            "--x11",
            "/s.sh",
        ]

    def test_no_requests_no_options(self, scheduler):
        script = scheduler.preview(make_job())
        assert not any(flag in script for flag in ("-L ", "--gres", "-C "))


class TestHpcVars:
    """``${hpc.*}`` in the script and in an output path."""

    def test_in_the_script(self, scheduler):
        job = make_job(
            env_vars={
                "CPUS": "${hpc.cpus}",
                "ID": "${hpc.job_id}",
                "TASK": "${hpc.task_id}",
                "NAME": "${hpc.job_name}",
                "TMP": "${hpc.tmpdir}/scratch",
                "HOST": "${hpc.host}",
                "TAG": "run-${hpc.job_id}-${hpc.task_id}.log",
            }
        )
        lines = scheduler.preview(job).splitlines()
        for line in (
            'export CPUS="$SLURM_CPUS_PER_TASK"',
            'export ID="${SLURM_ARRAY_JOB_ID:-$SLURM_JOB_ID}"',
            'export TASK="$SLURM_ARRAY_TASK_ID"',
            'export NAME="$SLURM_JOB_NAME"',
            'export TMP="$TMPDIR"/scratch',
            'export HOST="$HOSTNAME"',
            'export TAG=run-"${SLURM_ARRAY_JOB_ID:-$SLURM_JOB_ID}"-"$SLURM_ARRAY_TASK_ID".log',
        ):
            assert line in lines

    def test_in_the_interactive_script(self, scheduler):
        job = make_job(interactive=True, env_vars={"MAKEFLAGS": "-j${hpc.cpus}"})
        assert 'export MAKEFLAGS=-j"$SLURM_CPUS_PER_TASK"' in scheduler.preview(job).splitlines()

    def test_the_default_output_file(self, scheduler):
        assert "#SBATCH --output=/work/%x.%j.out" in _directives(scheduler.preview(make_job()))

    def test_the_default_output_file_of_an_array(self, scheduler):
        directives = _directives(scheduler.preview(make_job(array="1-4")))
        assert "#SBATCH --output=/work/%x.%A.%a.out" in directives

    @pytest.mark.parametrize(
        ("pattern", "array", "rendered"),
        [
            ("${hpc.job_name}.${hpc.job_id}.log", None, "%x.%j.log"),
            ("${hpc.job_name}.${hpc.job_id}.log", "1-4", "%x.%A.%a.log"),
            ("t/${hpc.job_id}.${hpc.task_id}", "1-4", "t/%A.%a"),
            ("${hpc.host}.out", None, "%N.out"),
            ("run.log", "1-4", "run.%a.log"),  # before the last extension
            ("run.tar.gz", "1-4", "run.tar.%a.gz"),
            ("run", "1-4", "run.%a"),  # no extension: appended
            ("${hpc.job_name}.${hpc.job_id}", "1-4", "%x.%A.%a"),  # a reference is not one
            ("logs.d/run", "1-4", "logs.d/run.%a"),  # nor is a directory's dot
        ],
    )
    def test_in_an_output_path(self, scheduler, pattern, array, rendered):
        directives = _directives(scheduler.preview(make_job(stdout=pattern, array=array)))
        assert f"#SBATCH --output=/work/{rendered}" in directives

    def test_stderr_only_when_set(self, scheduler):
        assert "--error" not in scheduler.preview(make_job())
        directives = _directives(scheduler.preview(make_job(stderr="${hpc.job_name}.err")))
        assert "#SBATCH --error=/work/%x.err" in directives

    def test_array_stderr_gets_the_task_id_too(self, scheduler):
        job = make_job(array="1-4", stderr="${hpc.job_name}.err")
        assert "#SBATCH --error=/work/%x.%a.err" in _directives(scheduler.preview(job))

    def test_relative_pattern_is_joined_onto_workdir(self, scheduler):
        job = make_job(workdir="/proj/run", stdout="o/${hpc.job_id}.out")
        directives = _directives(scheduler.preview(job))
        assert directives[0] == "#SBATCH --chdir=/proj/run"
        assert "#SBATCH --output=/proj/run/o/%j.out" in directives

    def test_absolute_pattern_kept(self, scheduler):
        job = make_job(stdout="/logs/${hpc.job_id}.out")
        assert "#SBATCH --output=/logs/%j.out" in _directives(scheduler.preview(job))


class TestJobName:
    @pytest.mark.parametrize(
        ("name", "rendered"),
        [
            ("alice_sim", "alice_sim"),
            ("9lives", "9lives"),
            ("my job", "my_job"),
            ("a/b", "a_b"),
            ("it's", "it_s"),
            ('say"hi"', "say_hi_"),
            ("back\\slash", "back_slash"),
            ("a#b", "a_b"),
            ("a:b@c", "a:b@c"),
        ],
    )
    def test_sanitised(self, scheduler, name, rendered):
        script = scheduler.preview(make_job(name=name))
        assert f"#SBATCH --job-name={rendered}" in _directives(script)


class TestDirectiveValues:
    """A value a ``#SBATCH`` line would not carry as written is refused before sbatch."""

    PREFIX = (
        "Slurm cannot take a value with whitespace, a quote, a backslash or '#' "
        "in a job script directive (#SBATCH lines are split on spaces and do not "
        "keep those characters): "
    )

    def test_workdir_with_space_is_refused(self, scheduler):
        job = make_job("pwd", workdir="/data/my run")
        with pytest.raises(SubmissionError, match="whitespace") as err:
            scheduler.preview(job)
        assert str(err.value) == (
            self.PREFIX + "workdir = '/data/my run'; stdout = '/data/my run/%x.%j.out'"
        )
        with patch_seam(lambda argv: (0, "", "")) as seam, pytest.raises(SubmissionError):
            scheduler.submit(job)
        assert not seam.called

    @pytest.mark.parametrize(
        ("fields", "offending"),
        [
            ({"queue": "a b"}, "queue = 'a b'"),
            ({"project": "a\tb"}, "project = 'a\\tb'"),
            ({"stdout": "my log.out"}, "stdout = '/work/my log.out'"),
            ({"stderr": "it's.err"}, 'stderr = "/work/it\'s.err"'),
            ({"stdout": 'a"b.out'}, "stdout = '/work/a\"b.out'"),
            ({"stdout": "a\\b.out"}, "stdout = '/work/a\\\\b.out'"),
            ({"stdout": "a#b.out"}, "stdout = '/work/a#b.out'"),
            ({"licenses": {"a b": 1}}, "licenses name 'a b'"),
            ({"devices": {"a b": 1}}, "devices name 'a b'"),
            ({"features": ["a b"]}, "features name 'a b'"),
        ],
    )
    def test_a_value_is_refused(self, scheduler, fields, offending):
        with pytest.raises(SubmissionError) as err:
            scheduler.preview(make_job(**fields))
        assert str(err.value) == self.PREFIX + offending

    def test_a_passed_through_value_is_refused(self, scheduler):
        job = make_job(
            inherit_env=False, env_passthrough=["TITLE"], host=make_host(env={"TITLE": "my run"})
        )
        with pytest.raises(SubmissionError) as err:
            scheduler.preview(job)
        assert str(err.value) == self.PREFIX + "env_passthrough TITLE = 'my run'"

    def test_srun_takes_the_value_as_one_argument(self, scheduler):
        job = make_job(
            interactive=True,
            workdir="/data/my run",
            queue="a b",
            inherit_env=False,
            env_passthrough=["TITLE"],
            host=make_host(env={"TITLE": "my run"}),
        )
        argv = scheduler._build_srun_command(job, "/s.sh")
        assert argv[1:3] == ["--chdir=/data/my run", "--export=TITLE=my run"]
        assert "--partition=a b" in argv

    def test_name_with_space_is_sanitised_not_refused(self, scheduler):
        directives = _directives(scheduler.preview(make_job(name="9 lives")))
        assert "#SBATCH --job-name=9_lives" in directives


class TestSubmitBatch:
    def test_the_job_id_is_read_from_sbatchs_line(self, flavour):
        captured = _captured(flavour, "sbatch_ok.json")
        assert captured["stdout"] == f"Submitted batch job {_job_id(flavour, 'ok')}\n"
        assert parse_sbatch_output(captured["stdout"]) == _job_id(flavour, "ok")
        array = _captured(flavour, "sbatch_array.json")
        assert parse_sbatch_output(array["stdout"]) == _job_id(flavour, "array")

    def test_no_job_id_in_other_text(self):
        assert parse_sbatch_output("") is None
        assert parse_sbatch_output("sbatch: error: Batch job submission failed\n") is None

    def test_sbatch_is_given_the_script_and_the_result_the_id(self, scheduler, flavour, tmp_path):
        seen: list[tuple[list[str], str]] = []

        def respond(argv):
            seen.append((argv, Path(argv[-1]).read_text()))
            return _answer(flavour, "sbatch_ok.json")

        job = make_job("echo hi", name="n", workdir=str(tmp_path), stderr="e/${hpc.job_id}")
        with patch_seam(respond):
            result = scheduler.submit(job)
        ((argv, script),) = seen
        assert argv == ["sbatch", str(tmp_path / "scripts" / Path(argv[1]).name)]
        assert Path(argv[1]).name.startswith("hpc_batch_") and argv[1].endswith(".sh")
        assert script == scheduler.preview(job)
        job_id = _job_id(flavour, "ok")
        assert not isinstance(result, ArrayJobResult)
        assert result.job_id == job_id
        assert result.job is job
        assert result.stdout_path == tmp_path / f"n.{job_id}.out"
        assert result.stderr_path == tmp_path / "e" / job_id

    def test_an_array_result_names_its_tasks(self, scheduler, flavour, tmp_path):
        job = make_job(name="a", array="1-4%2", workdir=str(tmp_path))
        with patch_seam(lambda argv: _answer(flavour, "sbatch_array.json")):
            result = scheduler.submit(job)
        job_id = _job_id(flavour, "array")
        assert isinstance(result, ArrayJobResult)
        assert result.job_id == job_id
        assert result.array.count == 4
        assert [result.task_id(n) for n in result.array.indices] == [
            f"{job_id}_1",
            f"{job_id}_2",
            f"{job_id}_3",
            f"{job_id}_4",
        ]
        assert result.task_stdout_path(3) == tmp_path / f"a.{job_id}.3.out"
        assert result.task_stderr_path(3) is None

    def test_the_script_is_removed_unless_kept(self, scheduler, flavour, tmp_path):
        scripts = tmp_path / "scripts"
        with patch_seam(lambda argv: _answer(flavour, "sbatch_ok.json")):
            scheduler.submit(make_job(workdir=str(tmp_path)))
            assert list(scripts.iterdir()) == []
            scheduler.submit(make_job(workdir=str(tmp_path)), keep_script=True)
        (kept,) = scripts.iterdir()
        assert kept.read_text() == scheduler.preview(make_job(workdir=str(tmp_path)))

    def test_the_output_directory_exists_before_sbatch(self, scheduler, flavour, tmp_path):
        seen = []

        def respond(argv):
            seen.append((tmp_path / "logs" / "n").is_dir())
            return _answer(flavour, "sbatch_ok.json")

        job = make_job(name="n", workdir=str(tmp_path), stdout="logs/n/${hpc.job_name}.out")
        with patch_seam(respond):
            scheduler.submit(job)
        assert seen == [True]

    def test_how_the_seam_is_called(self, scheduler, tmp_path):
        with patch(SEAM) as seam:
            seam.return_value = subprocess.CompletedProcess(
                ["sbatch"], 0, "Submitted batch job 1\n", ""
            )
            scheduler.submit(make_job(workdir=str(tmp_path)))
        (call,) = seam.call_args_list
        assert call.args[0][0] == "sbatch"
        assert call.kwargs["timeout"] == BaseScheduler.COMMAND_TIMEOUT
        assert call.kwargs.get("capture", True) is True
        assert call.kwargs["cwd"] == tmp_path

    def test_the_trace_carries_the_script(self, scheduler, flavour, tmp_path):
        with patch("hpc_runner.schedulers.slurm.scheduler.recorder.note_script") as note:
            with patch_seam(lambda argv: _answer(flavour, "sbatch_ok.json")) as seam:
                scheduler.submit(make_job(workdir=str(tmp_path)))
        ((path, text),) = (call.args for call in note.call_args_list)
        assert str(path) == seam.call_args.args[0][1]
        assert text == scheduler.preview(make_job(workdir=str(tmp_path)))

    @pytest.mark.parametrize(
        ("scenario", "headline"),
        [
            ("bad_partition", "sbatch: error: invalid partition specified: nosuch"),
            (
                "bad_account",
                "sbatch: error: Batch job submission failed: "
                "Invalid account or account/partition combination specified",
            ),
        ],
    )
    def test_a_rejected_submission_carries_sbatchs_message(
        self, scheduler, flavour, tmp_path, scenario, headline
    ):
        captured = _captured(flavour, f"sbatch_{scenario}.json")
        job = make_job(workdir=str(tmp_path))
        with patch_seam(lambda argv: _answer(flavour, f"sbatch_{scenario}.json")):
            with pytest.raises(SubmissionError) as err:
                scheduler.submit(job)
        assert str(err.value) == headline
        assert err.value.stderr == captured["stderr"]
        assert err.value.script == scheduler.preview(job)
        assert err.value.argv is not None and err.value.argv[0] == "sbatch"
        assert list((tmp_path / "scripts").iterdir()) == []

    def test_output_without_a_job_id_is_an_error(self, scheduler, tmp_path):
        with patch_seam(lambda argv: (0, "queued\n", "")):
            with pytest.raises(SubmissionError) as err:
                scheduler.submit(make_job(workdir=str(tmp_path)))
        assert str(err.value) == "could not parse job id from sbatch output: 'queued'"


class TestInteractive:
    @pytest.fixture(autouse=True)
    def _no_terminal(self, monkeypatch):
        monkeypatch.setattr(SlurmScheduler, "_at_a_terminal", staticmethod(lambda: False))

    def test_srun_runs_the_script_and_its_exit_code_is_the_results(self, scheduler, tmp_path):
        calls = []

        def respond(argv):
            calls.append((argv, Path(argv[-1]).read_text()))
            return 3, "", ""

        job = make_job(
            "xterm",
            interactive=True,
            name="gui",
            queue="short",
            project="proj",
            stdout="out.log",
            stderr="err.log",
            cpu=2,
            mem="1G",
            time="30m",
            workdir=str(tmp_path),
            scheduler_args=["--x11"],
        )
        with patch_seam(respond):
            result = scheduler.submit(job)

        assert result.job_id is None
        assert result.exit_code == 3
        assert result.job is job
        assert (result.stdout_path, result.stderr_path) == (None, None)
        ((argv, script),) = calls
        script_path = argv[-1]
        assert argv == [
            "srun",
            f"--chdir={tmp_path}",
            "--export=ALL",
            "--job-name=gui",
            "--cpus-per-task=2",
            "--ntasks=1",
            "--mem=1024",
            "--time=0-00:30:00",
            "--partition=short",
            "--account=proj",
            "--x11",
            script_path,
        ]
        assert Path(script_path).parent == tmp_path / "scripts"
        assert Path(script_path).name.startswith("hpc_interactive_")
        # The script deletes itself; what is left of it is removed after srun.
        assert f"rm -f {script_path}\n" in script
        assert not Path(script_path).exists()
        assert not (tmp_path / "out.log").exists()

    def test_keep_script(self, scheduler, tmp_path):
        job = make_job("xterm", interactive=True, workdir=str(tmp_path))
        with patch_seam(lambda argv: (0, "", "")) as seam:
            scheduler.submit(job, keep_script=True)
        kept = Path(seam.call_args.args[0][-1])
        assert kept.read_text() == scheduler.preview(job)
        assert f"rm -f {kept}" not in kept.read_text()

    def test_the_script_has_no_directives(self, scheduler):
        script = scheduler.preview(make_job("xterm", interactive=True))
        assert "#SBATCH" not in script
        assert "xterm\n_exit_code=$?\n" in script
        assert script.endswith("exit $_exit_code\n")

    def test_how_the_seam_is_called(self, scheduler, tmp_path):
        """srun runs for the job's lifetime on the user's terminal: no timeout, no capture."""
        with patch(SEAM) as seam:
            seam.return_value = subprocess.CompletedProcess(["srun"], 0, "", "")
            scheduler.submit(make_job("xterm", interactive=True, workdir=str(tmp_path)))
        (call,) = seam.call_args_list
        assert call.args[0][0] == "srun"
        assert call.kwargs["timeout"] is None
        assert call.kwargs["check"] is False
        assert call.kwargs["capture"] is False
        assert call.kwargs["cwd"] == tmp_path

    def test_the_trace_carries_the_script(self, scheduler, tmp_path):
        job = make_job("xterm", interactive=True, workdir=str(tmp_path))
        with patch("hpc_runner.schedulers.slurm.scheduler.recorder.note_script") as note:
            with patch_seam(lambda argv: (0, "", "")) as seam:
                scheduler.submit(job, keep_script=True)
        ((path, text),) = (call.args for call in note.call_args_list)
        assert str(path) == seam.call_args.args[0][-1]
        assert text == scheduler.preview(job)


class TestPty:
    """``--pty`` only when stdin and stdout are both terminals."""

    @pytest.mark.parametrize(
        ("stdin", "stdout", "pty"),
        [(True, True, True), (True, False, False), (False, True, False), (False, False, False)],
    )
    def test_rule(self, scheduler, monkeypatch, stdin, stdout, pty):
        monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: stdin))
        monkeypatch.setattr(sys, "stdout", SimpleNamespace(isatty=lambda: stdout))
        argv = scheduler._build_srun_command(make_job("xterm", interactive=True), "/s.sh")
        assert argv[:2] == (["srun", "--pty"] if pty else ["srun", "--chdir=/work"])
        assert ("--pty" in argv) is pty
