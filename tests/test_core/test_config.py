"""Tests for configuration system."""

import os

import pytest

from hpc_runner.core.config import (
    HPC_CONFIG_ENV_VAR,
    HPCConfig,
    _expand_env_vars,
    _match_contiguous,
    _merge,
    _normalise_tokens,
    _resolve_extends,
    find_config_file,
    find_config_files,
    load_config,
)
from hpc_runner.core.exceptions import ConfigError


class TestHPCConfig:
    """Tests for HPCConfig class."""

    def test_empty_config(self):
        """Test empty configuration."""
        config = HPCConfig()
        assert config.defaults == {}
        assert config.tools == {}
        assert config.types == {}

    def test_get_tool_config_defaults(self):
        """Test getting tool config falls back to defaults."""
        config = HPCConfig(defaults={"cpu": 2, "mem": "8G"})
        job_config = config.get_tool_config("unknown_tool")

        assert job_config["cpu"] == 2
        assert job_config["mem"] == "8G"

    def test_get_tool_config_tool(self):
        """Test getting tool config for a tool."""
        config = HPCConfig(
            defaults={"cpu": 1, "mem": "4G"},
            tools={"python": {"cpu": 4, "modules": ["python/3.11"]}},
        )
        job_config = config.get_tool_config("python script.py")

        assert job_config["cpu"] == 4  # Overridden
        assert job_config["mem"] == "4G"  # From defaults
        assert job_config["modules"] == ["python/3.11"]

    def test_get_type_config(self):
        """Test getting type config for a type."""
        config = HPCConfig(
            defaults={"cpu": 1},
            types={"gpu": {"queue": "gpu", "resources": [{"name": "gpu", "value": 1}]}},
        )
        job_config = config.get_type_config("gpu")

        assert job_config["cpu"] == 1  # From defaults
        assert job_config["queue"] == "gpu"

    def test_type_not_found_via_tool_config(self):
        """Test that a type name is NOT matched via get_tool_config."""
        config = HPCConfig(
            defaults={"cpu": 1},
            tools={"python": {"cpu": 4}},
            types={"gpu": {"queue": "gpu"}},
        )
        job_config = config.get_tool_config("gpu")

        # Should return only defaults — "gpu" is a type, not a tool
        assert job_config == {"cpu": 1}
        assert "queue" not in job_config

    def test_tool_not_found_via_type_config(self):
        """Test that a tool name is NOT matched via get_type_config."""
        config = HPCConfig(
            defaults={"cpu": 1},
            tools={"python": {"cpu": 4}},
            types={"gpu": {"queue": "gpu"}},
        )
        job_config = config.get_type_config("python")

        # Should return only defaults — "python" is a tool, not a type
        assert job_config == {"cpu": 1}
        assert "queue" not in job_config

    def test_get_tool_config_extracts_name(self):
        """Test that get_tool_config extracts tool name from command."""
        config = HPCConfig(
            tools={"python": {"cpu": 4}},
        )
        job_config = config.get_tool_config("python script.py --arg value")

        assert job_config["cpu"] == 4

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
    """Tests for _merge deep merge logic."""

    def test_list_merge_preserves_order(self):
        """Module load order matters on HPC - merge must preserve it."""
        base = {"modules": ["gcc/12", "openmpi/4", "python/3.11"]}
        override = {"modules": ["cuda/12", "python/3.11"]}
        result = _merge(base, override)

        # python/3.11 appears in both - should be deduplicated
        assert result["modules"].count("python/3.11") == 1
        # Order from base should come first, then new items from override
        assert result["modules"] == ["gcc/12", "openmpi/4", "python/3.11", "cuda/12"]

    def test_list_merge_reset_marker(self):
        """The '-' marker should discard the base list entirely."""
        base = {"modules": ["gcc/12", "openmpi/4"]}
        override = {"modules": ["-", "intel/2024", "impi/2024"]}
        result = _merge(base, override)

        assert result["modules"] == ["intel/2024", "impi/2024"]

    def test_list_merge_no_duplicates_from_base(self):
        """Duplicate entries within a single list should be preserved."""
        base = {"args": ["-v"]}
        override = {"args": ["-v", "-x"]}
        result = _merge(base, override)

        # -v appears in both lists but dedup should keep only one
        assert result["args"].count("-v") == 1
        assert "-x" in result["args"]

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
        config_file.write_text("[defaults]\ncpu = 1\n")

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


class TestEnvVarExpansion:
    """Tests for environment variable expansion in paths."""

    def test_expand_braced_env_var(self):
        """Test ${VAR} syntax expansion."""
        os.environ["TEST_HPC_PATH"] = "/test/path"
        try:
            result = _expand_env_vars("${TEST_HPC_PATH}/config.toml")
            assert result == "/test/path/config.toml"
        finally:
            del os.environ["TEST_HPC_PATH"]

    def test_expand_simple_env_var(self):
        """Test $VAR syntax expansion."""
        os.environ["TEST_HPC_PATH"] = "/test/path"
        try:
            result = _expand_env_vars("$TEST_HPC_PATH/config.toml")
            assert result == "/test/path/config.toml"
        finally:
            del os.environ["TEST_HPC_PATH"]

    def test_undefined_env_var_unchanged(self):
        """Test that undefined env vars are left unchanged."""
        result = _expand_env_vars("${UNDEFINED_VAR}/config.toml")
        assert result == "${UNDEFINED_VAR}/config.toml"

    def test_multiple_env_vars(self):
        """Test multiple env vars in one path."""
        os.environ["TEST_BASE"] = "/base"
        os.environ["TEST_SUB"] = "subdir"
        try:
            result = _expand_env_vars("${TEST_BASE}/${TEST_SUB}/config.toml")
            assert result == "/base/subdir/config.toml"
        finally:
            del os.environ["TEST_BASE"]
            del os.environ["TEST_SUB"]


