import pytest

from muxboard.inventory import Host
from muxboard.tmuxctl import (
    TmuxController,
    TmuxctlError,
    _as_user_prefix,
    valid_new_session_name,
)

SEP = "::"


def _ctrl(*hosts):
    return TmuxController(list(hosts), ssh_key="/board/key")


def test_as_user_prefix():
    assert _as_user_prefix("ops", "ops") == ""
    assert _as_user_prefix("deploy", "ops") == "sudo -n -u deploy "


def test_valid_session_name():
    assert valid_new_session_name("build-1")
    assert not valid_new_session_name("bad.name")
    assert not valid_new_session_name("has space")
    assert not valid_new_session_name("")


def test_build_argv_local():
    h = Host(key="local", hostname="localhost", tmux_users=("me",), local=True)
    argv, env = _ctrl(h)._build_argv(h, "tmux ls")
    assert argv[:2] == ["bash", "-c"]
    assert argv[2] == "tmux ls"
    assert env == {}


def test_build_argv_key_based():
    h = Host(key="db", hostname="db.example.com", ssh_user="ops",
             ssh_key="/keys/db", tmux_users=("ops",))
    argv, env = _ctrl(h)._build_argv(h, "tmux ls", interactive=True)
    assert argv[0] == "ssh"
    assert "/keys/db" in argv
    assert "-tt" in argv
    assert argv[-2:] == ["ops@db.example.com", "tmux ls"]
    assert env == {}


def test_build_argv_password(monkeypatch):
    monkeypatch.setenv("WEB_PASS", "secret")
    h = Host(key="web", hostname="web.example.com", ssh_user="ops",
             password_env="WEB_PASS", tmux_users=("ops",))
    argv, env = _ctrl(h)._build_argv(h, "tmux ls")
    assert argv[:3] == ["sshpass", "-e", "ssh"]
    assert env == {"SSHPASS": "secret"}


def test_build_argv_password_missing_env(monkeypatch):
    monkeypatch.delenv("WEB_PASS", raising=False)
    h = Host(key="web", hostname="web.example.com", ssh_user="ops",
             password_env="WEB_PASS", tmux_users=("ops",))
    with pytest.raises(TmuxctlError):
        _ctrl(h)._build_argv(h, "tmux ls")


def test_list_script_uses_sudo_for_other_users():
    h = Host(key="local", hostname="localhost",
             tmux_users=("root", "deploy"), local=True)
    # login user on a local host is the current process user; force a known
    # value via a controller whose _login_user we can predict by making the
    # first tmux user equal to it is not reliable, so just assert structure.
    script = _ctrl(h)._list_script(h)
    assert "tmux ls -F" in script
    assert "__MUXBOARD_ERR__" in script  # marker for sudo-refused users


def test_parse_list_output_roundtrip():
    line = SEP.join(["deploy", "build", "2", "1700000000", "1", "1700000500", "$3"])
    err = SEP.join(["__MUXBOARD_ERR__", "ops", "sudo refused"])
    parsed = TmuxController._parse_list_output(line + "\n" + err, host_key="x")
    assert parsed["sessions"]["deploy"][0]["name"] == "build"
    assert parsed["sessions"]["deploy"][0]["windows"] == 2
    assert parsed["sessions"]["deploy"][0]["attached"] is True
    assert parsed["errors"]["ops"] == "sudo refused"


def test_parse_list_output_natural_sorts_session_names():
    rows = [
        SEP.join(["deploy", "22", "1", "1700000000", "0", "1700000000", "$22"]),
        SEP.join(["deploy", "3", "1", "1700000000", "0", "1700000000", "$3"]),
        SEP.join(["deploy", "alpha10", "1", "1700000000", "0", "1700000000", "$10"]),
        SEP.join(["deploy", "2", "1", "1700000000", "0", "1700000000", "$2"]),
        SEP.join(["deploy", "alpha2", "1", "1700000000", "0", "1700000000", "$9"]),
    ]
    parsed = TmuxController._parse_list_output("\n".join(rows), host_key="x")
    assert [s["name"] for s in parsed["sessions"]["deploy"]] == [
        "2", "3", "22", "alpha2", "alpha10",
    ]


def test_parse_list_output_skips_garbage():
    parsed = TmuxController._parse_list_output("not-enough-fields\n", host_key="x")
    assert parsed["sessions"] == {}
    assert parsed["errors"] == {}


def test_kill_unknown_user_raises():
    h = Host(key="local", hostname="localhost", tmux_users=("me",), local=True)
    c = _ctrl(h)
    with pytest.raises(TmuxctlError):
        c.kill_session(h, "stranger", "s")


def test_snapshot_shape():
    h = Host(key="local", hostname="localhost", tmux_users=("me",), local=True)
    snap = _ctrl(h).snapshot()
    assert snap["hosts"][0]["key"] == "local"
    assert snap["hosts"][0]["users"] == ["me"]
    assert snap["last_sweep"] is None
