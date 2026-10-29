"""Tests for configuration system."""

import dataclasses
import logging
import os
from pathlib import Path

import pytest

from hpc_runner.core.config import (
    HPC_CONFIG_ENV_VAR,
    HPCConfig,
    _match_contiguous,
    _merge,
    _normalise_tokens,
    _resolve_extends,
    check_config,
    find_config_file,
    find_config_files,
    load_config,
)
from hpc_runner.core.exceptions import ConfigError
from hpc_runner.core.merge import Edit, Set, merge_list
from hpc_runner.core.schema import FIELD_VALIDATORS, PER_INVOCATION_FIELDS, find_line
from hpc_runner.core.spec import JobSpec
from hpc_runner.core.units import Memory
from hpc_runner.schedulers import scheduler_config_keys
from tests.fakes import make_job


def _layer_names(config: HPCConfig, command: str, type: str | None = None) -> list[str]:
    return [name for name, _ in config.layers_for(JobSpec(command, type=type))]


class TestHPCConfig:
    """Tests for HPCConfig class."""

    def test_empty_config(self):
        """Test empty configuration."""
        config = HPCConfig()
        assert config.defaults == {}
        assert config.tools == {}
        assert config.types == {}
        assert config.warnings == []

    def test_unknown_tool_gets_defaults_only(self):
        config = HPCConfig(defaults={"cpu": 2, "mem": "8G"})
        assert _layer_names(config, "unknown_tool") == ["defaults"]
        job = make_job("unknown_tool", config=config)
        assert (job.cpu, job.mem) == (2, Memory.parse("8G"))

    def test_tool_layer(self):
        config = HPCConfig(
            defaults={"cpu": 1, "mem": "4G"},
            tools={"python": {"cpu": 4, "modules": ["python/3.11"]}},
        )
        assert _layer_names(config, "python script.py") == ["defaults", "tools.python"]
        job = make_job("python script.py", config=config)
        assert job.cpu == 4  # Overridden
        assert job.mem == Memory.parse("4G")  # From defaults
        assert job.modules == ("python/3.11",)

    def test_type_layer(self):
        config = HPCConfig(
            defaults={"cpu": 1},
            types={"gpu": {"queue": "gpu", "resources": [{"name": "gpu", "value": 1}]}},
        )
        assert _layer_names(config, "anything", type="gpu") == ["defaults", "types.gpu"]
        job = make_job("anything", type="gpu", config=config)
        assert job.cpu == 1  # From defaults
        assert job.queue == "gpu"

    def test_type_name_not_matched_as_tool(self):
        """A type name in argv[0] is not a tool."""
        config = HPCConfig(
            defaults={"cpu": 1},
            tools={"python": {"cpu": 4}},
            types={"gpu": {"queue": "gpu"}},
        )
        assert _layer_names(config, "gpu") == ["defaults"]

    def test_tool_name_is_not_a_type(self):
        """A tool name given as a type is an unknown type."""
        config = HPCConfig(tools={"python": {"cpu": 4}}, types={"gpu": {"queue": "gpu"}})
        with pytest.raises(ConfigError, match="unknown job type 'python'"):
            config.layers_for(JobSpec("x", type="python"))

    def test_tool_extracted_from_command(self):
        config = HPCConfig(tools={"python": {"cpu": 4}})
        assert _layer_names(config, "python script.py --arg value") == [
            "defaults",
            "tools.python",
        ]

    def test_list_command(self):
        config = HPCConfig(tools={"python": {"cpu": 4}})
        layers = config.layers_for(JobSpec(["python", "a b.py"]))
        assert [name for name, _ in layers] == ["defaults", "tools.python"]

    def test_get_scheduler_config(self):
        """Test getting scheduler-specific config."""
        config = HPCConfig(
            schedulers={"sge": {"parallel_environment": "mpi"}},
        )
        sge_config = config.get_scheduler_config("sge")

        assert sge_config["parallel_environment"] == "mpi"

    def test_get_scheduler_config_missing(self):
        """Test getting config for missing scheduler."""
        config = HPCConfig()
        sge_config = config.get_scheduler_config("sge")

        assert sge_config == {}


