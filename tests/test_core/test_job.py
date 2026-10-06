"""Tests for Job model."""

import os
from unittest.mock import patch

import pytest

from hpc_runner.core.config import HPCConfig
from hpc_runner.core.job import Job
from hpc_runner.core.resources import ResourceSet


class TestJob:
    """Tests for Job class."""

    def test_basic_job_creation(self):
        """Test creating a basic job."""
        job = Job(command="echo hello")
        assert job.command == "echo hello"
        assert job.name is not None  # Auto-generated

    def test_job_with_list_command(self):
        """Test job with command as list."""
        job = Job(command=["python", "script.py", "--arg", "value"])
        assert job.command == "python script.py --arg value"

    def test_job_name_generation(self):
        """Test automatic job name generation."""
        job = Job(command="python script.py")
        user = os.environ.get("USER", "user")
        assert job.name.startswith(f"{user}_")
        assert "python" in job.name

    def test_job_with_explicit_name(self):
        """Test job with explicit name."""
        job = Job(command="echo test", name="my_job")
        assert job.name == "my_job"

    def test_job_with_resources(self):
        """Test job with resources."""
        job = Job(command="echo test", cpu=4, mem="16G", time="2:00:00")
        assert job.cpu == 4
        assert job.mem == "16G"
        assert job.time == "2:00:00"

    def test_job_with_queue(self):
        """Test job with queue."""
        job = Job(command="echo test", queue="gpu")
        assert job.queue == "gpu"

    def test_job_with_modules(self):
        """Test job with modules."""
        job = Job(command="python script.py", modules=["python/3.11", "cuda/12.0"])
        assert job.modules == ["python/3.11", "cuda/12.0"]

    def test_job_merge_output_default(self):
        """Test that merge_output is True by default (stderr=None)."""
        job = Job(command="echo test")
        assert job.merge_output is True
        assert job.stderr is None

    def test_job_separate_stderr(self):
        """Test job with separate stderr."""
        job = Job(command="echo test", stderr="error.log")
        assert job.merge_output is False
        assert job.stderr == "error.log"

    def test_job_with_resource_set(self):
        """Test job with ResourceSet."""
        resources = ResourceSet()
        resources.add("gpu", 2)
        resources.add("license", 1)

        job = Job(command="echo test", resources=resources)
        assert len(job.resources) == 2

    def test_job_with_raw_args(self):
        """Test job with raw scheduler arguments."""
        job = Job(command="echo test", raw_args=["-l", "scratch=10G"])
        assert job.raw_args == ["-l", "scratch=10G"]

    def test_job_with_sge_args(self):
        """Test job with SGE-specific arguments."""
        job = Job(command="echo test", sge_args=["-l", "exclusive=true"])
        assert job.sge_args == ["-l", "exclusive=true"]


class TestJobDependencies:
    """Tests for job dependencies."""

    def test_after_method(self):
        """Test after() method for adding dependencies."""
        from unittest.mock import MagicMock

        # Create mock job results
        result1 = MagicMock()
        result1.job_id = "123"
        result2 = MagicMock()
        result2.job_id = "456"

        job = Job(command="echo test")
        job.after(result1, result2, type="afterok")

        assert len(job.dependencies) == 2
        assert job.dependency_type == "afterok"

    def test_after_returns_self(self):
        """Test that after() returns the job for chaining."""
        from unittest.mock import MagicMock

        result = MagicMock()
        job = Job(command="echo test")
        returned = job.after(result)

        assert returned is job


