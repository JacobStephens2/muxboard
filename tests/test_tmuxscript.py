import getpass
import os
import subprocess

from muxboard.listing import ListedSession
from muxboard.tmuxscript import (
    listing_script,
    read_listing,
    read_sockets,
    socket_read_script,
    tmux_prefix,
)

SEP = "::"
FMT = (
    "#{session_name}::#{session_windows}::#{session_created}::"
    "#{session_attached}::#{session_activity}::#{session_id}"
)


def _run(script, *, path_prefix=None):
    env = dict(os.environ)
    if path_prefix is not None:
        env["PATH"] = f"{path_prefix}{os.pathsep}{env['PATH']}"
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       env=env, timeout=10, check=False)
    return r.stdout


def _refusing_sudo(tmp_path):
    """A directory holding a ``sudo`` that refuses everything."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sudo = bin_dir / "sudo"
    sudo.write_text("#!/bin/sh\nexit 1\n")
    sudo.chmod(0o755)
    return str(bin_dir)


# ---------- tmux prefix ----------


def test_tmux_prefix_for_the_login_user_on_the_default_socket():
    assert tmux_prefix("ops", "", "ops") == "tmux "


def test_tmux_prefix_elevates_to_another_user():
    assert tmux_prefix("deploy", "", "ops") == "sudo -n -u deploy tmux "


def test_tmux_prefix_threads_a_socket():
    assert tmux_prefix("me", "/tmp/s/a.sock", "me") == "tmux -S /tmp/s/a.sock "
    assert tmux_prefix("deploy", "/tmp/a b.sock", "ops") == (
        "sudo -n -u deploy tmux -S '/tmp/a b.sock' "
    )


# ---------- listing script ----------


def test_listing_script_is_unchanged_byte_for_byte():
    script = listing_script({"ops": "", "deploy": "/tmp/a b.sock"}, ("ops", "deploy"), "ops")
    assert script == (
        f"tmux ls -F 'ops::{FMT}' 2>/dev/null || true; "
        "if sudo -n -u deploy true 2>/dev/null; then   "
        f"sudo -n -u deploy tmux -S '/tmp/a b.sock' ls -F 'deploy::{FMT}' 2>/dev/null || true; "
        "else echo '__MUXBOARD_ERR__::deploy::sudo refused'; fi"
    )


def test_listing_script_without_socket_has_no_socket_flag():
    assert "-S " not in listing_script({"me": ""}, ("me",), "me")


def test_listing_script_threads_socket():
    script = listing_script({"me": "/tmp/swarmforge-me/ab12.sock"}, ("me",), "me")
    assert "tmux -S /tmp/swarmforge-me/ab12.sock ls -F" in script


def test_listing_script_skips_users_without_a_resolved_socket():
    script = listing_script({"me": "/tmp/a.sock"}, ("me", "other"), "me")
    assert "/tmp/a.sock" in script
    assert "other" not in script


def test_listing_script_follows_the_hosts_user_order():
    script = listing_script({"b": "", "a": ""}, ("a", "b"), "x")
    assert script.index("-u a ") < script.index("-u b ")


# ---------- read listing ----------


def _row(*cols):
    return SEP.join(cols)


def test_read_listing_valid_output():
    text = "\n".join([
        _row("deploy", "build", "2", "1700000000", "1", "1700000500", "$3"),
        _row("deploy", "a", "1", "5", "0", "6", "$4"),
        _row("__MUXBOARD_ERR__", "ops", "sudo refused"),
    ])
    sessions, errors = read_listing(text)
    assert sessions == {"deploy": [
        ListedSession(user="deploy", name="build", windows=2, created=1700000000,
                      attached=True, activity=1700000500, id="$3"),
        ListedSession(user="deploy", name="a", windows=1, created=5,
                      attached=False, activity=6, id="$4"),
    ]}
    assert errors == {"ops": "sudo refused"}


def test_read_listing_skips_garbage():
    text = "\n".join([
        "not-enough-fields",
        _row("deploy", "s", "two", "5", "0", "6", "$1"),
        "",
    ])
    assert read_listing(text) == ({}, {})


def test_read_listing_survives_a_truncated_marker():
    text = "__MUXBOARD_ERR__::ops\n" + _row("me", "s", "1", "5", "0", "6", "$1")
    sessions, errors = read_listing(text)
    assert list(sessions) == ["me"]
    assert errors == {}


def test_read_listing_tolerates_crlf():
    sessions, _ = read_listing(_row("me", "s", "1", "5", "0", "6", "$1") + "\r\n")
    assert sessions["me"][0].id == "$1"


# ---------- socket-file read script ----------


def test_socket_read_script_reads_capped_single_line():
    script = socket_read_script("/srv/p/.swarmforge/tmux-socket", ("me",), "me")
    assert "/srv/p/.swarmforge/tmux-socket" in script
    assert "head -c" in script  # size cap
    assert "head -n 1" in script  # single-line requirement


def test_socket_read_script_probes_sudo_for_another_user():
    script = socket_read_script("/srv/p/sock", ("someone-else",), "me")
    assert "sudo -n -u someone-else true" in script


# ---------- read sockets ----------


def _sock(user, path):
    return _row("__MUXBOARD_SOCK__", user, path)


def test_read_sockets_valid_and_invalid():
    text = "\n".join([
        _sock("me", "/tmp/swarmforge-me/ab12.sock"),
        _sock("other", ""),
        _sock("third", "/tmp/$(id).sock"),
        "unrelated noise",
    ])
    sockets, errors = read_sockets(text, ("me", "other", "third"))
    assert sockets == {"me": "/tmp/swarmforge-me/ab12.sock"}
    assert errors == {
        "other": "socket file unreadable (missing or empty)",
        "third": "socket file does not contain a valid absolute socket path",
    }


def test_read_sockets_garbage_leaves_every_user_unreadable():
    sockets, errors = read_sockets("garbage\n::\n", ("me", "you"))
    assert sockets == {}
    assert set(errors) == {"me", "you"}


def test_read_sockets_survives_a_truncated_marker():
    sockets, errors = read_sockets("__MUXBOARD_SOCK__::oops\n", ("me",))
    assert sockets == {}
    assert errors == {"me": "socket file unreadable (missing or empty)"}


def test_read_sockets_reports_a_missing_user():
    sockets, errors = read_sockets(_sock("me", "/tmp/a.sock"), ("me", "absent"))
    assert sockets == {"me": "/tmp/a.sock"}
    assert errors == {"absent": "socket file unreadable (missing or empty)"}


def test_read_sockets_ignores_users_it_was_not_asked_for():
    sockets, errors = read_sockets(_sock("stranger", "/tmp/a.sock"), ("me",))
    assert sockets == {}
    assert set(errors) == {"me"}


def test_read_sockets_reports_sudo_refusal():
    text = _row("__MUXBOARD_ERR__", "me", "sudo refused")
    assert read_sockets(text, ("me",)) == ({}, {"me": "sudo refused"})


# ---------- round trips through local bash ----------


def test_socket_file_read_without_sudo_returns_its_path(tmp_path):
    me = getpass.getuser()
    sock_file = tmp_path / "tmux-socket"
    sock_file.write_text("/tmp/swarmforge-me/ab12.sock\nignored second line\n")
    out = _run(socket_read_script(str(sock_file), (me,), me))
    assert read_sockets(out, (me,)) == ({me: "/tmp/swarmforge-me/ab12.sock"}, {})


def test_socket_file_read_of_a_missing_file_is_unreadable(tmp_path):
    me = getpass.getuser()
    out = _run(socket_read_script(str(tmp_path / "nope"), (me,), me))
    assert read_sockets(out, (me,)) == (
        {}, {me: "socket file unreadable (missing or empty)"},
    )


def test_socket_file_read_under_refused_sudo_is_sudo_refused(tmp_path):
    sock_file = tmp_path / "tmux-socket"
    sock_file.write_text("/tmp/a.sock\n")
    script = socket_read_script(str(sock_file), ("someone-else",), "me")
    out = _run(script, path_prefix=_refusing_sudo(tmp_path))
    assert read_sockets(out, ("someone-else",)) == ({}, {"someone-else": "sudo refused"})


def test_listing_under_refused_sudo_is_sudo_refused(tmp_path):
    script = listing_script({"someone-else": ""}, ("someone-else",), "me")
    out = _run(script, path_prefix=_refusing_sudo(tmp_path))
    assert read_listing(out) == ({}, {"someone-else": "sudo refused"})