class TestExtendsResolution:
    """Tests for extends chain resolution."""

    def test_resolve_extends_single(self, temp_dir):
        """Test resolving a single extends."""
        base_config = temp_dir / "base.toml"
        base_config.write_text("[defaults]\ncpu = 1\n")

        child_config = temp_dir / "child.toml"
        child_config.write_text(f'extends = "{base_config}"\n[defaults]\nmem = "8G"\n')

        chain = _resolve_extends(child_config)

        assert len(chain) == 2
        assert chain[0] == base_config.resolve()
        assert chain[1] == child_config.resolve()

    def test_resolve_extends_relative_path(self, temp_dir):
        """Test resolving extends with relative path."""
        base_config = temp_dir / "base.toml"
        base_config.write_text("[defaults]\ncpu = 1\n")

        child_config = temp_dir / "child.toml"
        child_config.write_text('extends = "base.toml"\n[defaults]\nmem = "8G"\n')

        chain = _resolve_extends(child_config)

        assert len(chain) == 2
        assert chain[0] == base_config.resolve()

    def test_resolve_extends_chain(self, temp_dir):
        """Test resolving a chain of extends."""
        grandparent = temp_dir / "grandparent.toml"
        grandparent.write_text("[defaults]\ncpu = 1\n")

        parent = temp_dir / "parent.toml"
        parent.write_text('extends = "grandparent.toml"\n[defaults]\nmem = "4G"\n')

        child = temp_dir / "child.toml"
        child.write_text('extends = "parent.toml"\n[defaults]\ntime = "1:00:00"\n')

        chain = _resolve_extends(child)

        assert len(chain) == 3
        assert chain[0] == grandparent.resolve()
        assert chain[1] == parent.resolve()
        assert chain[2] == child.resolve()

    def test_resolve_extends_circular_detected(self, temp_dir):
        """Test that circular extends are detected."""
        config_a = temp_dir / "a.toml"
        config_b = temp_dir / "b.toml"

        config_a.write_text('extends = "b.toml"\n[defaults]\ncpu = 1\n')
        config_b.write_text('extends = "a.toml"\n[defaults]\nmem = "4G"\n')

        with pytest.raises(ValueError, match="Circular extends"):
            _resolve_extends(config_a)

    def test_resolve_extends_with_env_var(self, temp_dir):
        """Test resolving extends with env var in path."""
        base_config = temp_dir / "base.toml"
        base_config.write_text("[defaults]\ncpu = 1\n")

        os.environ["TEST_CONFIG_DIR"] = str(temp_dir)
        try:
            child_config = temp_dir / "child.toml"
            child_config.write_text(
                'extends = "${TEST_CONFIG_DIR}/base.toml"\n[defaults]\nmem = "8G"\n'
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
        base_config.write_text('[defaults]\ncpu = 1\nmem = "4G"\n[schedulers.sge]\npe = "smp"\n')

        child_config = temp_dir / "child.toml"
        child_config.write_text(
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
        assert config.schedulers["sge"]["pe"] == "smp"
        # tools from child
        assert config.tools["python"]["modules"] == ["python/3.11"]

    def test_find_config_files_from_env_var(self, temp_dir):
        """Test that HPC_RUNNER_CONFIG env var replaces git root (mutually exclusive)."""
        # Create a fake git root with its own config
        git_dir = temp_dir / ".git"
        git_dir.mkdir()
        git_config = temp_dir / "hpc-runner.toml"
        git_config.write_text("[defaults]\ncpu = 1\n")

        # Create env var config in a different location
        env_config = temp_dir / "site" / "site.toml"
        env_config.parent.mkdir()
        env_config.write_text("[defaults]\ncpu = 2\n")

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
        env_config.write_text("[defaults]\ncpu = 2\n")

        # Create a local override in a subdirectory
        subdir = temp_dir / "project"
        subdir.mkdir()
        local_config = subdir / "hpc-runner.toml"
        local_config.write_text('[defaults]\nmem = "16G"\n')

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
        config_file.write_text("[defaults]\ncpu = 1\n")

        config = load_config(config_file)

        assert len(config._source_paths) == 1
        assert config._source_paths[0] == config_file.resolve()


class TestToolOptionMatching:
    """Tests for tool option matching."""

    def test_no_options_unchanged(self):
        """Tool without options works as before."""
        config = HPCConfig(
            defaults={"cpu": 1},
            tools={"python": {"cpu": 4}},
        )
        result = config.get_tool_config("python script.py")
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
        result = config.get_tool_config("mytool --gui input.dat")
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
        result = config.get_tool_config("fusesoc run --target lint --tool slang v:l:n:v")
        assert result["mem"] == "16G"
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
        result = config.get_tool_config("fusesoc run --tool=slang")
        assert result["mem"] == "16G"

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
        result = config.get_tool_config("fusesoc run --tool slang-lint")
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
        result = config.get_tool_config("mytool --batch input.dat")
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
        result = config.get_tool_config("fusesoc run --tool slang")
        assert result["mem"] == "16G"

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
        result = config.get_tool_config("fusesoc run --tool slang")
        assert result["cpu"] == 2  # Inherited from base
        assert result["mem"] == "16G"  # From option

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
        result = config.get_tool_config("mytool run")
        assert "options" not in result

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
        result = config.get_tool_config("mytool -g input.dat")
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
        result = config.get_tool_config("/usr/local/bin/mytool --gui")
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
