"""Tests for resolve(spec, config, host)."""

from pathlib import Path

import pytest

from hpc_runner.core.array import ArrayRange
from hpc_runner.core.config import HPCConfig
from hpc_runner.core.dependency import Dependency
from hpc_runner.core.exceptions import ConfigError, NotSupportedError
from hpc_runner.core.merge import Edit, Set
from hpc_runner.core.resolve import resolve
from hpc_runner.core.resources import Resource, ResourceSet
from hpc_runner.core.spec import JobSpec
from tests.fakes import make_host, make_job


def _config(defaults=None, tools=None, types=None) -> HPCConfig:
    return HPCConfig(defaults=defaults or {}, tools=tools or {}, types=types or {})


class TestBasics:
    def test_command_string_kept_raw(self):
        job = make_job("echo 'a b' | wc")
        assert job.command == "echo 'a b' | wc"

    def test_list_command_is_shell_quoted(self):
        """B3: list commands keep their quoting."""
        job = make_job(["bash", "-c", "echo hello world"])
        assert job.command == "bash -c 'echo hello world'"

    def test_spec_values(self):
        job = make_job(cpu=4, mem="16G", time="2:00:00", queue="gpu", project="proj1")
        assert (job.cpu, job.mem, job.time, job.queue, job.project) == (
            4,
            "16G",
            "2:00:00",
            "gpu",
            "proj1",
        )

    def test_package_defaults(self):
        job = make_job()
        assert job.inherit_env is True
        assert job.shell == "/bin/bash"
        assert job.use_cwd is True
        assert job.interactive is False
        assert job.cpu is None
        assert job.array is None

    def test_spec_list_modules(self):
        job = make_job("python script.py", modules=["python/3.11", "cuda/12.0"])
        assert job.modules == ("python/3.11", "cuda/12.0")

    def test_resource_set(self):
        resources = ResourceSet().add("gpu", 2).add("license", 1)
        job = make_job(resources=resources)
        assert len(job.resources) == 2
        assert job.resources is not resources

    def test_resource_list_of_dicts(self):
        job = make_job(resources=[{"name": "gpu", "value": 2}])
        assert job.resources.get("gpu").value == 2

    def test_scheduler_args(self):
        job = make_job(scheduler_args={"sge": ["-l", "scratch=10G"], "slurm": ["--x"]})
        assert job.scheduler_args == {"sge": ("-l", "scratch=10G"), "slurm": ("--x",)}

    def test_dependencies_carried(self):
        job = make_job(dependencies=[Dependency("1")])
        assert job.dependencies == (Dependency("1"),)

    def test_array_parsed(self):
        job = make_job(array="1-10:2", interactive=False)
        assert job.array == ArrayRange(1, 10, 2)

    def test_interactive_array_is_not_supported(self):
        with pytest.raises(NotSupportedError, match="array"):
            make_job(array="1-10", interactive=True)

    def test_spec_is_not_mutated(self):
        spec = JobSpec("echo hi", modules=["a"])
        resolve(spec, HPCConfig(), make_host())
        assert spec.modules == ["a"]
        assert spec.name is None


class TestName:
    def test_generated_from_host_user(self):
        job = make_job("python script.py", host=make_host(user="bob"))
        assert job.name == "bob_python"
        assert job.provenance["name"] == "host"

    def test_skips_env_assignments_and_paths(self):
        job = make_job("FOO=1 /usr/bin/make all")
        assert job.name == "alice_make"

    def test_explicit(self):
        job = make_job(name="my_job")
        assert job.name == "my_job"
        assert job.provenance["name"] == "spec"

    def test_from_config(self):
        job = make_job(config=_config(defaults={"name": "cfg_name"}))
        assert job.name == "cfg_name"
        assert job.provenance["name"] == "defaults"


def _venv(root, name="venv"):
    """A directory that passes the bin/activate check."""
    venv = root / name
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "activate").write_text("")
    return str(venv)


