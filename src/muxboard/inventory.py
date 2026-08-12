"""Host inventory for muxboard.

A muxboard manages one or more hosts. Each :class:`Host` says how to reach a
machine over SSH (or that it is the local machine) and which Unix users' tmux
sockets the board is allowed to touch. The single-host case is the trivial
``n == 1`` instance of the same model the fleet case uses - there is no
separate "single host" code path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# A board key appears in URLs (/<key>/<user>/<name>/attach) and as a dict key,
# so keep it to a conservative url-safe slug.
_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# tmux user names map to real Unix accounts that the board runs `sudo -n -u`
# against. Restrict to the portable POSIX login-name character set so a typo
# can never expand into shell syntax even before shlex.quote() runs.
_USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")

# One path segment of a tmux socket path. Socket paths are interpolated into a
# shell script that already runs through `sudo -n -u`, and one form of them is
# read out of a file some other tool wrote, so they get their own whitelist
# instead of relying on shlex.quote() alone. `:` is excluded deliberately: a
# resolved path travels back from the host inside a line this package splits
# on `::` (see _SEP in tmuxctl).
_SOCKET_SEGMENT_RE = re.compile(r"[A-Za-z0-9._@%+=,-]{1,255}")

# Generous next to PATH_MAX but far below it - a socket path this long is a
# configuration mistake, or something worse read out of a file.
SOCKET_PATH_MAX = 1024

# Ceiling on one `session_order` entry. Session names are a tmux concept and
# muxboard never interpolates an order entry into a command - the entries are
# compared against names in Python and nothing else - so the only rule worth
# enforcing is that an entry is a non-empty string of sane length.
ORDER_ENTRY_MAX = 128


def valid_socket_path(path: str) -> bool:
    """True for an absolute path safe to interpolate as ``tmux -S <path>``.

    Requires a leading ``/``, no trailing ``/``, no empty / ``.`` / ``..``
    segments, and only ``[A-Za-z0-9._@%+=,-]`` inside each segment - no
    whitespace, quotes, or shell metacharacters of any kind.
    """
    if not path or len(path) > SOCKET_PATH_MAX:
        return False
    if not path.startswith("/") or path.endswith("/"):
        return False
    # fullmatch, not match: re's `$` also matches just before a trailing
    # newline, which would let "/tmp/a.sock\n" through the whitelist.
    return all(
        _SOCKET_SEGMENT_RE.fullmatch(seg) and seg not in (".", "..")
        for seg in path[1:].split("/")
    )


@dataclass(frozen=True)
class Host:
    """One manageable machine.

    Args:
        key: Stable url-safe identifier (``web1``, ``db-primary``). Used in
            routes and as the inventory key. Must be unique within a board.
        hostname: SSH target host. For ``local=True`` this is informational
            only (commands run on the box muxboard itself runs on).
        label: Human-friendly name shown in the UI. Defaults to ``hostname``.
        tmux_users: Unix users whose tmux sockets this board may list, create,
            kill, and attach. The board's SSH login user is reached directly;
            every *other* user is reached with ``sudo -n -u <user>``, so those
            users require a NOPASSWD sudo rule from the login user. Empty means
            the host is inventory-only and exposes no tmux panel.
        ssh_user: Login user for SSH. Ignored when ``local=True``.
        ssh_port: SSH port.
        password_env: Name of an environment variable holding the SSH password.
            When set, muxboard authenticates with ``sshpass`` reading that var.
            Mutually exclusive with ``ssh_key``.
        ssh_key: Path to a private key for key-based SSH. Falls back to the
            board-level default key when neither this nor ``password_env`` is
            set.
        local: Run commands on the local machine with no SSH hop. This is the
            common single-host deployment (muxboard runs on the same box whose
            tmux you manage). See the README note on systemd ``PrivateTmp`` if
            your sockets are invisible under ``local=True``.
        tmux_socket: Absolute path of a non-default tmux server socket, threaded
            through every tmux call as ``-S <path>``. Unset means the user's
            default socket, which is the behaviour of every host that predates
            this option. A host entry addresses exactly one tmux server, so a
            machine running both a default server and a private one is two
            ``Host`` entries with different ``key``s (and a ``label`` to tell
            them apart). Mutually exclusive with ``tmux_socket_file``.
        tmux_socket_file: Absolute path of a *file on the host* whose first line
            is the socket path to use. For tools that mint a socket path at
            runtime and publish it - swarm-forge writes
            ``<project>/.swarmforge/tmux-socket`` - this stays correct across
            restarts without the operator knowing how the name is derived. The
            file is read as the tmux user, capped at 4 KiB, and its first line
            must satisfy the same validation as ``tmux_socket``. Mutually
            exclusive with ``tmux_socket``.
        session_order: Session names (or name prefixes) that sort first, in the
            order given, ahead of everything else. For a host whose sessions are
            stages of a pipeline - ``specifier``, ``coder``, ``cleaner`` - the
            meaningful order is the order work flows through them, which no sort
            derived from the names themselves can recover. Sessions that match
            no entry follow the named ones, still numeric-aware-sorted among
            themselves; a host that sets nothing here orders exactly as it did
            before this option existed. Matching is first-entry-wins on equality
            or prefix, so list the more specific entry first when two overlap.
            Presentation only: this never affects scoping, auth, or which
            sessions a principal can see.
    """

    key: str
    hostname: str
    label: str = ""
    tmux_users: tuple[str, ...] = field(default_factory=tuple)
    ssh_user: str = ""
    ssh_port: int = 22
    password_env: str = ""
    ssh_key: str = ""
    local: bool = False
    tmux_socket: str = ""
    tmux_socket_file: str = ""
    session_order: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not _KEY_RE.match(self.key):
            raise ValueError(
                f"host key {self.key!r} must be 1-64 chars of [A-Za-z0-9_-]"
            )
        if not self.hostname:
            raise ValueError(f"host {self.key!r} needs a hostname")
        for u in self.tmux_users:
            if not _USER_RE.match(u):
                raise ValueError(
                    f"host {self.key!r}: tmux user {u!r} is not a valid Unix "
                    "login name"
                )
        if self.password_env and self.ssh_key:
            raise ValueError(
                f"host {self.key!r}: set only one of password_env / ssh_key"
            )
        if not self.local and not self.ssh_user:
            raise ValueError(
                f"host {self.key!r}: ssh_user is required unless local=True"
            )
        if self.tmux_socket and self.tmux_socket_file:
            raise ValueError(
                f"host {self.key!r}: set only one of tmux_socket / tmux_socket_file"
            )
        for fieldname in ("tmux_socket", "tmux_socket_file"):
            value = getattr(self, fieldname)
            if value and not valid_socket_path(value):
                raise ValueError(
                    f"host {self.key!r}: {fieldname} {value!r} must be an absolute "
                    f"path of at most {SOCKET_PATH_MAX} chars, built from "
                    "[A-Za-z0-9._@%+=,-] segments (no '..', no spaces, no shell "
                    "metacharacters)"
                )
        seen: set[str] = set()
        for entry in self.session_order:
            if not entry or len(entry) > ORDER_ENTRY_MAX:
                raise ValueError(
                    f"host {self.key!r}: session_order entries must be 1-"
                    f"{ORDER_ENTRY_MAX} chars; got {entry!r}"
                )
            if entry in seen:
                raise ValueError(
                    f"host {self.key!r}: duplicate session_order entry {entry!r}"
                )
            seen.add(entry)

    @property
    def display(self) -> str:
        return self.label or self.hostname


def index_by_key(hosts: list[Host]) -> dict[str, Host]:
    out: dict[str, Host] = {}
    for h in hosts:
        if h.key in out:
            raise ValueError(f"duplicate host key {h.key!r}")
        out[h.key] = h
    return out