class TestMerge:
    """_merge: one file's sections over an earlier file's (D1 for lists)."""

    def test_list_merge_preserves_order(self):
        """Module load order matters on HPC - merge must preserve it."""
        base = {"modules": ["gcc/12", "openmpi/4", "python/3.11"]}
        override = {"modules": ["cuda/12", "python/3.11"]}
        result = _merge(base, override)

        # python/3.11 appears in both - should be deduplicated
        assert result["modules"].count("python/3.11") == 1
        # Order from base should come first, then new items from override
        assert result["modules"] == ["gcc/12", "openmpi/4", "python/3.11", "cuda/12"]

    def test_list_set_replaces(self):
        base = {"modules": ["gcc/12", "openmpi/4"]}
        override = {"modules": Set(["intel/2024", "impi/2024"])}
        result = _merge(base, override)

        assert result["modules"] == Set(["intel/2024", "impi/2024"])

    def test_list_edit_composes(self):
        """An earlier file's list then a later file's edit is one edit."""
        base = {"modules": ["gcc/12", "python/3.11"]}
        override = {"modules": Edit(remove=["python/3.11"], add=["python/3.12"])}
        result = _merge(base, override)

        assert merge_list(["site/1", "python/3.11"], result["modules"]) == [
            "site/1",
            "gcc/12",
            "python/3.12",
        ]

    def test_list_merge_no_duplicates_from_base(self):
        """An item in both files appears once."""
        base = {"env_unset": ["-v"]}
        override = {"env_unset": ["-v", "-x"]}
        result = _merge(base, override)

        assert result["env_unset"] == ["-v", "-x"]

    def test_resources_merge(self):
        """B2: two files both defining resources merge by name, later wins."""
        base = {"resources": [{"name": "gpu", "value": 1}, {"name": "scratch", "value": "1G"}]}
        override = {"resources": [{"name": "gpu", "value": 2}]}
        result = _merge(base, override)

        assert result["resources"] == [
            {"name": "gpu", "value": 2},
            {"name": "scratch", "value": "1G"},
        ]

    def test_dict_merge_deep(self):
        """Nested dicts should merge recursively."""
        base = {"schedulers": {"sge": {"pe": "smp", "mem": "mem_free"}}}
        override = {"schedulers": {"sge": {"pe": "mpi"}}}
        result = _merge(base, override)

        assert result["schedulers"]["sge"]["pe"] == "mpi"
        assert result["schedulers"]["sge"]["mem"] == "mem_free"

    def test_scalar_override(self):
        """Scalars in override should replace base."""
        base = {"cpu": 1, "mem": "4G"}
        override = {"cpu": 8}
        result = _merge(base, override)

        assert result["cpu"] == 8
        assert result["mem"] == "4G"

    def test_new_keys_added(self):
        """Keys only in override should be added."""
        base = {"cpu": 1}
        override = {"queue": "batch"}
        result = _merge(base, override)

        assert result["cpu"] == 1
        assert result["queue"] == "batch"


class TestLoadConfig:
    """Tests for config loading."""

    def test_load_config_from_file(self, sample_config):
        """Test loading config from a file."""
        config = load_config(sample_config)

        assert config.defaults["cpu"] == 2
        assert config.defaults["mem"] == "8G"
        assert config.schedulers["sge"]["parallel_environment"] == "mpi"

    def test_load_config_raises_on_invalid_toml(self, temp_dir):
        """Invalid TOML must raise ConfigError, not be silently dropped."""
        bad_config = temp_dir / "hpc-runner.toml"
        # Bareword value (e.g. `mem = 16G`) is invalid TOML
        bad_config.write_text("[tools.cmake]\ncpu = 16\nmem = 16G\n")

        with pytest.raises(ConfigError, match="invalid TOML"):
            load_config(bad_config)

    def test_load_config_raises_on_unreadable_path(self, temp_dir):
        """OSError (e.g. path is a directory) must raise ConfigError."""
        # Pass a directory where a file is expected — open() raises IsADirectoryError
        with pytest.raises(ConfigError, match="could not read file"):
            load_config(temp_dir)

    def test_load_config_returns_empty_when_no_config(self, temp_dir):
        """Test that loading returns empty config when no config files found."""
        # Change to temp dir where there's no user config
        old_cwd = os.getcwd()
        os.chdir(temp_dir)
        try:
            config = load_config()
            # No package defaults - should return empty config
            assert config.defaults == {}
            assert config.tools == {}
            assert config.types == {}
            assert config.schedulers == {}
        finally:
            os.chdir(old_cwd)


class TestFindConfigFile:
    """Tests for config file discovery."""

    def test_find_config_in_current_dir(self, temp_dir):
        """Test finding config in current directory."""
        config_file = temp_dir / "hpc-runner.toml"
        config_file.write_text("config_version = 1\n[defaults]\ncpu = 1\n")

        old_cwd = os.getcwd()
        os.chdir(temp_dir)
        try:
            found = find_config_file()
            assert found == config_file
        finally:
            os.chdir(old_cwd)

    def test_pyproject_toml_is_ignored(self, temp_dir):
        """Test that pyproject.toml is ignored even with [tool.hpc-runner]."""
        pyproject = temp_dir / "pyproject.toml"
        pyproject.write_text("[tool.hpc-runner]\n[tool.hpc-runner.defaults]\ncpu = 1\n")

        old_cwd = os.getcwd()
        os.chdir(temp_dir)
        try:
            found = find_config_file()
            assert found is None
        finally:
            os.chdir(old_cwd)