class TestJobConfigAware:
    """Tests for config-aware Job() construction."""

    def _make_config(
        self,
        defaults: dict | None = None,
        tools: dict | None = None,
        types: dict | None = None,
    ) -> HPCConfig:
        return HPCConfig(
            defaults=defaults or {},
            tools=tools or {},
            types=types or {},
        )

    def test_defaults_applied(self):
        """[defaults] are picked up by Job()."""
        cfg = self._make_config(defaults={"cpu": 2, "mem": "8G", "queue": "batch"})
        with patch("hpc_runner.core.config.get_config", return_value=cfg):
            job = Job(command="echo hello")
        assert job.cpu == 2
        assert job.mem == "8G"
        assert job.queue == "batch"

    def test_tool_auto_detected(self):
        """Tool extracted from command is matched to [tools.*]."""
        cfg = self._make_config(
            tools={"python": {"cpu": 4, "modules": ["python/3.11"]}},
        )
        with patch("hpc_runner.core.config.get_config", return_value=cfg):
            job = Job(command="python train.py")
        assert job.cpu == 4
        assert job.modules == ["python/3.11"]

    def test_tool_with_path_stripped(self):
        """/usr/bin/python → python for tool lookup."""
        cfg = self._make_config(
            tools={"python": {"cpu": 8}},
        )
        with patch("hpc_runner.core.config.get_config", return_value=cfg):
            job = Job(command="/usr/bin/python script.py")
        assert job.cpu == 8

    def test_job_type_config_applied(self):
        """job_type= pulls [types.*] config."""
        cfg = self._make_config(
            types={"gpu": {"queue": "gpu.q", "resources": [{"name": "gpu", "value": 1}]}},
        )
        with patch("hpc_runner.core.config.get_config", return_value=cfg):
            job = Job(command="python train.py", job_type="gpu")
        assert job.queue == "gpu.q"
        assert len(job.resources) == 1

    def test_job_type_skips_tool_matching(self):
        """When job_type is given, tool auto-detect is skipped."""
        cfg = self._make_config(
            tools={"python": {"cpu": 99}},
            types={"sim": {"cpu": 2}},
        )
        with patch("hpc_runner.core.config.get_config", return_value=cfg):
            job = Job(command="python run.py", job_type="sim")
        assert job.cpu == 2  # from types.sim, not tools.python

    def test_kwargs_override_config(self):
        """Explicit kwargs beat config values."""
        cfg = self._make_config(
            defaults={"cpu": 2, "mem": "4G"},
        )
        with patch("hpc_runner.core.config.get_config", return_value=cfg):
            job = Job(command="echo hello", cpu=16, mem="64G")
        assert job.cpu == 16
        assert job.mem == "64G"

    def test_descriptor_defaults_survive(self):
        """No config + no kwargs → descriptor defaults."""
        cfg = self._make_config()
        with patch("hpc_runner.core.config.get_config", return_value=cfg):
            job = Job(command="echo hello")
        assert job.inherit_env is True
        assert job.shell == "/bin/bash"
        assert job.use_cwd is True

    def test_config_overrides_descriptor_defaults(self):
        """Config can override shell, inherit_env, use_cwd."""
        cfg = self._make_config(
            defaults={"shell": "/bin/zsh", "inherit_env": False, "use_cwd": False},
        )
        with patch("hpc_runner.core.config.get_config", return_value=cfg):
            job = Job(command="echo hello")
        assert job.shell == "/bin/zsh"
        assert job.inherit_env is False
        assert job.use_cwd is False

    def test_no_config_files_is_fine(self):
        """Works with completely empty config."""
        cfg = self._make_config()
        with patch("hpc_runner.core.config.get_config", return_value=cfg):
            job = Job(command="echo hello")
        assert job.command == "echo hello"
        assert job.name is not None

    def test_config_command_key_ignored(self):
        """A 'command' key in config is stripped — command always comes from caller."""
        cfg = self._make_config(
            defaults={"command": "should be ignored", "cpu": 2},
        )
        with patch("hpc_runner.core.config.get_config", return_value=cfg):
            job = Job(command="echo real")
        assert job.command == "echo real"
        assert job.cpu == 2

    def test_unknown_tool_gets_defaults_only(self):
        """Unrecognised tool falls back to [defaults]."""
        cfg = self._make_config(
            defaults={"cpu": 1},
            tools={"python": {"cpu": 8}},
        )
        with patch("hpc_runner.core.config.get_config", return_value=cfg):
            job = Job(command="unknown_tool arg1")
        assert job.cpu == 1