class TestVenv:
    """D4: unset captures VIRTUAL_ENV, a path is used, False is none."""

    def test_captured_from_host(self, tmp_path):
        venv = _venv(tmp_path)
        job = make_job(host=make_host(env={"VIRTUAL_ENV": venv}))
        assert job.venv == venv
        assert job.provenance["venv"] == "captured from VIRTUAL_ENV"

    def test_none_without_host_venv(self):
        job = make_job()
        assert job.venv is None
        assert job.provenance["venv"] == "package"

    def test_explicit_path(self, tmp_path):
        a, b = _venv(tmp_path, "a"), _venv(tmp_path, "b")
        job = make_job(venv=b, host=make_host(env={"VIRTUAL_ENV": a}))
        assert job.venv == b
        assert job.provenance["venv"] == "spec"

    def test_false_disables_capture(self, tmp_path):
        job = make_job(venv=False, host=make_host(env={"VIRTUAL_ENV": _venv(tmp_path)}))
        assert job.venv is None
        assert job.provenance["venv"] == "spec (disabled)"

    def test_config_beats_capture(self, tmp_path):
        cfg_venv = _venv(tmp_path, "cfg")
        job = make_job(
            "python x.py",
            config=_config(tools={"python": {"venv": cfg_venv}}),
            host=make_host(env={"VIRTUAL_ENV": _venv(tmp_path, "a")}),
        )
        assert job.venv == cfg_venv
        assert job.provenance["venv"] == "tools.python"

    def test_type_disables(self, tmp_path):
        cfg = _config(defaults={"venv": _venv(tmp_path)}, types={"lint": {"venv": False}})
        job = make_job("make lint", type="lint", config=cfg)
        assert job.venv is None
        assert job.provenance["venv"] == "types.lint (disabled)"

    def test_path_is_expanded(self, tmp_path):
        _venv(tmp_path, ".venv")
        job = make_job(venv="${PROJ}/.venv", host=make_host(env={"PROJ": str(tmp_path)}))
        assert job.venv == f"{tmp_path}/.venv"

    def test_missing_activate_is_error(self, tmp_path):
        with pytest.raises(ConfigError, match=r"has no bin/activate \(stale VIRTUAL_ENV\?\)"):
            make_job(venv=str(tmp_path / "gone"))

    def test_stale_captured_venv_is_error(self, tmp_path):
        host = make_host(env={"VIRTUAL_ENV": str(tmp_path / "gone")})
        with pytest.raises(ConfigError, match="captured from VIRTUAL_ENV has no bin/activate"):
            make_job(host=host)


