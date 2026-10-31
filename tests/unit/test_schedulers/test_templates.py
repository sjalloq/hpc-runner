"""Rendered job scripts: the shared environment block, quoting, ${hpc.*}, D15."""

import dataclasses
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from hpc_runner.core.config import HPCConfig
from hpc_runner.core.exceptions import ConfigError
from hpc_runner.core.result import JobStatus
from hpc_runner.schedulers.local import LocalScheduler
from hpc_runner.schedulers.sge import SGEScheduler
from tests.fakes import make_host, make_job

requires_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="bash not installed")

BEGIN = "# --- hpc-runner environment ---"
END = "# --- end hpc-runner environment ---"

# Every quoting hazard in one value: double and single quotes, $, spaces.
NASTY = """say "hi" $HOME 'q' \\ end"""


def _venv(root: Path) -> str:
    venv = root / "venv dir"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "activate").write_text("export HPC_TEST_VENV_ACTIVE=1\n")
    return str(venv)


def _full_job(tmp_path: Path, **overrides):
    """A job using every environment feature, with awkward values."""
    fields = dict(
        name="full",
        modules=["gcc/12", "odd name/1"],
        modules_path=["/mf one"],
        modules_path_append=["/mf two"],
        venv=_venv(tmp_path),
        env_unset=["http_proxy"],
        env_prepend={"PATH": "/pre bin"},
        env_append={"PYTHONPATH": "/app lib"},
        env_vars={"NASTY": NASTY.replace("$", "$$")},
    )
    fields.update(overrides)
    return make_job("echo done", **fields)


def _stub_modules(tmp_path: Path, body: str = ":") -> str:
    """A module init script defining a stub `module` function."""
    init = tmp_path / "init.sh"
    init.write_text(f"module() {{ {body}; }}\nexport -f module 2>/dev/null || true\n")
    return str(init)


def _env_block(script: str) -> str:
    return script[script.index(BEGIN) : script.index(END) + len(END)]


def _sched_config(**settings) -> HPCConfig:
    return HPCConfig(schedulers={"sge": dict(settings), "local": dict(settings)})


class TestSharedEnvironmentBlock:
    def test_three_templates_render_identical_blocks(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path / "exit"))
        config = _sched_config(module_init_script="/site/init.sh", module_system="modules")
        job = _full_job(tmp_path)
        sge, local = SGEScheduler(config), LocalScheduler(config)
        batch = sge.preview(job)
        interactive = sge.preview(dataclasses.replace(job, interactive=True))
        local_script = local.preview(job)
        assert "#$ " in batch and "#$ " not in interactive  # really different templates
        assert _env_block(batch) == _env_block(interactive) == _env_block(local_script)

    def test_order(self, tmp_path):
        block = _env_block(LocalScheduler(HPCConfig()).preview(_full_job(tmp_path)))
        markers = [
            "module purge",
            "module use '/mf one'",
            "module use --append '/mf two'",
            "module load gcc/12",
            "/bin/activate",
            "unset http_proxy",
            "export PATH=",
            "export PYTHONPATH=",
            "export NASTY=",
        ]
        positions = [block.index(m) for m in markers]
        assert positions == sorted(positions)

    @requires_bash
    @pytest.mark.parametrize("which", ["batch", "interactive", "local"])
    @pytest.mark.parametrize("verbose", [False, True])
    def test_bash_syntax(self, tmp_path, monkeypatch, which, verbose):
        from hpc_runner.core.dependency import Dependency

        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path / "exit dir"))
        config = _sched_config(module_init_script="/site dir/init.sh", module_system="modules")
        host = make_host(env={"DISPLAY": ":0 'x'"})
        job = _full_job(
            tmp_path,
            inherit_env=False,
            env_passthrough=["DISPLAY"],
            host=host,
            dependencies=[Dependency("1")] if which == "batch" else [],
            interactive=which == "interactive",
            stdout="out file.log" if which == "local" else None,
        )
        job = dataclasses.replace(
            job, env_vars={**job.env_vars, "MAKEFLAGS": "-j${hpc.cpus} ${hpc.job_name}"}
        )
        scheduler = LocalScheduler(config) if which == "local" else SGEScheduler(config)
        script = scheduler.preview(job, verbose=verbose)
        result = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True)
        assert result.returncode == 0, result.stderr


@requires_bash
class TestQuotingRoundTrip:
    """B4: values reach the job exactly as written."""

    def test_values_round_trip(self, tmp_path):
        config = _sched_config(module_init_script=_stub_modules(tmp_path))
        out = tmp_path / "out.txt"
        job = _full_job(
            tmp_path,
            workdir=str(tmp_path),
            stdout=str(out),
        )
        job = dataclasses.replace(
            job,
            command='printf "%s\\n" "$NASTY" "$PATH" "$PYTHONPATH" "$HPC_TEST_VENV_ACTIVE"',
        )
        result = LocalScheduler(config).submit(job)
        assert result.wait(poll_interval=0.05) is JobStatus.COMPLETED
        nasty, path, pythonpath, venv = out.read_text().splitlines()
        assert nasty == NASTY
        assert path.startswith("/pre bin:")
        assert pythonpath.endswith("/app lib")
        assert venv == "1"


