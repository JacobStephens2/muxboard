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