class TestConfigLayers:
    def test_defaults_applied(self):
        job = make_job(config=_config(defaults={"cpu": 2, "mem": "8G", "queue": "batch"}))
        assert (job.cpu, job.mem, job.queue) == (2, "8G", "batch")

    def test_tool_auto_detected(self):
        cfg = _config(tools={"python": {"cpu": 4, "modules": ["python/3.11"]}})
        job = make_job("python train.py", config=cfg)
        assert job.cpu == 4
        assert job.modules == ("python/3.11",)

    def test_tool_with_path_stripped(self):
        job = make_job("/usr/bin/python script.py", config=_config(tools={"python": {"cpu": 8}}))
        assert job.cpu == 8

    def test_type_config_applied(self):
        cfg = _config(types={"gpu": {"queue": "gpu.q", "resources": [{"name": "gpu", "value": 1}]}})
        job = make_job("python train.py", type="gpu", config=cfg)
        assert job.queue == "gpu.q"
        assert len(job.resources) == 1
        assert job.type == "gpu"

    def test_type_skips_tool_matching(self):
        cfg = _config(tools={"python": {"cpu": 99}}, types={"sim": {"cpu": 2}})
        job = make_job("python run.py", type="sim", config=cfg)
        assert job.cpu == 2

    def test_unknown_type_is_error(self):
        with pytest.raises(ConfigError, match="unknown job type 'nope'"):
            make_job(type="nope", config=_config(defaults={"cpu": 3}))

    def test_spec_overrides_config(self):
        job = make_job(cpu=16, mem="64G", config=_config(defaults={"cpu": 2, "mem": "4G"}))
        assert (job.cpu, job.mem) == (16, "64G")

    def test_config_overrides_package_defaults(self):
        cfg = _config(defaults={"shell": "/bin/zsh", "inherit_env": False, "use_cwd": False})
        job = make_job(config=cfg)
        assert job.shell == "/bin/zsh"
        assert job.inherit_env is False
        assert job.use_cwd is False

    def test_config_command_key_ignored(self):
        job = make_job("echo real", config=_config(defaults={"command": "nope", "cpu": 2}))
        assert job.command == "echo real"
        assert job.cpu == 2

    def test_unknown_tool_gets_defaults_only(self):
        cfg = _config(defaults={"cpu": 1}, tools={"python": {"cpu": 8}})
        assert make_job("unknown_tool arg1", config=cfg).cpu == 1

    def test_tool_option_specialisation(self):
        cfg = _config(tools={"make": {"cpu": 2, "options": {"sim": {"cpu": 8}}}})
        job = make_job("make sim", config=cfg)
        assert job.cpu == 8
        assert job.provenance["cpu"] == "tools.make.options.sim"

    def test_resources_from_config(self):
        cfg = _config(defaults={"resources": [{"name": "scratch", "value": "10G"}]})
        job = make_job(config=cfg)
        assert job.resources.get("scratch").value == "10G"

    def test_spec_resources_append(self):
        cfg = _config(defaults={"resources": [{"name": "scratch", "value": "10G"}]})
        job = make_job(config=cfg, resources=ResourceSet().add("gpu", 1))
        assert [r.name for r in job.resources] == ["scratch", "gpu"]

    def test_spec_resources_set_replaces(self):
        cfg = _config(defaults={"resources": [{"name": "scratch", "value": "10G"}]})
        job = make_job(config=cfg, resources=Set([Resource("gpu", 1)]))
        assert [r.name for r in job.resources] == ["gpu"]

    def test_interactive_from_config(self):
        cfg = _config(tools={"xterm": {"interactive": True}})
        assert make_job("xterm", config=cfg).interactive is True

    def test_spec_interactive_beats_config(self):
        cfg = _config(tools={"xterm": {"interactive": True}})
        assert make_job("xterm", config=cfg, interactive=False).interactive is False

    def test_array_never_from_config(self):
        """array is per invocation; the schema rejects it and resolve ignores it."""
        cfg = _config(types={"sweep": {"array": "1-4"}})
        assert make_job(type="sweep", config=cfg).array is None

    def test_env_unset_from_config(self):
        cfg = _config(defaults={"env_unset": ["https_proxy"]})
        assert make_job(config=cfg).env_unset == ("https_proxy",)

    def test_spec_env_unset_appends(self):
        cfg = _config(defaults={"env_unset": ["https_proxy"]})
        assert make_job(config=cfg, env_unset=["X"]).env_unset == ("https_proxy", "X")

    def test_spec_env_unset_set_replaces(self):
        cfg = _config(defaults={"env_unset": ["https_proxy"]})
        assert make_job(config=cfg, env_unset=Set(["X"])).env_unset == ("X",)


class TestProvenance:
    def test_layers_recorded(self):
        cfg = _config(
            defaults={"cpu": 1, "mem": "4G"},
            tools={"python": {"cpu": 4}},
        )
        job = make_job("python x.py", config=cfg, queue="q")
        assert job.provenance["cpu"] == "tools.python"
        assert job.provenance["mem"] == "defaults"
        assert job.provenance["queue"] == "spec"
        assert job.provenance["shell"] == "package"
        assert job.provenance["time"] == "package"
        assert job.provenance["command"] == "spec"

    def test_type_layer_name(self):
        job = make_job(type="gpu", config=_config(types={"gpu": {"queue": "g"}}))
        assert job.provenance["queue"] == "types.gpu"

    def test_every_field_recorded(self):
        import dataclasses

        from hpc_runner.core.job import Job

        job = make_job()
        fields = {f.name for f in dataclasses.fields(Job)} - {"provenance"}
        assert fields <= set(job.provenance)