class TestModuleArgumentsRoundTrip:
    """Module names and paths reach `module` as single arguments, spaces included."""

    def test_module_sees_each_name_and_path_as_one_argument(self, tmp_path):
        log = tmp_path / "module calls.log"
        record = f'printf "%s\\n" "$#" "$@" >> {shlex.quote(str(log))}'
        config = _sched_config(module_init_script=_stub_modules(tmp_path, body=record))
        job = _full_job(tmp_path, workdir=str(tmp_path), stdout=str(tmp_path / "out.txt"))
        result = LocalScheduler(config).submit(job)
        assert result.wait(poll_interval=0.05) is JobStatus.COMPLETED

        calls, lines = [], log.read_text().splitlines()
        while lines:
            count, lines = int(lines[0]), lines[1:]
            calls.append(lines[:count])
            lines = lines[count:]
        assert calls == [
            ["purge"],
            ["use", "/mf one"],
            ["use", "--append", "/mf two"],
            ["load", "gcc/12"],
            ["load", "odd name/1"],
        ]


class TestModuleCommands:
    """D15: purge always runs; module output is shown only on failure."""

    def test_purge_unconditional_and_guarded(self):
        script = LocalScheduler(HPCConfig()).preview(make_job("x"))
        assert (
            'module purge 2>"$_hpc_modlog" || { cat "$_hpc_modlog" >&2; '
            "echo 'hpc-runner: module purge failed' >&2; exit 1; }"
        ) in script
        assert "_hpc_modlog=$(mktemp)" in script

    def test_load_guarded(self):
        script = SGEScheduler(HPCConfig()).preview(make_job("x", modules=["xcelium/23"]))
        assert (
            'module load xcelium/23 2>"$_hpc_modlog" || { cat "$_hpc_modlog" >&2; '
            "echo 'hpc-runner: module load xcelium/23 failed' >&2; exit 1; }"
        ) in script

    def test_verbose_renders_plain_lines(self):
        job = make_job("x", modules=["xcelium/23"], modules_path=["/mf"])
        script = SGEScheduler(HPCConfig()).preview(job, verbose=True)
        lines = script.splitlines()
        assert "module purge" in lines
        assert "module use /mf" in lines
        assert "module load xcelium/23" in lines
        assert "_hpc_modlog=" not in script
        assert "set -e" in lines  # still fatal on failure

    def test_one_exit_trap(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path))
        script = SGEScheduler(HPCConfig()).preview(make_job("x"))
        assert script.count("\ntrap ") == 1
        assert "trap _hpc_on_exit EXIT" in script

    def test_modules_verbosity_only_for_environment_modules(self):
        job = make_job("x")
        assert "MODULES_VERBOSITY" not in LocalScheduler(HPCConfig()).preview(job)
        for system in ("lmod", ""):
            script = LocalScheduler(_sched_config(module_system=system)).preview(job)
            assert "MODULES_VERBOSITY" not in script
        script = LocalScheduler(_sched_config(module_system="modules")).preview(job)
        assert script.index("export MODULES_VERBOSITY=concise") < script.index("module purge")

    def test_unknown_module_system_is_error(self):
        with pytest.raises(ConfigError, match="module_system"):
            SGEScheduler(_sched_config(module_system="spack"))

    def test_module_init_script_quoted(self):
        script = LocalScheduler(_sched_config(module_init_script="/a b/init")).preview(
            make_job("x")
        )
        assert ". '/a b/init'" in script

    @requires_bash
    def test_failure_is_loud(self, tmp_path):
        init = _stub_modules(
            tmp_path,
            'if [ "$1" = load ]; then echo "ERROR: no $2" >&2; return 1; fi',
        )
        job = make_job("echo ran", modules=["missing/1"], workdir=str(tmp_path), interactive=True)
        script = LocalScheduler(_sched_config(module_init_script=init)).preview(job)
        result = subprocess.run(
            ["bash", "-c", script], text=True, capture_output=True, cwd=tmp_path
        )
        assert result.returncode == 1
        assert "ERROR: no missing/1" in result.stderr
        assert "hpc-runner: module load missing/1 failed" in result.stderr
        assert "ran" not in result.stdout

    @requires_bash
    def test_success_is_quiet_and_log_removed(self, tmp_path):
        init = _stub_modules(tmp_path, 'echo "Loading $2" >&2')
        job = make_job(
            'echo "$_hpc_modlog"', modules=["ok/1"], workdir=str(tmp_path), interactive=True
        )
        script = LocalScheduler(_sched_config(module_init_script=init)).preview(job)
        result = subprocess.run(
            ["bash", "-c", script], text=True, capture_output=True, cwd=tmp_path
        )
        assert result.returncode == 0, result.stderr
        assert "Loading" not in result.stderr
        modlog = result.stdout.strip()
        assert modlog and not Path(modlog).exists()


