import pytest

from muxboard.inventory import Host, index_by_key, valid_socket_path


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


# ---------- custom tmux sockets (-S) ----------


def test_socket_fields_default_empty():
    h = Host(key="local", hostname="localhost", tmux_users=("deploy",), local=True)
    assert h.tmux_socket == ""
    assert h.tmux_socket_file == ""


def test_literal_socket_accepted():
    h = Host(key="swarm", hostname="localhost", local=True,
             tmux_users=("jacob",), tmux_socket="/tmp/swarmforge-jacob/ab12cd34.sock")
    assert h.tmux_socket == "/tmp/swarmforge-jacob/ab12cd34.sock"


def test_socket_file_accepted():
    h = Host(key="swarm", hostname="localhost", local=True,
             tmux_users=("jacob",), tmux_socket_file="/srv/proj/.swarmforge/tmux-socket")
    assert h.tmux_socket_file == "/srv/proj/.swarmforge/tmux-socket"


def test_socket_and_socket_file_mutually_exclusive():
    with pytest.raises(ValueError):
        Host(key="s", hostname="localhost", local=True,
             tmux_socket="/tmp/a.sock", tmux_socket_file="/tmp/f")


@pytest.mark.parametrize("bad", [
    "relative/path.sock",
    "/tmp/../etc/x.sock",
    "/tmp/has space.sock",
    "/tmp/$(id).sock",
    "/tmp/a;rm -rf /",
    "/tmp/back\\slash",
    "/tmp/colon:sock",
    "/tmp/trailing/",
    "/",
    "/tmp/\nnewline",
])
def test_bad_socket_path_rejected(bad):
    with pytest.raises(ValueError):
        Host(key="s", hostname="localhost", local=True, tmux_socket=bad)
    with pytest.raises(ValueError):
        Host(key="s", hostname="localhost", local=True, tmux_socket_file=bad)


def test_overlong_socket_path_rejected():
    with pytest.raises(ValueError):
        Host(key="s", hostname="localhost", local=True,
             tmux_socket="/tmp/" + "a" * 1100 + ".sock")


def test_valid_socket_path_helper():
    assert valid_socket_path("/tmp/swarmforge-jacob/2b1f9c.sock")
    assert valid_socket_path("/run/user/1000/tmux-default")
    assert not valid_socket_path("")
    assert not valid_socket_path("/tmp/..")
    assert not valid_socket_path("/tmp//double")


def test_trailing_newline_socket_path_rejected():
    # re `$` matches before a final newline; the whitelist must not.
    assert not valid_socket_path("/tmp/a.sock\n")
    with pytest.raises(ValueError):
        Host(key="s", hostname="localhost", local=True, tmux_socket="/tmp/a.sock\n")