class TestListsMerge:
    """D1: a spec list appends to the config list like any other layer."""

    def test_spec_modules_append(self):
        job = make_job(config=_config(defaults={"modules": ["default/1.0"]}), modules=["o/2.0"])
        assert job.modules == ("default/1.0", "o/2.0")

    def test_spec_modules_set_replaces(self):
        cfg = _config(defaults={"modules": ["default/1.0"]})
        job = make_job(config=cfg, modules=Set(["o/2.0"]))
        assert job.modules == ("o/2.0",)

    def test_config_modules_kept_without_spec(self):
        job = make_job(config=_config(defaults={"modules": ["a", "b"]}))
        assert job.modules == ("a", "b")

    def test_modules_path_append_from_config(self):
        job = make_job(config=_config(defaults={"modules_path_append": ["/site/local"]}))
        assert job.modules_path_append == ("/site/local",)

    def test_modules_path_append_spec_appends(self):
        cfg = _config(defaults={"modules_path_append": ["/site/local"]})
        job = make_job(config=cfg, modules_path_append=["/override"])
        assert job.modules_path_append == ("/site/local", "/override")
        assert job.modules_path == ()

    def test_type_config_merges_with_defaults(self):
        cfg = _config(
            defaults={"modules_path_append": ["/site/local"]},
            types={"xrun": {"modules_path_append": ["/proj/modulefiles"]}},
        )
        job = make_job("xrun", type="xrun", config=cfg)
        assert job.modules_path_append == ("/site/local", "/proj/modulefiles")

    def test_type_edit_removes_default_module(self):
        cfg = _config(
            defaults={"modules": ["gcc/12", "python/3.11"]},
            types={"py": {"modules": Edit(remove=["python/3.11"], add=["python/3.12"])}},
        )
        assert make_job(type="py", config=cfg).modules == ("gcc/12", "python/3.12")

    def test_removed_entry_never_expanded(self):
        """$VAR in an entry a later layer removed is not an error."""
        cfg = _config(
            defaults={"modules": ["$TEST_UNSET_REMOVED"]},
            types={"t": {"modules": Set(["a"])}},
        )
        assert make_job(type="t", config=cfg).modules == ("a",)


class TestTypeComposition:
    """D2: types compose from tools; the chain is fixed."""

    CFG = _config(
        defaults={"cpu": 1, "modules": ["site/1"]},
        tools={
            "xrun": {
                "modules": ["xcelium/23"],
                "resources": [{"name": "xcelium_lic", "value": 1}],
                "options": {"-gui": {"queue": "gui.q"}},
            },
            "slang": {"modules": ["slang/0.9"], "mem": "8G"},
            "python": {"modules": ["python/3.11"], "cpu": 4},
        },
        types={"lint": {"tools": ["xrun", "slang"], "cpu": 2}},
    )

    def test_chain_without_type(self):
        names = [n for n, _ in self.CFG.layers_for(JobSpec("xrun -gui top.f"))]
        assert names == ["defaults", "tools.xrun", "tools.xrun.options.-gui"]

    def test_chain_with_type(self):
        names = [n for n, _ in self.CFG.layers_for(JobSpec("python x.py", type="lint"))]
        assert names == ["defaults", "tools.xrun", "tools.slang", "types.lint"]

    def test_type_pulls_in_tools_in_order(self):
        job = make_job("make lint", type="lint", config=self.CFG)
        assert job.modules == ("site/1", "xcelium/23", "slang/0.9")
        assert job.provenance["modules"] == (
            ("site/1", "defaults"),
            ("xcelium/23", "tools.xrun"),
            ("slang/0.9", "tools.slang"),
        )
        assert (job.cpu, job.provenance["cpu"]) == (2, "types.lint")
        assert (job.mem, job.provenance["mem"]) == ("8G", "tools.slang")
        assert job.provenance["resources"][0][1] == "tools.xrun"

    def test_type_ignores_argv_tool(self):
        job = make_job("python x.py", type="lint", config=self.CFG)
        assert "python/3.11" not in job.modules
        assert job.cpu == 2

    def test_type_does_not_apply_tool_options(self):
        job = make_job("xrun -gui top.f", type="lint", config=self.CFG)
        assert job.queue is None

    def test_without_type_options_applied(self):
        job = make_job("xrun -gui top.f", config=self.CFG)
        assert (job.queue, job.provenance["queue"]) == ("gui.q", "tools.xrun.options.-gui")
        assert job.provenance["modules"] == (("site/1", "defaults"), ("xcelium/23", "tools.xrun"))

    def test_spec_is_the_last_layer(self):
        job = make_job("x", type="lint", config=self.CFG, cpu=8, modules=["extra/1"])
        assert job.provenance["cpu"] == "spec"
        assert job.provenance["modules"][-1] == ("extra/1", "spec")

    def test_type_with_unknown_tool(self):
        cfg = _config(types={"t": {"tools": ["missing"]}})
        with pytest.raises(ConfigError, match=r"\[types.t\] tools: no tool 'missing'"):
            make_job(type="t", config=cfg)