class TestHpcVars:
    def test_sge_renders_scheduler_variables(self):
        job = make_job("x", cpu=4, env_vars={"MAKEFLAGS": "-j${hpc.cpus}"})
        script = SGEScheduler(HPCConfig()).preview(job)
        assert 'export MAKEFLAGS=-j"$NSLOTS"' in script

    def test_local_renders_literals(self):
        job = make_job("x", cpu=4, name="my job", env_vars={"M": "-j${hpc.cpus} ${hpc.job_name}"})
        script = LocalScheduler(HPCConfig()).preview(job)
        assert "export M=-j4' 'my_job" in script  # the sanitised name

    def test_local_cpus_defaults_to_one(self):
        job = make_job("x", env_vars={"M": "-j${hpc.cpus}"})
        assert "export M=-j1" in LocalScheduler(HPCConfig()).preview(job)

    def test_local_job_id_is_the_allocated_id(self, tmp_path):
        job = make_job("x", env_vars={"ID": "${hpc.job_id}"})
        local = LocalScheduler(HPCConfig())
        assert local.hpc_vars(job, job_id="local_7")["job_id"] == "local_7"
        assert "export ID='<job_id>'" in local.preview(job)

    @requires_bash
    def test_local_values_at_run_time(self, tmp_path):
        config = _sched_config(module_init_script=_stub_modules(tmp_path))
        out = tmp_path / "out"
        job = make_job(
            'echo "$A"',
            cpu=3,
            env_vars={"A": "${hpc.cpus}:${hpc.task_id}:${hpc.job_id}"},
            workdir=str(tmp_path),
            stdout=str(out),
        )
        local = LocalScheduler(config)
        assert local.submit(job).wait(poll_interval=0.05) is JobStatus.COMPLETED
        cpus, task, job_id = out.read_text().strip().split(":")
        assert (cpus, task) == ("3", "")
        assert job_id.startswith("local_")

    def test_render_hpc_vars_path_context(self):
        sge = SGEScheduler(HPCConfig())
        job = make_job("x")
        text = "${hpc.job_name}.${hpc.job_id}.${hpc.task_id}"
        assert sge.render_hpc_vars(text, job=job) == "$JOB_NAME.$JOB_ID.$SGE_TASK_ID"
        assert sge.render_hpc_vars(text, job=job, context="path") == "$JOB_NAME.$JOB_ID.$TASK_ID"


class TestInheritEnv:
    """D3: -V by default; strict mode passes named variables explicitly."""

    def test_default_is_dash_v(self):
        script = SGEScheduler(HPCConfig()).preview(make_job("x"))
        assert "#$ -V" in script.splitlines()

    def test_passthrough_replaces_dash_v(self):
        host = make_host(env={"DISPLAY": ":0", "LM_LICENSE_FILE": "5280@lic host"})
        job = make_job(
            "x", inherit_env=False, env_passthrough=["DISPLAY", "LM_LICENSE_FILE"], host=host
        )
        lines = SGEScheduler(HPCConfig()).preview(job).splitlines()
        assert "#$ -V" not in lines
        assert "#$ -v DISPLAY=:0" in lines
        assert "#$ -v LM_LICENSE_FILE='5280@lic host'" in lines

    def test_passthrough_ignored_with_inherit_env(self):
        job = make_job("x", env_passthrough=["DISPLAY"])
        assert "#$ -v" not in SGEScheduler(HPCConfig()).preview(job)

    def test_qrsh_passthrough_args(self):
        host = make_host(env={"DISPLAY": ":0"})
        job = make_job(
            "xterm", inherit_env=False, env_passthrough=["DISPLAY"], host=host, interactive=True
        )
        cmd = SGEScheduler(HPCConfig())._build_qrsh_command(job, "/s.sh")
        assert "-V" not in cmd
        assert cmd[cmd.index("-v") + 1] == "DISPLAY=:0"

    @requires_bash
    def test_local_strict_environment(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HPC_TEST_LEAK", "leaked")
        config = _sched_config(module_init_script=_stub_modules(tmp_path))
        out = tmp_path / "out"
        host = make_host(env={"DISPLAY": ":9"})
        job = make_job(
            'echo "${HPC_TEST_LEAK:-none} $DISPLAY"',
            inherit_env=False,
            env_passthrough=["DISPLAY"],
            host=host,
            workdir=str(tmp_path),
            stdout=str(out),
        )
        assert LocalScheduler(config).submit(job).wait(poll_interval=0.05) is JobStatus.COMPLETED
        assert out.read_text().strip() == "none :9"


class TestScriptPathsQuoted:
    def test_rm_script_path_quoted(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HPC_EXIT_DIR", str(tmp_path / "exit dir"))
        sge = SGEScheduler(HPCConfig())
        job = make_job("x")
        assert "rm -f '/a b/s.sh'" in sge._render_batch(job, "/a b/s.sh", keep_script=False)
        assert f"_hpc_exit_dir='{tmp_path}/exit dir'" in sge.preview(job)


class TestBaseJobName:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [("ok-name.1", "ok-name.1"), ("3 bad/name", "3_bad_name"), ("a\tb\x01c", "a_b_c")],
    )
    def test_baseline_sanitisation(self, name, expected):
        assert LocalScheduler(HPCConfig()).job_name(make_job(name=name)) == expected