class TestJobEnvVarExpansion:
    """Tests for environment variable expansion in env_vars, env_prepend, env_append."""

    def _make_config(self, **kwargs) -> HPCConfig:
        return HPCConfig(
            defaults=kwargs.get("defaults", {}),
            tools=kwargs.get("tools", {}),
            types=kwargs.get("types", {}),
        )

    def test_env_vars_expanded(self):
        """$VAR references in env_vars are expanded from the current environment."""
        cfg = self._make_config(
            defaults={"env_vars": {"MY_HOME": "$TEST_HPC_VAR"}},
        )
        with (
            patch.dict(os.environ, {"TEST_HPC_VAR": "/some/path"}),
            patch("hpc_runner.core.config.get_config", return_value=cfg),
        ):
            job = Job(command="echo hello")
        assert job.env_vars["MY_HOME"] == "/some/path"

    def test_env_vars_braced_syntax(self):
        """${VAR} syntax is also expanded."""
        cfg = self._make_config(
            defaults={"env_vars": {"MY_HOME": "${TEST_HPC_VAR}/subdir"}},
        )
        with (
            patch.dict(os.environ, {"TEST_HPC_VAR": "/some/path"}),
            patch("hpc_runner.core.config.get_config", return_value=cfg),
        ):
            job = Job(command="echo hello")
        assert job.env_vars["MY_HOME"] == "/some/path/subdir"

    def test_env_vars_undefined_passthrough(self):
        """Undefined $VAR references are passed through literally."""
        cfg = self._make_config(
            defaults={"env_vars": {"MY_HOME": "$UNDEFINED_VAR_XYZ"}},
        )
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("hpc_runner.core.config.get_config", return_value=cfg),
        ):
            # Make sure the var is truly unset
            os.environ.pop("UNDEFINED_VAR_XYZ", None)
            job = Job(command="echo hello")
        assert job.env_vars["MY_HOME"] == "$UNDEFINED_VAR_XYZ"

    def test_env_vars_braced_undefined_passthrough(self):
        """Undefined ${VAR} references are passed through literally."""
        cfg = self._make_config(
            defaults={"env_vars": {"MY_HOME": "${UNDEFINED_VAR_XYZ}"}},
        )
        with (
            patch.dict(os.environ, {}, clear=False),
            patch("hpc_runner.core.config.get_config", return_value=cfg),
        ):
            os.environ.pop("UNDEFINED_VAR_XYZ", None)
            job = Job(command="echo hello")
        assert job.env_vars["MY_HOME"] == "${UNDEFINED_VAR_XYZ}"

    def test_env_prepend_expanded(self):
        """$VAR references in env_prepend are expanded."""
        cfg = self._make_config(
            defaults={"env_prepend": {"PATH": "$TEST_HPC_VAR/bin"}},
        )
        with (
            patch.dict(os.environ, {"TEST_HPC_VAR": "/tools"}),
            patch("hpc_runner.core.config.get_config", return_value=cfg),
        ):
            job = Job(command="echo hello")
        assert job.env_prepend["PATH"] == "/tools/bin"

    def test_env_append_expanded(self):
        """$VAR references in env_append are expanded."""
        cfg = self._make_config(
            defaults={"env_append": {"PYTHONPATH": "$TEST_HPC_VAR/lib"}},
        )
        with (
            patch.dict(os.environ, {"TEST_HPC_VAR": "/tools"}),
            patch("hpc_runner.core.config.get_config", return_value=cfg),
        ):
            job = Job(command="echo hello")
        assert job.env_append["PYTHONPATH"] == "/tools/lib"

    def test_plain_values_unchanged(self):
        """Values without $VAR references are not modified."""
        cfg = self._make_config(
            defaults={"env_vars": {"FOO": "/a/plain/path", "BAR": "literal"}},
        )
        with patch("hpc_runner.core.config.get_config", return_value=cfg):
            job = Job(command="echo hello")
        assert job.env_vars["FOO"] == "/a/plain/path"
        assert job.env_vars["BAR"] == "literal"

    def test_multiple_vars_in_one_value(self):
        """Multiple $VAR references in a single value are all expanded."""
        cfg = self._make_config(
            defaults={"env_vars": {"COMBINED": "$TEST_A/$TEST_B"}},
        )
        with (
            patch.dict(os.environ, {"TEST_A": "foo", "TEST_B": "bar"}),
            patch("hpc_runner.core.config.get_config", return_value=cfg),
        ):
            job = Job(command="echo hello")
        assert job.env_vars["COMBINED"] == "foo/bar"

    def test_mixed_defined_and_undefined(self):
        """Mix of defined and undefined vars: defined are expanded, undefined pass through."""
        cfg = self._make_config(
            defaults={"env_vars": {"MIX": "$TEST_DEFINED:$NONEXISTENT_VAR_XYZ"}},
        )
        with (
            patch.dict(os.environ, {"TEST_DEFINED": "real"}, clear=False),
            patch("hpc_runner.core.config.get_config", return_value=cfg),
        ):
            os.environ.pop("NONEXISTENT_VAR_XYZ", None)
            job = Job(command="echo hello")
        assert job.env_vars["MIX"] == "real:$NONEXISTENT_VAR_XYZ"


