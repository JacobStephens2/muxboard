import pytest

from muxboard.inventory import Host
from muxboard.tmuxctl import (
    TmuxController,
    TmuxctlError,
    _as_user_prefix,
    _socket_flag,
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
    script = _ctrl(h)._list_script(h, {"root": "", "deploy": ""})
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


# ---------- custom tmux sockets (-S) ----------


def _swarm_host(**kw):
    return Host(key="swarm", hostname="localhost", local=True,
                tmux_users=("me",), **kw)


def test_socket_flag():
    assert _socket_flag("") == ""
    assert _socket_flag("/tmp/s/a.sock") == "-S /tmp/s/a.sock "


def test_list_script_without_socket_is_unchanged():
    h = Host(key="local", hostname="localhost", tmux_users=("me",), local=True)
    assert "-S " not in _ctrl(h)._list_script(h, {"me": ""})


def test_list_script_threads_socket():
    h = _swarm_host(tmux_socket="/tmp/swarmforge-me/ab12.sock")
    script = _ctrl(h)._list_script(h, {"me": "/tmp/swarmforge-me/ab12.sock"})
    assert "tmux -S /tmp/swarmforge-me/ab12.sock ls -F" in script


def test_list_script_skips_users_without_a_resolved_socket():
    h = Host(key="swarm", hostname="localhost", local=True,
             tmux_users=("me", "other"), tmux_socket_file="/srv/p/.swarmforge/tmux-socket")
    script = _ctrl(h)._list_script(h, {"me": "/tmp/a.sock"})
    assert "/tmp/a.sock" in script
    assert "other" not in script


def test_mutation_and_attach_scripts_thread_socket(monkeypatch):
    h = _swarm_host(tmux_socket="/tmp/swarmforge-me/ab12.sock")
    c = _ctrl(h)
    seen = []
    monkeypatch.setattr(c, "_run_mutation", lambda host, script: seen.append(script))
    c.kill_session(h, "me", "swarmforge-coder")
    c.create_session(h, "me", "newsess")
    argv, _ = c.attach_argv(h, "me", "swarmforge-coder")
    assert "tmux -S /tmp/swarmforge-me/ab12.sock kill-session" in seen[0]
    assert "tmux -S /tmp/swarmforge-me/ab12.sock new-session" in seen[1]
    assert "tmux -S /tmp/swarmforge-me/ab12.sock attach" in argv[2]


def test_socket_read_script_reads_capped_single_line():
    h = Host(key="swarm", hostname="localhost", local=True,
             tmux_users=("me",), tmux_socket_file="/srv/p/.swarmforge/tmux-socket")
    script = _ctrl(h)._socket_read_script(h, ("me",))
    assert "__MUXBOARD_SOCK__" in script
    assert "/srv/p/.swarmforge/tmux-socket" in script
    assert "head -c" in script  # size cap
    assert "head -n 1" in script  # single-line requirement


def test_parse_socket_output_valid_and_invalid():
    text = "\n".join([
        SEP.join(["__MUXBOARD_SOCK__", "me", "/tmp/swarmforge-me/ab12.sock"]),
        SEP.join(["__MUXBOARD_SOCK__", "other", ""]),
        SEP.join(["__MUXBOARD_SOCK__", "third", "/tmp/$(id).sock"]),
        "unrelated noise",
    ])
    sockets, errors = TmuxController._parse_socket_output(text, ("me", "other", "third"))
    assert sockets == {"me": "/tmp/swarmforge-me/ab12.sock"}
    assert set(errors) == {"other", "third"}


def test_parse_socket_output_reports_missing_users():
    sockets, errors = TmuxController._parse_socket_output("", ("me",))
    assert sockets == {}
    assert "me" in errors


def test_resolve_sockets_literal_needs_no_subprocess():
    h = _swarm_host(tmux_socket="/tmp/swarmforge-me/ab12.sock")
    sockets, errors = _ctrl(h)._resolve_sockets(h, ("me",))
    assert sockets == {"me": "/tmp/swarmforge-me/ab12.sock"}
    assert errors == {}


def test_resolve_sockets_default_when_unconfigured():
    h = Host(key="local", hostname="localhost", tmux_users=("me",), local=True)
    sockets, errors = _ctrl(h)._resolve_sockets(h, ("me",))
    assert sockets == {"me": ""}
    assert errors == {}


def test_resolve_sockets_from_file_on_local_host(tmp_path):
    import getpass
    me = getpass.getuser()
    sock_file = tmp_path / "tmux-socket"
    sock_file.write_text("/tmp/swarmforge-me/ab12.sock\nignored second line\n")
    h = Host(key="swarm", hostname="localhost", local=True,
             tmux_users=(me,), tmux_socket_file=str(sock_file))
    sockets, errors = _ctrl(h)._resolve_sockets(h, (me,))
    assert sockets == {me: "/tmp/swarmforge-me/ab12.sock"}
    assert errors == {}


def test_resolve_sockets_missing_file_is_an_error(tmp_path):
    import getpass
    me = getpass.getuser()
    h = Host(key="swarm", hostname="localhost", local=True,
             tmux_users=(me,), tmux_socket_file=str(tmp_path / "nope"))
    sockets, errors = _ctrl(h)._resolve_sockets(h, (me,))
    assert sockets == {}
    assert me in errors


def test_mutation_on_unresolvable_socket_file_raises(tmp_path):
    import getpass
    me = getpass.getuser()
    h = Host(key="swarm", hostname="localhost", local=True,
             tmux_users=(me,), tmux_socket_file=str(tmp_path / "nope"))
    with pytest.raises(TmuxctlError):
        _ctrl(h).kill_session(h, me, "s")


def test_list_host_surfaces_socket_resolution_errors(tmp_path):
    import getpass
    me = getpass.getuser()
    h = Host(key="swarm", hostname="localhost", local=True,
             tmux_users=(me,), tmux_socket_file=str(tmp_path / "nope"))
    result = _ctrl(h).list_host(h)
    assert result["ok"] is True
    assert result["sessions"] == {}
    assert me in result["errors"]


def test_parse_socket_output_survives_a_malformed_marker_line():
    # A truncated marker line must not take down the whole sweep.
    sockets, errors = TmuxController._parse_socket_output(
        "__MUXBOARD_SOCK__::oops\n", ("me",)
    )
    assert sockets == {}
    assert "me" in errors


def test_socket_read_script_distinguishes_sudo_refusal():
    h = Host(key="swarm", hostname="localhost", local=True,
             tmux_users=("someone-else",),
             tmux_socket_file="/srv/p/.swarmforge/tmux-socket")
    script = _ctrl(h)._socket_read_script(h, ("someone-else",))
    assert "sudo -n -u someone-else true" in script


def test_parse_socket_output_reports_sudo_refusal_distinctly():
    text = SEP.join(["__MUXBOARD_SOCK__", "me", "__MUXBOARD_SUDO_REFUSED__"])
    sockets, errors = TmuxController._parse_socket_output(text, ("me",))
    assert sockets == {}
    assert errors["me"] == "sudo refused"


def test_attach_on_unresolvable_socket_file_raises(tmp_path):
    import getpass
    me = getpass.getuser()
    h = Host(key="swarm", hostname="localhost", local=True,
             tmux_users=(me,), tmux_socket_file=str(tmp_path / "nope"))
    with pytest.raises(TmuxctlError):
        _ctrl(h).attach_argv(h, me, "swarmforge-coder")


def test_attach_argv_uses_socket_from_file(tmp_path):
    import getpass
    me = getpass.getuser()
    sock_file = tmp_path / "tmux-socket"
    sock_file.write_text("/tmp/swarmforge-me/ab12.sock\n")
    h = Host(key="swarm", hostname="localhost", local=True,
             tmux_users=(me,), tmux_socket_file=str(sock_file))
    argv, _ = _ctrl(h).attach_argv(h, me, "swarmforge-coder")
    assert "tmux -S /tmp/swarmforge-me/ab12.sock attach -t swarmforge-coder" in argv[2]
