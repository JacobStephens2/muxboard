"""The scripts the tmux controller runs on a Host, and the reading of their output.

Each script sits next to the reader that has to agree with it, so the wire
protocol between them - the separator, the marker lines, the sudo probe - lives
here and nowhere else:

  - :func:`socket_read_script` / :func:`read_sockets`: read each Tmux user's
    socket path out of a Host's ``tmux_socket_file``.
  - :func:`listing_script` / :func:`read_listing`: list each Tmux user's
    Sessions.
  - :func:`tmux_prefix`: the start of a single-user tmux command, for Kill,
    create and Attach.

Everything here takes plain values - the login user, the Tmux users, paths -
and knows nothing about how a script reaches a Host. Every untrusted value is
``shlex.quote``-d before it lands in a script.
"""

from __future__ import annotations

import logging
import shlex
from typing import Mapping, Optional, Sequence

from .inventory import valid_socket_path
from .listing import ListedSession

log = logging.getLogger("muxboard.tmuxscript")

# Field separator for every line a script emits. Must be printable: tmux 3.4+
# escapes non-printable bytes in format output as octal (so a raw US 0x1F
# becomes the four characters "\037" and parsing silently fails). Session names
# cannot contain `.` or `:`, Tmux user names cannot contain `:`, and the Host
# inventory keeps `:` out of socket paths, so a double-colon is unambiguous
# across every field we emit.
_SEP = "::"

# `tmux ls -F` format, behind the Tmux user the listing script prepends.
# Order: user <SEP> name <SEP> windows <SEP> created <SEP> attached <SEP> activity <SEP> id
_FMT = (
    "#{session_name}" + _SEP +
    "#{session_windows}" + _SEP +
    "#{session_created}" + _SEP +
    "#{session_attached}" + _SEP +
    "#{session_activity}" + _SEP +
    "#{session_id}"
)

# Marker for a Tmux user the login user cannot become: ``<ERR><SEP><user><SEP>
# sudo refused``. Both scripts emit it from one probe, so "cannot become this
# user" stays distinguishable from "the file is not there" or "no Sessions".
_ERR_MARK = "__MUXBOARD_ERR__"
_SUDO_REFUSED = "sudo refused"

# Marker for the socket-path lines the socket-file read script emits, and the
# ceiling on how much of that file we are willing to read. The file belongs to
# whatever tool minted the socket, so treat it as untrusted input: capped,
# first line only, and validated against the same whitelist as a literal path.
_SOCK_MARK = "__MUXBOARD_SOCK__"
_SOCKET_FILE_MAX_BYTES = 4096

_SOCKET_UNREADABLE = "socket file unreadable (missing or empty)"
_SOCKET_INVALID = "socket file does not contain a valid absolute socket path"


def tmux_prefix(user: str, socket: str, login_user: str) -> str:
    """Everything a single-user tmux command needs before its subcommand: the
    sudo prefix, ``tmux``, and the ``-S`` flag when ``socket`` is not the
    default (empty string)."""
    return f"{_as_user_prefix(user, login_user)}tmux {_socket_flag(socket)}"


def socket_read_script(socket_file: str, users: Sequence[str], login_user: str) -> str:
    """Script emitting one ``__MUXBOARD_SOCK__<SEP><user><SEP><path>`` line per
    user in ``users``, read from ``socket_file``.

    The file is read *as the tmux user* - the same account the tmux command
    will run as - so muxboard never reads a path out of a file that user could
    not read itself. ``head -c`` caps the read and ``head -n 1`` enforces the
    single-line rule; :func:`read_sockets` validates the value before it is
    ever interpolated into a tmux command.
    """
    quoted_file = shlex.quote(socket_file)
    parts: list[str] = []
    for u in users:
        prefix = _as_user_prefix(u, login_user)
        read = (
            f"{prefix}head -c {_SOCKET_FILE_MAX_BYTES} -- {quoted_file} "
            f"2>/dev/null | head -n 1"
        )
        emit = (
            f"printf '{_SOCK_MARK}{_SEP}%s{_SEP}%s\\n' {shlex.quote(u)} \"$({read})\""
        )
        parts.append(_as_user(u, prefix, emit))
    return "; ".join(parts)