class TestSchedulerArgs:
    def test_merged_across_layers_per_scheduler(self):
        cfg = _config(
            defaults={"scheduler_args": {"sge": ["-binding", "linear:1"]}},
            tools={"sim": {"scheduler_args": {"sge": ["-masterq", "a.q"], "slurm": ["--x"]}}},
        )
        job = make_job("sim run", config=cfg, scheduler_args={"sge": ["-l", "gpu=1"]})
        assert job.scheduler_args == {
            "sge": ("-binding", "linear:1", "-masterq", "a.q", "-l", "gpu=1"),
            "slurm": ("--x",),
        }
        assert job.provenance["scheduler_args"][0] == (("sge", "-binding"), "defaults")
        assert (("slurm", "--x"), "tools.sim") in job.provenance["scheduler_args"]
        assert (("sge", "gpu=1"), "spec") in job.provenance["scheduler_args"]

    def test_repeated_flags_kept(self):
        # DECISION NEEDED (see resolve): scheduler_args are argv, not deduped.
        cfg = _config(defaults={"scheduler_args": {"sge": ["-l", "a=1"]}})
        job = make_job(config=cfg, scheduler_args={"sge": ["-l", "b=2", "-l", "c=3"]})
        assert job.scheduler_args["sge"] == ("-l", "a=1", "-l", "b=2", "-l", "c=3")
        assert [layer for _, layer in job.provenance["scheduler_args"]] == [
            "defaults",
            "defaults",
            "spec",
            "spec",
            "spec",
            "spec",
        ]

    def test_edit_removes_every_occurrence(self):
        cfg = _config(defaults={"scheduler_args": {"sge": ["-pty", "y", "-l", "a=1"]}})
        job = make_job(config=cfg, scheduler_args={"sge": Edit(remove=["-pty", "y"])})
        assert job.scheduler_args["sge"] == ("-l", "a=1")

    def test_verbs_apply_per_scheduler(self):
        cfg = _config(defaults={"scheduler_args": {"sge": ["-binding", "linear:1"]}})
        job = make_job(config=cfg, scheduler_args={"sge": Set(["-pty", "y"])})
        assert job.scheduler_args["sge"] == ("-pty", "y")

    def test_each_scheduler_reads_its_own(self):
        from types import SimpleNamespace

        from hpc_runner.schedulers.base import BaseScheduler

        cfg = _config(defaults={"scheduler_args": {"sge": ["-binding", "linear:1"]}})
        job = make_job(config=cfg)
        sge, local = SimpleNamespace(name="sge"), SimpleNamespace(name="local")
        assert BaseScheduler._passthrough_args(sge, job) == ("-binding", "linear:1")  # type: ignore[arg-type]
        assert BaseScheduler._passthrough_args(local, job) == ()  # type: ignore[arg-type]


