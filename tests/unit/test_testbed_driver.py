"""Tests for the ``hpc-testbed`` driver: the docker compose commands it builds.

Nothing here starts a container: :func:`hpc_runner._testbed.run` is replaced
by a recorder and the assertions are on the argv lists and environment.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hpc_runner import _testbed as driver
from hpc_runner._testbed import Stack, find_root, find_stack, main

TestbedError = driver.TestbedError
TestbedError.__test__ = False  # type: ignore[attr-defined]  # not a test class, despite the name


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A fake checkout with one family and two flavours."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    for family, flavour in (("sge", "soge"), ("sge", "ocs")):
        d = tmp_path / "testbed" / family / flavour
        d.mkdir(parents=True)
        (d / "compose.yml").write_text("services: {}\n")
    (tmp_path / "src" / "deep").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[list[str], dict[str, str] | None]]:
    """Record every command the driver would run instead of running it."""
    recorded: list[tuple[list[str], dict[str, str] | None]] = []

    def fake_run(argv, *, env=None, check=True):
        recorded.append((list(argv), env))
        return 0

    monkeypatch.setattr(driver, "run", fake_run)
    return recorded


def _compose(repo: Path, *tail: str) -> list[str]:
    return [
        "docker",
        "compose",
        "-f",
        str(repo / "testbed/sge/soge/compose.yml"),
        "-p",
        "hpc-testbed-soge",
        *tail,
    ]


class TestDiscovery:
    def test_root_is_found_from_a_subdirectory(self, repo):
        assert find_root(repo / "src" / "deep") == repo

    def test_no_checkout_is_an_error(self, tmp_path):
        with pytest.raises(TestbedError, match="no hpc-runner checkout"):
            find_root(tmp_path)

    def test_flavour_resolves_to_its_family(self, repo):
        stack = find_stack(repo, "soge")
        assert stack == Stack(root=repo, family="sge", flavour="soge")
        assert stack.compose_file == repo / "testbed/sge/soge/compose.yml"
        assert stack.project == "hpc-testbed-soge"

    def test_unknown_flavour_lists_the_known_ones(self, repo):
        with pytest.raises(TestbedError, match="known flavours: ocs, soge"):
            find_stack(repo, "nope")

    def test_main_reports_usage_errors_on_stderr(self, repo, monkeypatch, capsys):
        monkeypatch.chdir(repo)
        assert main(["--flavour", "nope", "down"]) == 2
        assert "hpc-testbed: unknown flavour 'nope'" in capsys.readouterr().err


class TestCommands:
    @pytest.fixture(autouse=True)
    def _in_repo(self, repo, monkeypatch):
        monkeypatch.chdir(repo)

    def test_up_builds_starts_and_waits_for_ready(self, repo, calls):
        assert main(["up"]) == 0
        argvs = [c[0] for c in calls]
        assert argvs == [
            _compose(repo, "build"),
            _compose(repo, "up", "-d", "--wait"),
            _compose(
                repo,
                "exec",
                "-T",
                "login",
                "/opt/testbed/common/wait-for.sh",
                "90",
                "3",
                "--",
                "/opt/testbed/ready.sh",
            ),
        ]
        assert all(env == {"ACCOUNTING_FLUSH": "00:00:01"} for _, env in calls)

    def test_up_slow_accounting_and_no_build(self, repo, calls):
        main(["up", "--slow-accounting", "--no-build", "--ready-timeout", "30"])
        argvs = [c[0] for c in calls]
        assert argvs[0] == _compose(repo, "up", "-d", "--wait")
        assert "30" in argvs[1]
        assert all(env == {"ACCOUNTING_FLUSH": "00:00:15"} for _, env in calls)

    def test_down_removes_volumes(self, repo, calls):
        main(["down"])
        assert calls == [(_compose(repo, "down", "-v", "--remove-orphans"), None)]

    def test_shell_runs_as_tester_with_the_venv_on_path(self, repo, calls):
        main(["shell", "--py", "3.11"])
        ((argv, _),) = calls
        assert argv[:7] == _compose(repo, "exec")
        assert "-u" in argv and argv[argv.index("-u") + 1] == "tester"
        assert argv[argv.index("-e") + 1].startswith("PATH=/opt/venv/3.11/bin:")
        assert argv[-2:] == ["login", "bash"]

    def test_logs_passes_the_services(self, repo, calls):
        main(["logs", "master", "exec1"])
        assert calls[0][0] == _compose(repo, "logs", "--no-color", "master", "exec1")

    def test_clean_runs_the_script_in_login(self, repo, calls):
        main(["clean"])
        assert calls[0][0] == _compose(repo, "exec", "-T", "login", "/opt/testbed/clean.sh")

    def test_pytest_runs_in_login_and_copies_the_report(self, repo, calls):
        assert main(["pytest", "--py", "3.10", "--", "-k", "submit", "-x"]) == 0
        run_argv, copy_argv = (c[0] for c in calls)
        assert run_argv[:6] == _compose(repo)
        assert run_argv[6] == "exec"
        exec_tail = run_argv[7:]
        assert exec_tail[: exec_tail.index("login")] == ["-T", "-u", "tester", "-w", "/src"]
        pytest_part = exec_tail[exec_tail.index("login") + 1 :]
        assert pytest_part[0] == "/opt/venv/3.10/bin/pytest"
        assert pytest_part[1:4] == ["tests/integration", "-m", "integration"]
        assert "--require-cluster" in pytest_part
        assert pytest_part[pytest_part.index("--cluster") + 1] == "sge"
        assert pytest_part[pytest_part.index("--flavour") + 1] == "soge"
        assert "--junitxml=/work/junit-3.10.xml" in pytest_part
        assert pytest_part[-3:] == ["-k", "submit", "-x"]
        assert copy_argv == _compose(
            repo, "cp", "login:/work/junit-3.10.xml", str(repo / "testbed/junit-3.10.xml")
        )

    def test_pytest_slow_accounting_selects_the_variant(self, repo, calls):
        main(["pytest", "--slow-accounting"])
        assert calls[0][0][-1] == "--slow-accounting"
        main(["pytest"])
        assert "--slow-accounting" not in calls[2][0]

    def test_pytest_junit_path_can_be_chosen(self, repo, calls):
        main(["pytest", "--junit", "out/report.xml"])
        assert calls[-1][0][-1] == "out/report.xml"
        assert (repo / "out").is_dir()

    def test_pytest_returns_the_suite_status_after_copying_the_report(self, repo, monkeypatch):
        statuses = iter([1, 0])
        seen: list[list[str]] = []

        def fake_run(argv, *, env=None, check=True):
            seen.append(list(argv))
            return next(statuses)

        monkeypatch.setattr(driver, "run", fake_run)
        assert main(["pytest"]) == 1
        assert seen[-1][6] == "cp"

    def test_pytest_up_brings_the_stack_up_and_down_around_the_run(self, repo, calls):
        main(["pytest", "--up", "--slow-accounting"])
        verbs = [c[0][6] for c in calls]
        assert verbs == ["build", "up", "exec", "exec", "cp", "down"]
        assert calls[0][1] == {"ACCOUNTING_FLUSH": "00:00:15"}

    def test_pytest_up_keep_leaves_the_stack_running(self, repo, calls):
        main(["pytest", "--up", "--keep"])
        assert [c[0][6] for c in calls] == ["build", "up", "exec", "exec", "cp"]

    def test_pytest_up_takes_the_stack_down_even_if_the_suite_fails(self, repo, monkeypatch):
        seen: list[str] = []

        def fake_run(argv, *, env=None, check=True):
            seen.append(argv[6])
            return 1 if argv[6] == "exec" and "pytest" in " ".join(argv) else 0

        monkeypatch.setattr(driver, "run", fake_run)
        assert main(["pytest", "--up"]) == 1
        assert seen[-1] == "down"

    def test_capture_runs_the_module_in_login_and_copies_the_fixtures(self, repo, calls):
        main(["capture", "--py", "3.11"])
        run_argv, copy_argv = (c[0] for c in calls)
        assert run_argv[6] == "exec"
        tail = run_argv[run_argv.index("login") + 1 :]
        assert tail == [
            "/opt/venv/3.11/bin/python",
            "-m",
            "tests.integration.capture",
            "/work/capture",
            "soge",
        ]
        assert copy_argv == _compose(
            repo, "cp", "login:/work/capture/.", str(repo / "tests/unit/fixtures/sge")
        )
        assert (repo / "tests/unit/fixtures/sge").is_dir()

    def test_other_flavour_changes_file_and_project(self, repo, calls):
        main(["--flavour", "ocs", "down"])
        argv = calls[0][0]
        assert argv[3] == str(repo / "testbed/sge/ocs/compose.yml")
        assert argv[5] == "hpc-testbed-ocs"


def test_run_echoes_and_raises_on_failure(capsys):
    with pytest.raises(SystemExit) as info:
        driver.run(["false"])
    assert info.value.code == 1
    assert "+ false" in capsys.readouterr().err


def test_run_check_false_returns_status(capsys):
    assert driver.run(["false"], check=False) == 1
    assert driver.run(["true"], env={"X": "1"}) == 0