def read_sockets(
    text: str, users: Sequence[str]
) -> tuple[dict[str, str], dict[str, str]]:
    """Split :func:`socket_read_script` output into ``(sockets_by_user,
    errors_by_user)``.

    Every user in ``users`` lands in exactly one of the two, so a user whose
    socket file is missing shows up as an error in the UI rather than as a
    deceptively empty session list.
    """
    seen: dict[str, str] = {}
    refused: set[str] = set()
    mark = _SOCK_MARK + _SEP
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        refused_user = _refused_user(line)
        if refused_user is not None:
            refused.add(refused_user)
            continue
        if not line.startswith(mark):
            continue
        cols = line.split(_SEP, 2)
        if len(cols) < 3:
            # A truncated marker line is not worth killing the sweep over;
            # the user it belonged to falls through to an error below.
            log.debug("muxboard: unparseable socket line %r", raw)
            continue
        seen[cols[1]] = cols[2]
    sockets: dict[str, str] = {}
    errors: dict[str, str] = {}
    for u in users:
        path = seen.get(u, "")
        if u in refused:
            errors[u] = _SUDO_REFUSED
        elif not path:
            errors[u] = _SOCKET_UNREADABLE
        elif not valid_socket_path(path):
            log.warning("muxboard: rejected socket path %r from socket file", path)
            errors[u] = _SOCKET_INVALID
        else:
            sockets[u] = path
    return sockets, errors


def listing_script(
    sockets: Mapping[str, str], users: Sequence[str], login_user: str
) -> str:
    """Script emitting one ``<user><SEP><name><SEP>...<SEP><id>`` line per
    Session, in the order of ``users`` (the Host's Tmux users).

    ``sockets`` maps user to the already-resolved socket path (empty string
    for the default socket). Only the users it contains are listed; a user
    whose socket could not be resolved is reported as an error by the caller.
    """
    parts: list[str] = []
    for u in users:
        if u not in sockets:
            continue
        prefix = _as_user_prefix(u, login_user)
        per_user_fmt = f"{u}{_SEP}{_FMT}"
        ls = (
            f"{tmux_prefix(u, sockets[u], login_user)}ls -F '{per_user_fmt}' "
            "2>/dev/null || true"
        )
        # The two leading spaces keep the generated text what it has always been.
        parts.append(_as_user(u, prefix, f"  {ls}" if prefix else ls))
    return "; ".join(parts)


def read_listing(
    text: str,
) -> tuple[dict[str, list[ListedSession]], dict[str, str]]:
    """Split :func:`listing_script` output into ``(sessions_by_user,
    errors_by_user)``, each user's Sessions in the order tmux listed them."""
    sessions_by_user: dict[str, list[ListedSession]] = {}
    errors: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        if not line:
            continue
        refused_user = _refused_user(line)
        if refused_user is not None:
            errors[refused_user] = _SUDO_REFUSED
            continue
        cols = line.split(_SEP)
        if len(cols) < 7:
            log.debug("muxboard: unparseable tmux list line %r", raw)
            continue
        user, name, windows, created, attached, activity, sid = cols[:7]
        try:
            entry = ListedSession(
                user=user,
                name=name,
                windows=int(windows),
                created=int(created),
                attached=int(attached) > 0,
                activity=int(activity),
                id=sid,
            )
        except ValueError:
            log.debug("muxboard: bad ints in tmux list line %r", raw)
            continue
        sessions_by_user.setdefault(user, []).append(entry)
    return sessions_by_user, errors


def _as_user(user: str, prefix: str, command: str) -> str:
    """``command``, run only once the login user is known to become ``user``.

    With no sudo ``prefix`` (``user`` is the login user) there is nothing to
    probe. Otherwise a refused ``sudo -n`` emits the refusal marker instead.
    """
    if not prefix:
        return command
    return (
        f"if {prefix}true 2>/dev/null; then {command}; "
        f"else echo '{_ERR_MARK}{_SEP}{user}{_SEP}{_SUDO_REFUSED}'; fi"
    )


def _refused_user(line: str) -> Optional[str]:
    """The Tmux user a refusal marker line names, or ``None`` for any other line."""
    if not line.startswith(_ERR_MARK + _SEP):
        return None
    cols = line.split(_SEP, 2)
    if len(cols) < 3 or not cols[1]:
        log.debug("muxboard: unparseable refusal line %r", line)
        return None
    return cols[1]


def _as_user_prefix(target_user: str, login_user: str) -> str:
    """Shell prefix that elevates from login_user to target_user.

    Returns ``"sudo -n -u <user> "`` (with trailing space), or ``""`` when the
    target is the login user.
    """
    if target_user == login_user:
        return ""
    return f"sudo -n -u {shlex.quote(target_user)} "


def _socket_flag(path: str) -> str:
    """``"-S <path> "`` (with trailing space) for a non-default tmux socket.

    Empty string for the default socket, which keeps every generated command
    byte-for-byte what it was before sockets were configurable.
    """
    if not path:
        return ""
    return f"-S {shlex.quote(path)} "