class TestExtraModules:
    """Tests for extra_modules and extra_modules_path append behaviour."""

    def _make_config(self, **kwargs) -> HPCConfig:
        return HPCConfig(
            defaults=kwargs.get("defaults", {}),
            tools=kwargs.get("tools", {}),
            types=kwargs.get("types", {}),
        )

    def test_extra_modules_appended_to_config(self):
        """extra_modules appends to config-defined modules."""
        cfg = self._make_config(
            tools={"python": {"modules": ["python/3.11"]}},
        )
        with patch("hpc_runner.core.config.get_config", return_value=cfg):
            job = Job(command="python train.py", extra_modules=["cuda/12.0"])
        assert job.modules == ["python/3.11", "cuda/12.0"]

    def test_extra_modules_deduplicates(self):
        """Duplicate modules are removed, preserving order."""
        cfg = self._make_config(
            tools={"python": {"modules": ["python/3.11", "numpy/1.25"]}},
        )
        with patch("hpc_runner.core.config.get_config", return_value=cfg):
            job = Job(command="python train.py", extra_modules=["python/3.11", "cuda/12.0"])
        assert job.modules == ["python/3.11", "numpy/1.25", "cuda/12.0"]

    def test_extra_modules_no_config_modules(self):
        """extra_modules works when config defines no modules."""
        cfg = self._make_config()
        with patch("hpc_runner.core.config.get_config", return_value=cfg):
            job = Job(command="echo hello", extra_modules=["gcc/13"])
        assert job.modules == ["gcc/13"]

    def test_modules_override_with_extra(self):
        """modules= overrides config; extra_modules appends to that override."""
        cfg = self._make_config(
            defaults={"modules": ["default/1.0"]},
        )
        with patch("hpc_runner.core.config.get_config", return_value=cfg):
            job = Job(
                command="echo hello",
                modules=["override/2.0"],
                extra_modules=["extra/3.0"],
            )
        assert job.modules == ["override/2.0", "extra/3.0"]

    def test_extra_modules_path_appended(self):
        """extra_modules_path appends to config-defined modules_path."""
        cfg = self._make_config(
            defaults={"modules_path": ["/opt/modules"]},
        )
        with patch("hpc_runner.core.config.get_config", return_value=cfg):
            job = Job(command="echo hello", extra_modules_path=["/my/modules"])
        assert job.modules_path == ["/opt/modules", "/my/modules"]

    def test_extra_modules_path_deduplicates(self):
        """Duplicate module paths are removed."""
        cfg = self._make_config(
            defaults={"modules_path": ["/opt/modules"]},
        )
        with patch("hpc_runner.core.config.get_config", return_value=cfg):
            job = Job(command="echo hello", extra_modules_path=["/opt/modules", "/my/modules"])
        assert job.modules_path == ["/opt/modules", "/my/modules"]

    def test_no_extras_preserves_config(self):
        """Without extra_modules, config modules are unchanged."""
        cfg = self._make_config(
            defaults={"modules": ["a", "b"]},
        )
        with patch("hpc_runner.core.config.get_config", return_value=cfg):
            job = Job(command="echo hello")
        assert job.modules == ["a", "b"]

    def test_modules_override_without_extra(self):
        """modules= alone still fully overrides config."""
        cfg = self._make_config(
            defaults={"modules": ["default/1.0"]},
        )
        with patch("hpc_runner.core.config.get_config", return_value=cfg):
            job = Job(command="echo hello", modules=["override/2.0"])
        assert job.modules == ["override/2.0"]