class TestExtendsExpansion:
    """`extends` follows the D5 rule: $VAR from the environment, ${CONFIG_DIR}."""

    def test_env_var(self, tmp_path, monkeypatch):
        (tmp_path / "base.toml").write_text("config_version = 1\n")
        child = tmp_path / "child.toml"
        child.write_text('config_version = 1\nextends = "${TEST_HPC_BASE}/base.toml"\n')
        monkeypatch.setenv("TEST_HPC_BASE", str(tmp_path))
        assert _resolve_extends(child) == [(tmp_path / "base.toml").resolve(), child.resolve()]

    def test_config_dir(self, tmp_path):
        (tmp_path / "base.toml").write_text("config_version = 1\n")
        child = tmp_path / "child.toml"
        child.write_text('config_version = 1\nextends = "${CONFIG_DIR}/base.toml"\n')
        assert _resolve_extends(child)[0] == (tmp_path / "base.toml").resolve()

    def test_undefined_is_error(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TEST_HPC_UNSET", raising=False)
        child = tmp_path / "child.toml"
        child.write_text('config_version = 1\nextends = "$TEST_HPC_UNSET/base.toml"\n')
        with pytest.raises(ConfigError, match=r"undefined variable \$TEST_HPC_UNSET") as info:
            _resolve_extends(child)
        assert info.value.line == 2


class TestExtendsResolution:
    """Tests for extends chain resolution."""

    def test_resolve_extends_single(self, temp_dir):
        """Test resolving a single extends."""
        base_config = temp_dir / "base.toml"
        base_config.write_text("config_version = 1\n[defaults]\ncpu = 1\n")

        child_config = temp_dir / "child.toml"
        child_config.write_text(
            f'config_version = 1\nextends = "{base_config}"\n[defaults]\nmem = "8G"\n'
        )

        chain = _resolve_extends(child_config)

        assert len(chain) == 2
        assert chain[0] == base_config.resolve()
        assert chain[1] == child_config.resolve()

    def test_resolve_extends_relative_path(self, temp_dir):
        """Test resolving extends with relative path."""
        base_config = temp_dir / "base.toml"
        base_config.write_text("config_version = 1\n[defaults]\ncpu = 1\n")

        child_config = temp_dir / "child.toml"
        child_config.write_text(
            'config_version = 1\nextends = "base.toml"\n[defaults]\nmem = "8G"\n'
        )

        chain = _resolve_extends(child_config)

        assert len(chain) == 2
        assert chain[0] == base_config.resolve()

    def test_resolve_extends_chain(self, temp_dir):
        """Test resolving a chain of extends."""
        grandparent = temp_dir / "grandparent.toml"
        grandparent.write_text("config_version = 1\n[defaults]\ncpu = 1\n")

        parent = temp_dir / "parent.toml"
        parent.write_text(
            'config_version = 1\nextends = "grandparent.toml"\n[defaults]\nmem = "4G"\n'
        )

        child = temp_dir / "child.toml"
        child.write_text(
            'config_version = 1\nextends = "parent.toml"\n[defaults]\ntime = "1:00:00"\n'
        )

        chain = _resolve_extends(child)

        assert len(chain) == 3
        assert chain[0] == grandparent.resolve()
        assert chain[1] == parent.resolve()
        assert chain[2] == child.resolve()

    def test_resolve_extends_circular_detected(self, temp_dir):
        """Test that circular extends are detected."""
        config_a = temp_dir / "a.toml"
        config_b = temp_dir / "b.toml"

        config_a.write_text('config_version = 1\nextends = "b.toml"\n[defaults]\ncpu = 1\n')
        config_b.write_text('config_version = 1\nextends = "a.toml"\n[defaults]\nmem = "4G"\n')

        with pytest.raises(ConfigError, match="extends cycle"):
            _resolve_extends(config_a)

    def test_resolve_extends_with_env_var(self, temp_dir):
        """Test resolving extends with env var in path."""
        base_config = temp_dir / "base.toml"
        base_config.write_text("config_version = 1\n[defaults]\ncpu = 1\n")

        os.environ["TEST_CONFIG_DIR"] = str(temp_dir)
        try:
            child_config = temp_dir / "child.toml"
            child_config.write_text(
                "config_version = 1\n"
                'extends = "${TEST_CONFIG_DIR}/base.toml"\n'
                '[defaults]\nmem = "8G"\n'
            )

            chain = _resolve_extends(child_config)

            assert len(chain) == 2
            assert chain[0] == base_config.resolve()
        finally:
            del os.environ["TEST_CONFIG_DIR"]


class TestConfigMerging:
    """Tests for merging multiple config files."""

    def test_load_config_merges_extends(self, temp_dir):
        """Test that extends configs are merged correctly."""
        base_config = temp_dir / "base.toml"
        base_config.write_text(
            'config_version = 1\n[defaults]\ncpu = 1\nmem = "4G"\n'
            '[schedulers.sge]\nparallel_environment = "smp"\n'
        )

        child_config = temp_dir / "child.toml"
        child_config.write_text(
            "config_version = 1\n"
            f'extends = "{base_config}"\n'
            "[defaults]\ncpu = 4\n"
            '[tools.python]\nmodules = ["python/3.11"]\n'
        )

        config = load_config(child_config)

        # cpu overridden by child
        assert config.defaults["cpu"] == 4
        # mem inherited from base
        assert config.defaults["mem"] == "4G"
        # scheduler from base
        assert config.schedulers["sge"]["parallel_environment"] == "smp"
        # tools from child
        assert config.tools["python"]["modules"] == ["python/3.11"]

    def test_find_config_files_from_env_var(self, temp_dir):
        """Test that HPC_RUNNER_CONFIG env var replaces git root (mutually exclusive)."""
        # Create a fake git root with its own config
        git_dir = temp_dir / ".git"
        git_dir.mkdir()
        git_config = temp_dir / "hpc-runner.toml"
        git_config.write_text("config_version = 1\n[defaults]\ncpu = 1\n")

        # Create env var config in a different location
        env_config = temp_dir / "site" / "site.toml"
        env_config.parent.mkdir()
        env_config.write_text("config_version = 1\n[defaults]\ncpu = 2\n")

        # chdir to a subdirectory without hpc-runner.toml so Level 2 doesn't
        # pick up git_config as a cwd override
        subdir = temp_dir / "subdir"
        subdir.mkdir()

        os.environ[HPC_CONFIG_ENV_VAR] = str(env_config)
        old_cwd = os.getcwd()
        os.chdir(subdir)
        try:
            configs = find_config_files()
            # env var config should be used
            assert env_config.resolve() in configs
            # git root config should NOT be included (env var replaces it)
            assert git_config.resolve() not in configs
        finally:
            del os.environ[HPC_CONFIG_ENV_VAR]
            os.chdir(old_cwd)

    def test_env_var_with_cwd_override(self, temp_dir):
        """Test two-level model: env var config + cwd local override."""
        # Create env var config
        env_config = temp_dir / "site" / "site.toml"
        env_config.parent.mkdir()
        env_config.write_text("config_version = 1\n[defaults]\ncpu = 2\n")

        # Create a local override in a subdirectory
        subdir = temp_dir / "project"
        subdir.mkdir()
        local_config = subdir / "hpc-runner.toml"
        local_config.write_text('config_version = 1\n[defaults]\nmem = "16G"\n')

        os.environ[HPC_CONFIG_ENV_VAR] = str(env_config)
        old_cwd = os.getcwd()
        os.chdir(subdir)
        try:
            configs = find_config_files()
            # Both configs should be present
            assert env_config.resolve() in configs
            assert local_config.resolve() in configs
            # env var config should come first (lower priority)
            assert configs.index(env_config.resolve()) < configs.index(local_config.resolve())
        finally:
            del os.environ[HPC_CONFIG_ENV_VAR]
            os.chdir(old_cwd)

    def test_config_tracks_source_paths(self, temp_dir):
        """Test that loaded config tracks its source paths."""
        config_file = temp_dir / "hpc-runner.toml"
        config_file.write_text("config_version = 1\n[defaults]\ncpu = 1\n")

        config = load_config(config_file)

        assert len(config._source_paths) == 1
        assert config._source_paths[0] == config_file.resolve()


def _from_config(config: HPCConfig, command: str) -> dict:
    """The job fields config supplied for *command*, as a dict."""
    job = make_job(command, config=config)
    result = {}
    for field, source in job.provenance.items():
        if isinstance(source, tuple):
            if source:
                result[field] = [item for item, _ in source]
        elif source not in ("spec", "package", "host"):
            result[field] = getattr(job, field)
    return result


class TestToolOptionMatching:
    """Tests for tool option matching."""

    def test_no_options_unchanged(self):
        """Tool without options works as before."""
        config = HPCConfig(
            defaults={"cpu": 1},
            tools={"python": {"cpu": 4}},
        )
        result = _from_config(config, "python script.py")
        assert result["cpu"] == 4

    def test_flag_match(self):
        """Boolean flag option matches."""
        config = HPCConfig(
            defaults={"cpu": 1},
            tools={
                "mytool": {
                    "cpu": 2,
                    "options": {
                        "--gui": {"queue": "interactive.q"},
                    },
                }
            },
        )
        result = _from_config(config, "mytool --gui input.dat")
        assert result["cpu"] == 2
        assert result["queue"] == "interactive.q"

    def test_flag_value_match(self):
        """Flag with value option matches."""
        config = HPCConfig(
            defaults={"cpu": 1},
            tools={
                "fusesoc": {
                    "modules": ["fusesoc/2.0"],
                    "options": {
                        "--tool slang": {
                            "mem": "16G",
                            "modules": ["slang/0.9"],
                        },
                    },
                }
            },
        )
        result = _from_config(config, "fusesoc run --target lint --tool slang v:l:n:v")
        assert result["mem"] == Memory.parse("16G")
        assert "slang/0.9" in result["modules"]
        assert "fusesoc/2.0" in result["modules"]

    def test_equals_form_normalised(self):
        """--tool=slang matches option key '--tool slang'."""
        config = HPCConfig(
            defaults={},
            tools={
                "fusesoc": {
                    "options": {
                        "--tool slang": {"mem": "16G"},
                    },
                }
            },
        )
        result = _from_config(config, "fusesoc run --tool=slang")
        assert result["mem"] == Memory.parse("16G")

    def test_no_partial_match(self):
        """'--tool slang' must not match '--tool slang-lint'."""
        config = HPCConfig(
            defaults={},
            tools={
                "fusesoc": {
                    "options": {
                        "--tool slang": {"mem": "16G"},
                    },
                }
            },
        )
        result = _from_config(config, "fusesoc run --tool slang-lint")
        assert "mem" not in result

    def test_no_match_uses_base(self):
        """When no option matches, base tool config is returned."""
        config = HPCConfig(
            defaults={"cpu": 1},
            tools={
                "mytool": {
                    "cpu": 2,
                    "options": {
                        "--gui": {"queue": "interactive.q"},
                    },
                }
            },
        )
        result = _from_config(config, "mytool --batch input.dat")
        assert result["cpu"] == 2
        assert "queue" not in result

    def test_first_match_wins(self):
        """First matching option key wins."""
        config = HPCConfig(
            defaults={},
            tools={
                "fusesoc": {
                    "options": {
                        "--tool slang": {"mem": "16G"},
                        "--tool": {"mem": "8G"},
                    },
                }
            },
        )
        result = _from_config(config, "fusesoc run --tool slang")
        assert result["mem"] == Memory.parse("16G")

    def test_option_merges_with_base(self):
        """Option config merges on top of base, not replaces."""
        config = HPCConfig(
            defaults={"cpu": 1},
            tools={
                "fusesoc": {
                    "cpu": 2,
                    "modules": ["fusesoc/2.0"],
                    "options": {
                        "--tool slang": {"mem": "16G"},
                    },
                }
            },
        )
        result = _from_config(config, "fusesoc run --tool slang")
        assert result["cpu"] == 2  # Inherited from base
        assert result["mem"] == Memory.parse("16G")  # From option

    def test_options_key_excluded_from_base(self):
        """The 'options' key itself must not leak into job config."""
        config = HPCConfig(
            defaults={},
            tools={
                "mytool": {
                    "cpu": 2,
                    "options": {
                        "--gui": {"queue": "interactive.q"},
                    },
                }
            },
        )
        layers = config.layers_for(JobSpec("mytool run"))
        assert all("options" not in table for _, table in layers)

    def test_short_flag_match(self):
        """Short flags match literally — no magic expansion."""
        config = HPCConfig(
            defaults={},
            tools={
                "mytool": {
                    "options": {
                        "-g": {"queue": "interactive.q"},
                    },
                }
            },
        )
        result = _from_config(config, "mytool -g input.dat")
        assert result["queue"] == "interactive.q"

    def test_command_with_path(self):
        """Tool name extraction strips path."""
        config = HPCConfig(
            defaults={},
            tools={
                "mytool": {
                    "cpu": 4,
                    "options": {
                        "--gui": {"queue": "interactive.q"},
                    },
                }
            },
        )
        result = _from_config(config, "/usr/local/bin/mytool --gui")
        assert result["cpu"] == 4
        assert result["queue"] == "interactive.q"


class TestNormaliseTokens:
    """Tests for _normalise_tokens."""

    def test_simple(self):
        assert _normalise_tokens("--tool slang") == ["--tool", "slang"]

    def test_equals_split(self):
        assert _normalise_tokens("--tool=slang") == ["--tool", "slang"]

    def test_mixed(self):
        assert _normalise_tokens("--target lint --tool=slang -v") == [
            "--target",
            "lint",
            "--tool",
            "slang",
            "-v",
        ]

    def test_short_flag_untouched(self):
        assert _normalise_tokens("-t slang") == ["-t", "slang"]

    def test_list_input(self):
        assert _normalise_tokens(["--tool=slang", "-v"]) == [
            "--tool",
            "slang",
            "-v",
        ]


class TestMatchContiguous:
    """Tests for _match_contiguous."""

    def test_match(self):
        assert _match_contiguous(["a", "b", "c", "d"], ["b", "c"]) is True

    def test_no_match(self):
        assert _match_contiguous(["a", "b", "c", "d"], ["b", "d"]) is False

    def test_single_token(self):
        assert _match_contiguous(["--gui", "file"], ["--gui"]) is True

    def test_empty_needle(self):
        assert _match_contiguous(["a", "b"], []) is False

    def test_needle_longer_than_haystack(self):
        assert _match_contiguous(["a"], ["a", "b"]) is False

    def test_at_end(self):
        assert _match_contiguous(["a", "b", "c"], ["b", "c"]) is True

    def test_at_start(self):
        assert _match_contiguous(["a", "b", "c"], ["a", "b"]) is True


def _write(tmp_path, text, name="hpc-runner.toml"):
    path = tmp_path / name
    path.write_text(text)
    return path


def _errors(path):
    """Every ConfigError load_config reports for *path*."""
    with pytest.raises(ConfigError) as info:
        load_config(path)
    return info.value.errors


class TestConfigVersion:
    def test_missing_is_error(self, tmp_path):
        path = _write(tmp_path, "[defaults]\ncpu = 1\n")
        (err,) = _errors(path)
        assert str(err) == (
            f"{path}: missing config_version; add `config_version = 1` as the first line"
        )

    def test_unknown_version_is_error(self, tmp_path):
        path = _write(tmp_path, "config_version = 2\n")
        (err,) = _errors(path)
        assert err.line == 1
        assert "unsupported version 2" in str(err)

    def test_required_in_extends_parent(self, tmp_path):
        _write(tmp_path, "[defaults]\ncpu = 1\n", name="base.toml")
        child = _write(tmp_path, 'config_version = 1\nextends = "base.toml"\n')
        (err,) = _errors(child)
        assert err.path == (tmp_path / "base.toml").resolve()
        assert "missing config_version" in str(err)


class TestExtendsErrors:
    def test_missing_file_is_error(self, tmp_path):
        path = _write(tmp_path, 'config_version = 1\n\nextends = "nope.toml"\n')
        with pytest.raises(ConfigError, match="extends: file not found") as info:
            load_config(path)
        assert info.value.line == 3

    def test_cycle_is_config_error(self, tmp_path):
        _write(tmp_path, 'config_version = 1\nextends = "b.toml"\n', name="a.toml")
        _write(tmp_path, 'config_version = 1\nextends = "a.toml"\n', name="b.toml")
        with pytest.raises(ConfigError, match="extends cycle"):
            load_config(tmp_path / "a.toml")


class TestSchema:
    def test_wrong_type_names_file_line_section_key(self, tmp_path):
        path = _write(tmp_path, 'config_version = 1\n\n[defaults]\nmem = "4G"\ncpu = "4"\n')
        (err,) = _errors(path)
        assert (err.path, err.line) == (path, 5)
        assert str(err) == f'{path}:5: [defaults] cpu: expected an integer, got string "4"'

    def test_string_for_list_is_error(self, tmp_path):
        path = _write(tmp_path, 'config_version = 1\n[tools.gcc]\nmodules = "gcc/12"\n')
        (err,) = _errors(path)
        assert err.line == 3
        assert "[tools.gcc] modules: expected a list of strings" in str(err)

    def test_unknown_key_did_you_mean(self, tmp_path):
        path = _write(tmp_path, 'config_version = 1\n[types.sim]\nmodles = ["a"]\n')
        (err,) = _errors(path)
        assert err.line == 3
        assert "[types.sim] modles: unknown key; did you mean 'modules'?" in str(err)

    def test_all_errors_reported_together(self, tmp_path):
        path = _write(
            tmp_path,
            'config_version = 1\n[defaults]\ncpu = "4"\nmem = 16\n[tools.x]\nmodles = []\n',
        )
        with pytest.raises(ConfigError) as info:
            load_config(path)
        errors = info.value.errors
        assert [e.line for e in errors] == [3, 4, 6]
        assert str(info.value).splitlines() == [str(e) for e in errors]

    def test_bool_is_not_an_int(self, tmp_path):
        path = _write(tmp_path, "config_version = 1\n[defaults]\ncpu = true\n")
        (err,) = _errors(path)
        assert "expected an integer, got boolean true" in str(err)

    def test_per_invocation_fields_rejected(self, tmp_path):
        path = _write(tmp_path, 'config_version = 1\n[types.sweep]\narray = "1-4"\n')
        (err,) = _errors(path)
        assert "[types.sweep] array: unknown key" in str(err)

    def test_unknown_top_level_key(self, tmp_path):
        path = _write(tmp_path, "config_version = 1\n[default]\ncpu = 1\n")
        (err,) = _errors(path)
        assert "default: unknown key; did you mean 'defaults'?" in str(err)

    def test_tools_under_tool_rejected(self, tmp_path):
        """Tools do not reference tools (D2)."""
        path = _write(tmp_path, 'config_version = 1\n[tools.a]\ntools = ["b"]\n[tools.b]\n')
        (err,) = _errors(path)
        assert "[tools.a] tools: unknown key" in str(err)

    def test_options_under_type_rejected(self, tmp_path):
        path = _write(tmp_path, 'config_version = 1\n[types.t.options."--gui"]\ncpu = 1\n')
        (err,) = _errors(path)
        assert "[types.t] options: unknown key" in str(err)
        assert err.line == 2

    def test_option_layer_validated(self, tmp_path):
        path = _write(tmp_path, 'config_version = 1\n[tools.f.options."--tool slang"]\nmem = 16\n')
        (err,) = _errors(path)
        assert err.line == 3
        assert '[tools.f.options."--tool slang"] mem: expected a string' in str(err)

    def test_dict_value_located(self, tmp_path):
        path = _write(tmp_path, "config_version = 1\n[defaults.env_vars]\nA = 'x'\nB = 1\n")
        (err,) = _errors(path)
        assert err.line == 4
        assert "[defaults] env_vars.B: expected a string" in str(err)

    def test_resource_entry_checked(self, tmp_path):
        path = _write(
            tmp_path, 'config_version = 1\n[defaults]\nresources = [{ name = "gpu", val = 1 }]\n'
        )
        (err,) = _errors(path)
        assert "entry 1: unknown key 'val'" in str(err)

    def test_venv_accepts_path_or_false(self, tmp_path):
        ok = _write(
            tmp_path, 'config_version = 1\n[defaults]\nvenv = false\n[tools.a]\nvenv = "/v"\n'
        )
        config = load_config(ok)
        assert config.defaults["venv"] is False
        bad = _write(tmp_path, "config_version = 1\n[defaults]\nvenv = true\n", name="b.toml")
        (err,) = _errors(bad)
        assert "expected a path or false" in str(err)

    def test_env_set_and_prepend_same_layer_is_error(self, tmp_path):
        """D5.6: env_vars and env_prepend/env_append on one key in one layer."""
        path = _write(
            tmp_path,
            "config_version = 1\n[tools.a]\n"
            'env_vars = { PATH = "/a" }\nenv_append = { PATH = "/b" }\n',
        )
        (err,) = _errors(path)
        assert "[tools.a] env_append.PATH: PATH is also set by env_vars" in str(err)
        assert err.line == 4

    def test_env_set_and_prepend_across_layers_allowed(self, tmp_path):
        path = _write(
            tmp_path,
            "config_version = 1\n"
            '[defaults.env_vars]\nPATH = "/a"\n[tools.a.env_prepend]\nPATH = "/b"\n',
        )
        assert load_config(path).tools["a"]["env_prepend"] == {"PATH": "/b"}

    @pytest.mark.parametrize(
        "line",
        [
            'env_vars = { "A B" = "x" }',
            'env_prepend = { "1A" = "x" }',
            'env_unset = ["a;b"]',
            'env_passthrough = ["$DISPLAY"]',
        ],
    )
    def test_env_names_must_be_identifiers(self, tmp_path, line):
        path = _write(tmp_path, f"config_version = 1\n[defaults]\n{line}\n")
        (err,) = _errors(path)
        assert "not a valid environment variable name" in str(err)

    def test_env_passthrough_is_a_d1_list(self, tmp_path):
        path = _write(
            tmp_path,
            'config_version = 1\n[defaults]\nenv_passthrough = ["DISPLAY"]\n'
            '[types.t]\nenv_passthrough = { set = ["LM_LICENSE_FILE"] }\n',
        )
        config = load_config(path)
        assert config.defaults["env_passthrough"] == ["DISPLAY"]
        assert config.types["t"]["env_passthrough"] == Set(["LM_LICENSE_FILE"])

    def test_verbs_converted(self, tmp_path):
        path = _write(
            tmp_path,
            "config_version = 1\n"
            "[defaults]\n"
            'modules = { set = ["a"] }\n'
            'env_unset = { remove = ["X"], add = ["Y"] }\n'
            'scheduler_args = { sge = ["-binding", "linear:1"], slurm = { add = ["--x"] } }\n',
        )
        config = load_config(path)
        assert config.defaults["modules"] == Set(["a"])
        assert config.defaults["env_unset"] == Edit(remove=["X"], add=["Y"])
        assert config.defaults["scheduler_args"] == {
            "sge": ["-binding", "linear:1"],
            "slurm": Edit(add=["--x"]),
        }

    def test_unknown_verb_rejected(self, tmp_path):
        path = _write(tmp_path, 'config_version = 1\n[defaults]\nmodules = { replace = ["a"] }\n')
        (err,) = _errors(path)
        assert "unknown list verb 'replace'" in str(err)

    def test_set_with_add_rejected(self, tmp_path):
        path = _write(
            tmp_path, 'config_version = 1\n[defaults]\nmodules = { set = ["a"], add = ["b"] }\n'
        )
        (err,) = _errors(path)
        assert "'set' cannot be combined" in str(err)

    def test_type_missing_tool(self, tmp_path):
        path = _write(
            tmp_path,
            'config_version = 1\n[tools.xrun]\n\n[types.lint]\ntools = ["xrun", "slang"]\n',
        )
        (err,) = _errors(path)
        assert (err.path, err.line) == (path.resolve(), 5)
        assert "[types.lint] tools: no tool 'slang'" in str(err)

    def test_type_tool_defined_in_other_file(self, tmp_path):
        _write(tmp_path, "config_version = 1\n[tools.slang]\ncpu = 1\n", name="base.toml")
        path = _write(
            tmp_path, 'config_version = 1\nextends = "base.toml"\n[types.lint]\ntools = ["slang"]\n'
        )
        assert load_config(path).types["lint"]["tools"] == ["slang"]

    def test_resources_merge_across_files(self, tmp_path):
        """B2: two files both defining resources load and merge."""
        _write(
            tmp_path,
            'config_version = 1\n[defaults]\nresources = [{ name = "gpu", value = 1 }]\n',
            name="base.toml",
        )
        path = _write(
            tmp_path,
            'config_version = 1\nextends = "base.toml"\n'
            '[defaults]\nresources = [{ name = "gpu", value = 2 }, { name = "s", value = "1G" }]\n',
        )
        assert load_config(path).defaults["resources"] == [
            {"name": "gpu", "value": 2},
            {"name": "s", "value": "1G"},
        ]

    def test_schema_covers_exactly_the_config_settable_jobspec_fields(self):
        """A new JobSpec field must be added to the config schema (or excluded)."""
        settable = {f.name for f in dataclasses.fields(JobSpec)} - PER_INVOCATION_FIELDS
        assert set(FIELD_VALIDATORS) == settable

    def test_shipped_default_config_is_valid(self):
        path = Path(__file__).parents[2] / "defaults" / "config.toml"
        config, errors = check_config(path)
        assert errors == []


class TestUnitValues:
    """D10: mem and time are checked at load, with file and line."""

    def test_bad_mem_reported_with_line(self, tmp_path):
        path = _write(tmp_path, 'config_version = 1\n[tools.sim]\ncpu = 2\nmem = "lots"\n')
        (err,) = _errors(path)
        assert (err.path, err.line) == (path, 4)
        assert "[tools.sim] mem: invalid memory 'lots'" in str(err)

    def test_bare_integer_time_reported(self, tmp_path):
        path = _write(tmp_path, 'config_version = 1\n[defaults]\ntime = "90"\n')
        (err,) = _errors(path)
        assert err.line == 3
        assert "ambiguous: use 90s or 90m" in str(err)

    def test_valid_values_kept_as_written(self, tmp_path):
        path = _write(tmp_path, 'config_version = 1\n[defaults]\nmem = "16GiB"\ntime = "1h30m"\n')
        config = load_config(path)
        assert (config.defaults["mem"], config.defaults["time"]) == ("16GiB", "1h30m")

    def test_variable_checked_at_resolve(self, tmp_path):
        path = _write(tmp_path, 'config_version = 1\n[defaults]\nmem = "${MEM}"\n')
        assert load_config(path).defaults["mem"] == "${MEM}"


class TestOutputPatterns:
    @pytest.mark.parametrize(
        ("key", "hint"),
        [("stdout", "omit the key to use the default"), ("stderr", "omit the key to merge")],
    )
    def test_empty_is_error(self, tmp_path, key, hint):
        path = _write(tmp_path, f'config_version = 1\n[defaults]\n{key} = ""\n')
        (err,) = _errors(path)
        assert err.line == 3
        assert f"[defaults] {key}: empty; {hint}" in str(err)


class TestSchedulerSections:
    def test_declared_keys_accepted(self, tmp_path):
        path = _write(
            tmp_path,
            'config_version = 1\n[schedulers.sge]\nparallel_environment = "mpi"\n'
            "emulate_afterok = false\n",
        )
        config = load_config(path)
        assert config.schedulers["sge"] == {"parallel_environment": "mpi", "emulate_afterok": False}

    def test_unknown_key_rejected(self, tmp_path):
        path = _write(tmp_path, "config_version = 1\n[schedulers.sge]\nmerge_output = true\n")
        (err,) = _errors(path)
        assert err.line == 3
        assert "[schedulers.sge] merge_output: unknown key" in str(err)

    def test_memory_per_declared(self, tmp_path):
        path = _write(tmp_path, 'config_version = 1\n[schedulers.sge]\nmemory_per = "job"\n')
        assert load_config(path).schedulers["sge"] == {"memory_per": "job"}

    def test_wrong_type_rejected(self, tmp_path):
        path = _write(tmp_path, "config_version = 1\n[schedulers.local]\nmodule_system = 1\n")
        (err,) = _errors(path)
        assert "expected str" in str(err)

    @pytest.mark.parametrize("key", ["purge_modules", "silent_modules", "expand_makeflags"])
    def test_deleted_script_flags_rejected(self, tmp_path, key):
        """D15: these keys are gone; module purge is unconditional."""
        path = _write(tmp_path, f"config_version = 1\n[schedulers.sge]\n{key} = true\n")
        (err,) = _errors(path)
        assert f"[schedulers.sge] {key}: unknown key" in str(err)

    def test_unknown_scheduler_is_warning(self, tmp_path, caplog):
        path = _write(tmp_path, 'config_version = 1\n[schedulers.lsf]\nqueue = "x"\n')
        with caplog.at_level(logging.WARNING, logger="hpc_runner.config"):
            config = load_config(path)
        assert len(config.warnings) == 1
        assert "[schedulers.lsf] no scheduler 'lsf'" in config.warnings[0]
        assert config.warnings[0] in caplog.text

    def test_registered_but_missing_backend_is_warning(self, tmp_path):
        path = _write(tmp_path, "config_version = 1\n[schedulers.slurm]\n")
        assert len(load_config(path).warnings) == 1

    def test_backend_declares_keys(self):
        assert scheduler_config_keys("sge")["emulate_afterok"] is bool
        assert scheduler_config_keys("nope") is None


class TestFindLine:
    TEXT = (
        "config_version = 1\n"
        "[defaults]\n"
        "cpu = 1\n"
        "\n"
        "[tools.python]\n"
        "  modules = []\n"
        '[tools.fusesoc.options."--tool slang"]\n'
        "mem = 1\n"
        "[tools]\n"
        "gcc = { cpu = 1 }\n"
    )

    @pytest.mark.parametrize(
        "path, line",
        [
            (["config_version"], 1),
            (["defaults", "cpu"], 3),
            (["tools", "python", "modules"], 6),
            (["tools", "python", "cpu"], 5),  # falls back to the header
            (["tools", "fusesoc", "options", "--tool slang", "mem"], 8),
            (["tools", "gcc", "cpu"], 10),  # inline table: its key line
            (["types", "x"], None),
        ],
    )
    def test_lookup(self, path, line):
        assert find_line(self.TEXT, path) == line
