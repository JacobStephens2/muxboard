import pytest

from muxboard.inventory import Host, index_by_key


def test_minimal_local_host_ok():
    h = Host(key="local", hostname="localhost", tmux_users=("deploy",), local=True)
    assert h.display == "localhost"
    assert h.local is True


def test_label_overrides_display():
    h = Host(key="w", hostname="w.example.com", label="web-1", ssh_user="ops")
    assert h.display == "web-1"


def test_bad_key_rejected():
    with pytest.raises(ValueError):
        Host(key="has space", hostname="x", ssh_user="ops")


def test_remote_host_requires_ssh_user():
    with pytest.raises(ValueError):
        Host(key="w", hostname="w.example.com")


def test_password_and_key_mutually_exclusive():
    with pytest.raises(ValueError):
        Host(key="w", hostname="x", ssh_user="ops", password_env="P", ssh_key="/k")


def test_invalid_tmux_user_rejected():
    with pytest.raises(ValueError):
        Host(key="w", hostname="x", ssh_user="ops", tmux_users=("Bad User",))


def test_index_by_key_detects_dupes():
    a = Host(key="x", hostname="a", ssh_user="ops")
    b = Host(key="x", hostname="b", ssh_user="ops")
    with pytest.raises(ValueError):
        index_by_key([a, b])