class TestEnvVarExpansion:
    """D5: $VAR in env_* values is resolved from host.env; undefined is an error."""

    def _job(self, field, value, env):
        return make_job(config=_config(defaults={field: value}), host=make_host(env=env))

    def test_env_vars_expanded(self):
        job = self._job("env_vars", {"MY_HOME": "$TEST_HPC_VAR"}, {"TEST_HPC_VAR": "/p"})
        assert job.env_vars["MY_HOME"] == "/p"

    def test_braced_syntax(self):
        job = self._job("env_vars", {"H": "${TEST_HPC_VAR}/sub"}, {"TEST_HPC_VAR": "/p"})
        assert job.env_vars["H"] == "/p/sub"

    def test_undefined_is_error(self):
        with pytest.raises(ConfigError, match=r"defaults: env_vars.H: undefined variable \$UNDEF"):
            self._job("env_vars", {"H": "$UNDEF_XYZ"}, {})

    def test_braced_undefined_is_error(self):
        with pytest.raises(ConfigError, match=r"undefined variable \$UNDEF_XYZ"):
            self._job("env_vars", {"H": "${UNDEF_XYZ}"}, {})

    def test_env_prepend_expanded(self):
        job = self._job("env_prepend", {"PATH": "$T/bin"}, {"T": "/tools"})
        assert job.env_prepend["PATH"] == "/tools/bin"

    def test_env_append_expanded(self):
        job = self._job("env_append", {"PYTHONPATH": "$T/lib"}, {"T": "/tools"})
        assert job.env_append["PYTHONPATH"] == "/tools/lib"

    def test_plain_values_unchanged(self):
        job = self._job("env_vars", {"FOO": "/a/plain/path", "BAR": "literal"}, {})
        assert dict(job.env_vars) == {"FOO": "/a/plain/path", "BAR": "literal"}

    def test_multiple_vars_in_one_value(self):
        job = self._job("env_vars", {"C": "$TEST_A/$TEST_B"}, {"TEST_A": "foo", "TEST_B": "bar"})
        assert job.env_vars["C"] == "foo/bar"

    def test_dollar_dollar_is_literal(self):
        job = self._job("env_vars", {"M": "$$HOME"}, {"HOME": "/home/a"})
        assert job.env_vars["M"] == "$HOME"

    def test_hpc_runtime_kept(self):
        job = self._job("env_vars", {"MAKEFLAGS": "-j${hpc.cpus}"}, {})
        assert job.env_vars["MAKEFLAGS"] == "-j${hpc.cpus}"

    def test_runtime_variable_hint(self):
        with pytest.raises(ConfigError, match=r"did you mean \$\{hpc.cpus\}\?"):
            self._job("env_vars", {"MAKEFLAGS": "-j$NSLOTS"}, {})

    def test_not_read_from_process_environment(self, monkeypatch):
        monkeypatch.setenv("TEST_HPC_PROCESS_ONLY", "leak")
        with pytest.raises(ConfigError, match="TEST_HPC_PROCESS_ONLY"):
            self._job("env_vars", {"X": "$TEST_HPC_PROCESS_ONLY"}, {})

    def test_resolver_variables(self):
        host = make_host(cwd=Path("/work/proj"), user="bob")
        job = make_job(env_vars={"A": "${CWD}:${USER}"}, host=host)
        assert job.env_vars["A"] == "/work/proj:bob"

    def test_resolver_variable_beats_host_env(self):
        host = make_host(env={"USER": "root"}, user="bob")
        assert make_job(env_vars={"U": "$USER"}, host=host).env_vars["U"] == "bob"

    def test_config_dir_in_spec_is_error(self):
        with pytest.raises(ConfigError, match=r"only defined for values set in a config file"):
            make_job(env_vars={"P": "${CONFIG_DIR}/bin"})

    def test_git_root(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / "sub").mkdir()
        job = make_job(env_vars={"R": "${GIT_ROOT}"}, host=make_host(cwd=tmp_path / "sub"))
        assert job.env_vars["R"] == str(tmp_path.resolve())

    def test_git_root_outside_repo_is_error(self, tmp_path):
        with pytest.raises(ConfigError, match="not inside a git repository"):
            make_job(env_vars={"R": "${GIT_ROOT}"}, host=make_host(cwd=tmp_path))


