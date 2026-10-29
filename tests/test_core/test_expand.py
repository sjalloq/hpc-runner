"""Tests for the D5 expansion rule (hpc_runner.core.expand)."""

import pytest

from hpc_runner.core.config import load_config
from hpc_runner.core.exceptions import ConfigError
from hpc_runner.core.expand import bind_config_dir, expand
from hpc_runner.core.resolve import resolve
from hpc_runner.core.spec import JobSpec
from tests.fakes import make_host


def _x(value, env=None, extra=None):
    return expand(value, host_env=env or {}, extra=extra or {}, where="here")


class TestRules:
    def test_bare_and_braced(self):
        assert _x("$A/${A}", {"A": "1"}) == "1/1"

    def test_dollar_dollar_is_literal(self):
        assert _x("$$", {}) == "$"
        assert _x("$$HOME", {"HOME": "/h"}) == "$HOME"
        assert _x("a$$$A", {"A": "1"}) == "a$1"

    def test_expanded_value_not_rescanned(self):
        assert _x("$A", {"A": "$B", "B": "no"}) == "$B"

    @pytest.mark.parametrize("name", ["cpus", "job_id", "task_id", "tmpdir", "job_name", "host"])
    def test_hpc_names_preserved(self, name):
        assert _x(f"x${{hpc.{name}}}y") == f"x${{hpc.{name}}}y"

    def test_unknown_hpc_name_is_error(self):
        with pytest.raises(ConfigError, match=r"unknown \$\{hpc.cores\}; valid names are") as info:
            _x("${hpc.cores}")
        assert "${hpc.cpus}" in str(info.value)

    def test_undefined_is_error(self):
        with pytest.raises(ConfigError, match=r"^here: undefined variable \$NOPE$"):
            _x("$NOPE")

    @pytest.mark.parametrize(
        "var,hpc",
        [("NSLOTS", "cpus"), ("SLURM_JOB_ID", "job_id"), ("SGE_TASK_ID", "task_id")],
    )
    def test_runtime_hint(self, var, hpc):
        with pytest.raises(ConfigError, match=rf"did you mean \$\{{hpc.{hpc}\}}\?"):
            _x(f"-j${var}")

    def test_hinted_name_defined_on_host_is_used(self):
        assert _x("$TMPDIR", {"TMPDIR": "/tmp"}) == "/tmp"

    def test_extra_before_host_env(self):
        assert _x("$USER", {"USER": "env"}, {"USER": "extra"}) == "extra"

    def test_unsupported_brace_form_is_error(self):
        with pytest.raises(ConfigError, match=r"unsupported \$\{A:-x\}"):
            _x("${A:-x}", {"A": "1"})

    def test_lone_dollar_kept(self):
        assert _x("cost $5 $(x)") == "cost $5 $(x)"

    def test_config_dir_outside_config_is_error(self):
        with pytest.raises(ConfigError, match=r"only defined for values set in a config file"):
            _x("${CONFIG_DIR}/x")


class TestBindConfigDir:
    def test_binds_both_forms(self):
        assert bind_config_dir("$CONFIG_DIR/${CONFIG_DIR}", "/c") == "/c//c"

    def test_keeps_escapes_and_other_vars(self):
        assert bind_config_dir("$${CONFIG_DIR} $HOME", "/c") == "$${CONFIG_DIR} $HOME"

    def test_dollar_in_dir_stays_literal(self):
        bound = bind_config_dir("${CONFIG_DIR}/x", "/a$b")
        assert _x(bound) == "/a$b/x"


class TestConfigDirFromFile:
    """${CONFIG_DIR} is the directory of the file that set the value."""

    def test_two_file_extends_chain(self, tmp_path):
        base_dir = tmp_path / "site"
        proj_dir = tmp_path / "proj"
        base_dir.mkdir()
        proj_dir.mkdir()
        (base_dir / "base.toml").write_text(
            "config_version = 1\n"
            '[defaults]\nmodules_path = ["${CONFIG_DIR}/modulefiles"]\n'
            '[defaults.env_vars]\nSITE = "${CONFIG_DIR}"\n'
        )
        proj = proj_dir / "hpc-runner.toml"
        proj.write_text(
            "config_version = 1\n"
            'extends = "../site/base.toml"\n'
            '[defaults]\nmodules_path = ["${CONFIG_DIR}/mf"]\n'
            '[defaults.env_prepend]\nPATH = "${CONFIG_DIR}/bin"\n'
        )
        config = load_config(proj)
        job = resolve(JobSpec("echo hi"), config, make_host())
        site, project = str(base_dir.resolve()), str(proj_dir.resolve())
        assert job.modules_path == (f"{site}/modulefiles", f"{project}/mf")
        assert job.env_vars["SITE"] == site
        assert job.env_prepend["PATH"] == f"{project}/bin"

    def test_spec_value_is_error(self, tmp_path):
        proj = tmp_path / "hpc-runner.toml"
        proj.write_text("config_version = 1\n")
        with pytest.raises(ConfigError, match=r"^spec: workdir: \$\{CONFIG_DIR\} is only"):
            resolve(JobSpec("x", workdir="${CONFIG_DIR}"), load_config(proj), make_host())

    def test_options_and_types_bound(self, tmp_path):
        proj = tmp_path / "hpc-runner.toml"
        proj.write_text(
            "config_version = 1\n"
            '[tools.xrun.options."-gui"]\nworkdir = "${CONFIG_DIR}/gui"\n'
            '[types.t]\nstdout = "${CONFIG_DIR}/t.out"\n'
            '[types.t.scheduler_args]\nsge = ["-l", "x=${CONFIG_DIR}"]\n'
        )
        config = load_config(proj)
        here = str(tmp_path.resolve())
        assert resolve(JobSpec("xrun -gui"), config, make_host()).workdir == f"{here}/gui"
        job = resolve(JobSpec("x", type="t"), config, make_host())
        assert job.stdout == f"{here}/t.out"
        assert job.scheduler_args["sge"] == ("-l", f"x={here}")