class TestModuleExpansion:
    """$VAR references in modules / modules_path are expanded and validated.

    Module names are resolved on the submit host so the job script never
    contains a literal ``module load $FOO`` or a bare ``module load`` — both
    are silent no-ops that leave the job with the wrong tool environment.
    """

    def _make_config(self, **kwargs) -> HPCConfig:
        return HPCConfig(
            defaults=kwargs.get("defaults", {}),
            tools=kwargs.get("tools", {}),
            types=kwargs.get("types", {}),
        )

    def test_modules_expanded(self):
        """$VAR in a module name is replaced with its value."""
        cfg = self._make_config(
            types={"xrun": {"modules": ["$TEST_XCELIUM_MODULE", "vipcat/11.30"]}},
        )
        with (
            patch.dict(os.environ, {"TEST_XCELIUM_MODULE": "xcelium/26.03.006"}),
            patch("hpc_runner.core.config.get_config", return_value=cfg),
        ):
            job = Job(command="xrun -f top.f", job_type="xrun")
        assert job.modules == ["xcelium/26.03.006", "vipcat/11.30"]

    def test_modules_braced_syntax(self):
        """${VAR} syntax, including mixed literal text, is expanded."""
        cfg = self._make_config(
            defaults={"modules": ["xcelium/${TEST_XCELIUM_VER}"]},
        )
        with (
            patch.dict(os.environ, {"TEST_XCELIUM_VER": "26.03.006"}),
            patch("hpc_runner.core.config.get_config", return_value=cfg),
        ):
            job = Job(command="echo hello")
        assert job.modules == ["xcelium/26.03.006"]

    def test_modules_path_expanded(self):
        """modules_path entries are expanded the same way."""
        cfg = self._make_config(
            defaults={"modules_path": ["$TEST_MODULEFILES"]},
        )
        with (
            patch.dict(os.environ, {"TEST_MODULEFILES": "/site/modulefiles"}),
            patch("hpc_runner.core.config.get_config", return_value=cfg),
        ):
            job = Job(command="echo hello")
        assert job.modules_path == ["/site/modulefiles"]

    def test_plain_module_names_unchanged(self):
        """Entries without $ pass through untouched."""
        cfg = self._make_config(defaults={"modules": ["python/3.13.3", "uv/0.7.18"]})
        with patch("hpc_runner.core.config.get_config", return_value=cfg):
            job = Job(command="echo hello")
        assert job.modules == ["python/3.13.3", "uv/0.7.18"]

    def test_undefined_variable_raises(self):
        """An unset variable is a ConfigError that names the variable."""
        from hpc_runner.core.exceptions import ConfigError

        cfg = self._make_config(types={"xrun": {"modules": ["$TEST_UNSET_VIPCAT_MODULE"]}})
        env = {k: v for k, v in os.environ.items() if k != "TEST_UNSET_VIPCAT_MODULE"}
        with (
            patch.dict(os.environ, env, clear=True),
            patch("hpc_runner.core.config.get_config", return_value=cfg),
            pytest.raises(ConfigError, match=r"\$TEST_UNSET_VIPCAT_MODULE"),
        ):
            Job(command="xrun", job_type="xrun")

    def test_empty_variable_raises(self):
        """A variable that is set but empty is also a ConfigError."""
        from hpc_runner.core.exceptions import ConfigError

        cfg = self._make_config(types={"xrun": {"modules": ["$TEST_EMPTY_MODULE"]}})
        with (
            patch.dict(os.environ, {"TEST_EMPTY_MODULE": ""}),
            patch("hpc_runner.core.config.get_config", return_value=cfg),
            pytest.raises(ConfigError, match="empty"),
        ):
            Job(command="xrun", job_type="xrun")

    def test_error_reports_original_entry_and_key(self):
        """The message shows the raw config entry and which list it came from."""
        from hpc_runner.core.exceptions import ConfigError

        cfg = self._make_config(defaults={"modules_path": ["$TEST_UNSET_MODULE_PATH"]})
        env = {k: v for k, v in os.environ.items() if k != "TEST_UNSET_MODULE_PATH"}
        with (
            patch.dict(os.environ, env, clear=True),
            patch("hpc_runner.core.config.get_config", return_value=cfg),
            pytest.raises(ConfigError) as excinfo,
        ):
            Job(command="echo hello")
        msg = str(excinfo.value)
        assert "modules_path" in msg
        assert "'$TEST_UNSET_MODULE_PATH'" in msg

    def test_extra_modules_also_expanded(self):
        """Modules appended via extra_modules go through the same expansion."""
        cfg = self._make_config(defaults={"modules": ["python/3.13.3"]})
        with (
            patch.dict(os.environ, {"TEST_EXTRA_MODULE": "gcc/13"}),
            patch("hpc_runner.core.config.get_config", return_value=cfg),
        ):
            job = Job(command="echo hello", extra_modules=["$TEST_EXTRA_MODULE"])
        assert job.modules == ["python/3.13.3", "gcc/13"]

    def test_explicit_modules_kwarg_expanded(self):
        """modules= passed directly to Job() is expanded too."""
        with patch.dict(os.environ, {"TEST_KW_MODULE": "cuda/12.0"}):
            job = Job(command="echo hello", modules=["$TEST_KW_MODULE"])
        assert job.modules == ["cuda/12.0"]