class TestExpandedFields:
    """D5.4: every string that reaches the script or the scheduler is expanded."""

    ENV = {"V": "val"}

    @pytest.mark.parametrize(
        "field", ["name", "mem", "time", "queue", "project", "stdout", "stderr", "workdir"]
    )
    def test_scalar(self, field):
        job = make_job(host=make_host(env=self.ENV), **{field: "x-$V"})
        assert getattr(job, field) == "x-val"

    @pytest.mark.parametrize("field", ["modules", "modules_path", "modules_path_append"])
    def test_module_lists(self, field):
        job = make_job(host=make_host(env=self.ENV), **{field: ["x/$V"]})
        assert getattr(job, field) == ("x/val",)
        assert job.provenance[field] == (("x/val", "spec"),)

    @pytest.mark.parametrize("field", ["env_vars", "env_prepend", "env_append"])
    def test_env_dicts(self, field):
        job = make_job(host=make_host(env=self.ENV), **{field: {"K": "$V"}})
        assert getattr(job, field) == {"K": "val"}

    def test_scheduler_args(self):
        job = make_job(host=make_host(env=self.ENV), scheduler_args={"sge": ["-l", "x=$V"]})
        assert job.scheduler_args == {"sge": ("-l", "x=val")}
        assert job.provenance["scheduler_args"][1] == (("sge", "x=val"), "spec")

    def test_command_not_expanded(self):
        job = make_job("echo $V", host=make_host(env=self.ENV))
        assert job.command == "echo $V"

    def test_env_unset_names_not_expanded(self):
        assert make_job(env_unset=["http_proxy"]).env_unset == ("http_proxy",)

    def test_error_names_the_layer(self):
        with pytest.raises(ConfigError, match=r"^types.t: queue: undefined variable \$NOPE"):
            make_job(type="t", config=_config(types={"t": {"queue": "$NOPE"}}))


class TestEnvConflicts:
    def test_same_key_set_and_prepend_in_spec_is_error(self):
        with pytest.raises(ConfigError, match="PATH is both set"):
            make_job(env_vars={"PATH": "/a"}, env_prepend={"PATH": "/b"})

    def test_across_layers_allowed(self):
        cfg = _config(defaults={"env_prepend": {"PATH": "/b"}})
        job = make_job(config=cfg, env_vars={"PATH": "/a"})
        assert (job.env_vars["PATH"], job.env_prepend["PATH"]) == ("/a", "/b")

    def test_spec_env_name_validated(self):
        with pytest.raises(ConfigError, match="not a valid environment variable name"):
            make_job(env_vars={"A B": "x"})

    def test_spec_env_unset_name_validated(self):
        with pytest.raises(ConfigError, match="not a valid environment variable name"):
            make_job(env_unset=Set(["a;rm"]))


