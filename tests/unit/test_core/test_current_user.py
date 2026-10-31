"""current_user(): $USER when set, the account name otherwise."""

import getpass

from hpc_runner.core.host import Host, current_user


def test_user_env_wins(monkeypatch):
    monkeypatch.setenv("USER", "alice")
    assert current_user() == "alice"
    assert current_user({"USER": "bob"}) == "bob"


def test_falls_back_to_the_account_name(monkeypatch):
    """docker exec, cron and some sudo setups leave USER unset."""
    monkeypatch.delenv("USER", raising=False)
    monkeypatch.delenv("USERNAME", raising=False)
    assert current_user() == getpass.getuser()
    assert current_user({}) == getpass.getuser()
    assert current_user({"USER": ""}) == getpass.getuser()


def test_host_capture_uses_it(monkeypatch):
    monkeypatch.delenv("USER", raising=False)
    assert Host.capture().user == getpass.getuser()