class TestModulesPathAppend:
    """modules_path_append renders as ``module use --append`` and merges like modules_path."""

    def _make_config(self, **kwargs) -> HPCConfig:
        return HPCConfig(
            defaults=kwargs.get("defaults", {}),
            tools=kwargs.get("tools", {}),
            types=kwargs.get("types", {}),
        )

    def test_default_is_empty(self):
        job = Job(command="echo hello")
        assert job.modules_path_append == []

    def test_kwarg(self):
        job = Job(command="echo hello", modules_path_append=["/my/modulefiles"])
        assert job.modules_path_append == ["/my/modulefiles"]
        assert job.modules_path == []

    def test_from_config(self):
        cfg = self._make_config(defaults={"modules_path_append": ["/site/local"]})
        with patch("hpc_runner.core.config.get_config", return_value=cfg):
            job = Job(command="echo hello")
        assert job.modules_path_append == ["/site/local"]

    def test_kwarg_overrides_config(self):
        cfg = self._make_config(defaults={"modules_path_append": ["/site/local"]})
        with patch("hpc_runner.core.config.get_config", return_value=cfg):
            job = Job(command="echo hello", modules_path_append=["/override"])
        assert job.modules_path_append == ["/override"]

    def test_extra_appended_and_deduplicated(self):
        cfg = self._make_config(defaults={"modules_path_append": ["/site/local"]})
        with patch("hpc_runner.core.config.get_config", return_value=cfg):
            job = Job(
                command="echo hello",
                extra_modules_path_append=["/site/local", "/mine"],
            )
        assert job.modules_path_append == ["/site/local", "/mine"]

    def test_type_config_merges_with_defaults(self):
        cfg = self._make_config(
            defaults={"modules_path_append": ["/site/local"]},
            types={"xrun": {"modules_path_append": ["/proj/modulefiles"]}},
        )
        with patch("hpc_runner.core.config.get_config", return_value=cfg):
            job = Job(command="xrun", job_type="xrun")
        assert job.modules_path_append == ["/site/local", "/proj/modulefiles"]

    def test_env_var_expanded(self):
        with patch.dict(os.environ, {"TEST_LOCAL_MODULEFILES": "/home/me/modulefiles"}):
            job = Job(command="echo hello", modules_path_append=["$TEST_LOCAL_MODULEFILES"])
        assert job.modules_path_append == ["/home/me/modulefiles"]

    def test_undefined_env_var_raises(self):
        from hpc_runner.core.exceptions import ConfigError

        env = {k: v for k, v in os.environ.items() if k != "TEST_UNSET_APPEND_PATH"}
        with (
            patch.dict(os.environ, env, clear=True),
            pytest.raises(ConfigError, match="modules_path_append"),
        ):
            Job(command="echo hello", modules_path_append=["$TEST_UNSET_APPEND_PATH"])