class TestEnvPassthrough:
    """D3: inherit_env = false passes only the named submit-host variables."""

    def test_resolved_from_host(self):
        host = make_host(env={"DISPLAY": ":0", "OTHER": "x"})
        job = make_job(inherit_env=False, env_passthrough=["DISPLAY"], host=host)
        assert job.env_passthrough == {"DISPLAY": ":0"}
        assert job.provenance["env_passthrough"] == (("DISPLAY", "spec"),)

    def test_missing_is_error(self):
        with pytest.raises(ConfigError, match="defaults: env_passthrough: DISPLAY is not set"):
            make_job(
                inherit_env=False,
                config=_config(defaults={"env_passthrough": ["DISPLAY"]}),
            )

    def test_ignored_with_inherit_env(self):
        job = make_job(env_passthrough=["DISPLAY"])  # not on the host: no error
        assert job.inherit_env is True
        assert job.env_passthrough == {}
        assert job.provenance["env_passthrough"] == (("DISPLAY", "spec"),)

    def test_merges_across_layers(self):
        cfg = _config(defaults={"env_passthrough": ["DISPLAY"]})
        host = make_host(env={"DISPLAY": ":0", "LM_LICENSE_FILE": "1@lic"})
        job = make_job(
            config=cfg, inherit_env=False, env_passthrough=["LM_LICENSE_FILE"], host=host
        )
        assert list(job.env_passthrough) == ["DISPLAY", "LM_LICENSE_FILE"]


class TestModuleExpansion:
    """$VAR in modules / modules_path is expanded from host.env and validated."""

    def test_modules_expanded(self):
        cfg = _config(types={"xrun": {"modules": ["$TEST_XCELIUM_MODULE", "vipcat/11.30"]}})
        host = make_host(env={"TEST_XCELIUM_MODULE": "xcelium/26.03.006"})
        job = make_job("xrun -f top.f", type="xrun", config=cfg, host=host)
        assert job.modules == ("xcelium/26.03.006", "vipcat/11.30")

    def test_braced_syntax(self):
        cfg = _config(defaults={"modules": ["xcelium/${TEST_XCELIUM_VER}"]})
        job = make_job(config=cfg, host=make_host(env={"TEST_XCELIUM_VER": "26.03.006"}))
        assert job.modules == ("xcelium/26.03.006",)

    def test_modules_path_expanded(self):
        cfg = _config(defaults={"modules_path": ["$TEST_MODULEFILES"]})
        job = make_job(config=cfg, host=make_host(env={"TEST_MODULEFILES": "/site/mf"}))
        assert job.modules_path == ("/site/mf",)

    def test_plain_names_unchanged(self):
        job = make_job(config=_config(defaults={"modules": ["python/3.13.3", "uv/0.7.18"]}))
        assert job.modules == ("python/3.13.3", "uv/0.7.18")

    def test_undefined_variable_raises(self):
        cfg = _config(types={"xrun": {"modules": ["$TEST_UNSET_VIPCAT_MODULE"]}})
        with pytest.raises(ConfigError, match=r"\$TEST_UNSET_VIPCAT_MODULE"):
            make_job("xrun", type="xrun", config=cfg)

    def test_empty_variable_raises(self):
        cfg = _config(types={"xrun": {"modules": ["$TEST_EMPTY_MODULE"]}})
        with pytest.raises(ConfigError, match="empty"):
            make_job("xrun", type="xrun", config=cfg, host=make_host(env={"TEST_EMPTY_MODULE": ""}))

    def test_error_reports_original_entry_and_key(self):
        cfg = _config(defaults={"modules_path": ["$TEST_UNSET_MODULE_PATH"]})
        with pytest.raises(ConfigError) as excinfo:
            make_job(config=cfg)
        msg = str(excinfo.value)
        assert "modules_path" in msg
        assert "'$TEST_UNSET_MODULE_PATH'" in msg

    def test_spec_modules_expanded(self):
        job = make_job(
            modules=["$TEST_KW_MODULE"], host=make_host(env={"TEST_KW_MODULE": "cuda/12"})
        )
        assert job.modules == ("cuda/12",)

    def test_modules_path_append_expanded(self):
        host = make_host(env={"TEST_LOCAL_MODULEFILES": "/home/me/mf"})
        job = make_job(modules_path_append=["$TEST_LOCAL_MODULEFILES"], host=host)
        assert job.modules_path_append == ("/home/me/mf",)

    def test_modules_path_append_undefined_raises(self):
        with pytest.raises(ConfigError, match="modules_path_append"):
            make_job(modules_path_append=["$TEST_UNSET_APPEND_PATH"])
