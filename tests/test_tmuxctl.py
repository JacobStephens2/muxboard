import subprocess

import pytest

from muxboard.inventory import Host
from muxboard.listing import ListedSession, Listing
from muxboard.tmuxctl import TmuxController, TmuxctlError, valid_new_session_name

SEP = "::"


def _ctrl(*hosts):
    return TmuxController(list(hosts), ssh_key="/board/key")


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


# ---------- list_host: a Listing on every path ----------


def _local(*users):
    return Host(key="box", hostname="localhost", local=True, tmux_users=users)


def _fake_run(monkeypatch, *, raises=None, returncode=0, stdout="", stderr=""):
    def run(argv, **kwargs):
        if raises is not None:
            raise raises
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    monkeypatch.setattr("muxboard.tmuxctl.subprocess.run", run)


def test_list_host_on_an_inventory_only_host_is_an_empty_listing():
    h = _local()
    assert _ctrl(h).list_host(h) == Listing.worked(h, sessions={}, errors={}, elapsed_ms=0)


def test_list_host_success_is_a_listing_of_listed_sessions(monkeypatch):
    h = _local("me")
    _fake_run(monkeypatch, stdout=SEP.join(["me", "s1", "1", "5", "0", "6", "$1"]) + "\n")
    listing = _ctrl(h).list_host(h)
    assert listing.ok is True
    assert listing.users == ("me",)
    assert listing.sessions["me"] == (ListedSession(
        user="me", name="s1", windows=1, created=5, attached=False, activity=6, id="$1",
    ),)


def test_list_host_timeout_is_a_failed_listing(monkeypatch):
    h = _local("me")
    _fake_run(monkeypatch, raises=subprocess.TimeoutExpired(["bash"], 1))
    listing = _ctrl(h).list_host(h)
    assert (listing.ok, listing.error, listing.users) == (False, "timeout (>24s)", ("me",))


def test_list_host_oserror_is_a_failed_listing(monkeypatch):
    h = _local("me")
    _fake_run(monkeypatch, raises=FileNotFoundError("no bash"))
    listing = _ctrl(h).list_host(h)
    assert listing.ok is False
    assert listing.error == "FileNotFoundError: no bash"


def test_list_host_nonzero_exit_with_no_output_is_a_failed_listing(monkeypatch):
    h = _local("me")
    _fake_run(monkeypatch, returncode=255, stderr="warning\nconnection refused\n")
    listing = _ctrl(h).list_host(h)
    assert listing.ok is False
    assert listing.error == "exit 255: connection refused"


def test_kill_unknown_user_raises():
    h = Host(key="local", hostname="localhost", tmux_users=("me",), local=True)
    c = _ctrl(h)
    with pytest.raises(TmuxctlError):
        c.kill_session(h, "stranger", "s")


# ---------- custom tmux sockets (-S) ----------


def _swarm_host(**kw):
    return Host(key="swarm", hostname="localhost", local=True,
                tmux_users=("me",), **kw)


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
    listing = _ctrl(h).list_host(h)
    assert listing.ok is True
    assert listing.sessions == {}
    assert me in listing.errors


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
