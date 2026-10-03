"""tmux session management across a muxboard's hosts.

For each :class:`~muxboard.inventory.Host` with ``tmux_users`` set, the board
can list those users' tmux sessions, create one, kill one, and (via
:mod:`muxboard.ttyproxy`) attach a live web terminal.

User scoping:
  - For the host's own SSH login user, tmux runs directly.
  - To reach a *different* user's tmux socket we run ``sudo -n -u <user> tmux
    ...``. That user therefore needs a NOPASSWD sudo rule from the login user.
  - A ``local=True`` host shells out locally with no SSH hop.

The controller lists one Host at a time; :mod:`muxboard.sweep` keeps the
latest listing of every Host and renews it in the background.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import time
from typing import Optional

from . import tmuxscript
from .inventory import Host, index_by_key
from .listing import Listing

# New-session name whitelist: liberal, but no shell metas, no `.` (pane/window
# selector), `:` (target separator), or spaces.
_NEW_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class TmuxctlError(Exception):
    """Recoverable failure surfaced to the API caller."""


def valid_new_session_name(name: str) -> bool:
    return bool(_NEW_SESSION_RE.match(name))


class TmuxController:
    """Lists, creates, kills and attaches tmux Sessions on a board's Hosts."""

    def __init__(
        self,
        hosts: list[Host],
        *,
        ssh_key: Optional[str] = None,
        ssh_timeout: int = 8,
        strict_host_key_checking: bool = True,
    ) -> None:
        self.hosts = hosts
        self.by_key = index_by_key(hosts)
        self.default_ssh_key = ssh_key
        self.ssh_timeout = ssh_timeout
        self.strict = strict_host_key_checking

    # ---------- lookup ----------

    def get_host(self, key: str) -> Optional[Host]:
        return self.by_key.get(key)

    def is_tmux_host(self, key: str) -> bool:
        h = self.by_key.get(key)
        return bool(h and h.tmux_users)

    def known_user(self, key: str, user: str) -> bool:
        h = self.by_key.get(key)
        return bool(h and user in h.tmux_users)

    def _login_user(self, host: Host) -> str:
        # For local hosts the "login user" is whoever the process runs as;
        # commands without a sudo prefix run as that account.
        if host.local:
            return _current_username()
        return host.ssh_user

    # ---------- transport ----------

    def _build_argv(
        self, host: Host, remote_script: str, *, interactive: bool = False
    ) -> tuple[list[str], dict[str, str]]:
        """Build a local subprocess argv (+ env additions) running
        ``remote_script`` on ``host``.

        ``interactive=True`` allocates a TTY (``ssh -tt``) for the attach
        bridge; list/kill/create use ``False``. No command ever runs through a
        shell on this side (no ``shell=True``); the remote string is the SSH
        command argument or the ``bash -c`` script, and every untrusted value
        inside it is ``shlex.quote``-d by the caller.
        """
        if host.local:
            return ["bash", "-c", remote_script], {}

        base: list[str]
        env_add: dict[str, str] = {}
        if host.password_env:
            password = os.environ.get(host.password_env)
            if not password:
                raise TmuxctlError(
                    f"host {host.key!r}: env var {host.password_env!r} is empty "
                    "(no SSH password available)"
                )
            base = [
                "sshpass", "-e", "ssh",
                "-o", "BatchMode=no",
            ]
            env_add = {"SSHPASS": password}
        else:
            key_path = host.ssh_key or self.default_ssh_key
            if not key_path:
                raise TmuxctlError(
                    f"host {host.key!r}: no password_env, no ssh_key, and no "
                    "board-level ssh_key default"
                )
            base = [
                "ssh",
                "-i", key_path,
                "-o", "IdentitiesOnly=yes",
                "-o", "BatchMode=yes",
            ]
        base += [
            "-o", f"StrictHostKeyChecking={'yes' if self.strict else 'no'}",
            "-o", f"ConnectTimeout={self.ssh_timeout}",
            "-o", "ServerAliveInterval=30",
            "-p", str(host.ssh_port),
        ]
        if interactive:
            base += ["-tt"]
        base += [f"{host.ssh_user}@{host.hostname}", remote_script]
        return base, env_add

    # ---------- socket resolution ----------

    def _resolve_sockets(
        self, host: Host, users: Optional[tuple[str, ...]] = None
    ) -> tuple[dict[str, str], dict[str, str]]:
        """Socket path to use per user, plus per-user resolution failures.

        An empty string means "the user's default socket" - no ``-S`` at all.
        Raises :class:`TmuxctlError` only for transport failures (the host is
        unreachable); a file that is missing or garbage is a per-user error.

        Only ``tmux_socket_file`` costs anything: a literal ``tmux_socket`` and
        the default-socket case both answer locally, so a host that configures
        no socket runs exactly the commands it ran before this existed. A
        socket-file host pays one extra exec per sweep (and one per mutation or
        attach) to read the file *before* a path can be interpolated into a
        tmux command - that ordering is what lets the value be validated in
        Python instead of by a shell test.
        """
        wanted = tuple(users) if users is not None else tuple(host.tmux_users)
        if host.tmux_socket:
            return {u: host.tmux_socket for u in wanted}, {}
        if not host.tmux_socket_file:
            return {u: "" for u in wanted}, {}
        script = tmuxscript.socket_read_script(
            host.tmux_socket_file, wanted, self._login_user(host)
        )
        argv, env_add = self._build_argv(host, script)
        env = {**os.environ, **env_add} if env_add else None
        try:
            r = subprocess.run(
                argv, capture_output=True, text=True,
                # Same budget _run_mutation gives a one-shot command: this is a
                # single `head`, not a sweep of every user's sessions.
                timeout=self.ssh_timeout * 2, env=env, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TmuxctlError(f"socket file read timed out: {exc}") from exc
        except OSError as exc:
            raise TmuxctlError(
                f"socket file read failed: {exc.__class__.__name__}: {exc}"
            ) from exc
        if r.returncode != 0 and not r.stdout.strip():
            tail = (r.stderr.strip().splitlines() or ["unknown"])[-1]
            raise TmuxctlError(f"socket file read failed: exit {r.returncode}: {tail}")
        return tmuxscript.read_sockets(r.stdout, wanted)

    def _tmux_for(self, host: Host, user: str) -> str:
        """Everything a single-user tmux command needs before its subcommand:
        the sudo prefix, ``tmux``, and the ``-S`` flag when one applies.

        Raises :class:`TmuxctlError` if the user's socket cannot be resolved -
        muxboard never silently falls back to the default socket, because
        talking to the wrong tmux server is worse than refusing.
        """
        sockets, errors = self._resolve_sockets(host, (user,))
        if user in errors:
            raise TmuxctlError(f"{host.key}/{user}: {errors[user]}")
        return tmuxscript.tmux_prefix(user, sockets[user], self._login_user(host))

    # ---------- list ----------

    def list_host(self, host: Host) -> Listing:
        """List ``host``'s Sessions. Every failure is a Listing too, never raised."""
        start = time.monotonic()
        if not host.tmux_users:
            return Listing.worked(host, sessions={}, errors={}, elapsed_ms=0)
        try:
            sockets, socket_errors = self._resolve_sockets(host)
        except TmuxctlError as exc:
            return self._list_fail(host, str(exc), start)
        if not sockets:
            # Every user's socket file failed to resolve; there is nothing to
            # ask tmux, but the reason belongs in the UI.
            return Listing.worked(
                host, sessions={}, errors=socket_errors, elapsed_ms=_elapsed_ms(start)
            )
        script = tmuxscript.listing_script(sockets, host.tmux_users, self._login_user(host))
        argv, env_add = self._build_argv(host, script)
        env = {**os.environ, **env_add} if env_add else None
        try:
            r = subprocess.run(
                argv, capture_output=True, text=True,
                timeout=self.ssh_timeout * 3, env=env, check=False,
            )
        except subprocess.TimeoutExpired:
            return self._list_fail(host, f"timeout (>{self.ssh_timeout * 3}s)", start)
        except OSError as exc:
            return self._list_fail(host, f"{exc.__class__.__name__}: {exc}", start)
        if r.returncode != 0 and not r.stdout.strip():
            tail = (r.stderr.strip().splitlines() or ["unknown"])[-1]
            return self._list_fail(host, f"exit {r.returncode}: {tail}", start)
        sessions, errors = tmuxscript.read_listing(r.stdout)
        return Listing.worked(
            host,
            sessions=sessions,
            errors={**socket_errors, **errors},
            elapsed_ms=_elapsed_ms(start),
        )

    @staticmethod
    def _list_fail(host: Host, error: str, start: float) -> Listing:
        return Listing.failed(host, error, elapsed_ms=_elapsed_ms(start))

    # ---------- kill / create ----------

    def kill_session(self, host: Host, user: str, name: str) -> None:
        if user not in host.tmux_users:
            raise TmuxctlError(f"unknown user {user!r} on {host.key}")
        script = f"{self._tmux_for(host, user)}kill-session -t {shlex.quote(name)}"
        self._run_mutation(host, script)

    def create_session(
        self, host: Host, user: str, name: str, command: Optional[str] = None
    ) -> None:
        if user not in host.tmux_users:
            raise TmuxctlError(f"unknown user {user!r} on {host.key}")
        if not valid_new_session_name(name):
            raise TmuxctlError(
                "name must be 1-64 chars of [A-Za-z0-9_-] (no dots, colons, spaces)"
            )
        script = f"{self._tmux_for(host, user)}new-session -d -s {shlex.quote(name)}"
        if command:
            # tmux re-parses this as a shell command for the new window. The
            # caller is already an authorized principal, so we do not second-
            # guess pipelines; we only shlex-quote it as one argv to tmux.
            script += " " + shlex.quote(command)
        self._run_mutation(host, script)

    def _run_mutation(self, host: Host, script: str) -> None:
        argv, env_add = self._build_argv(host, script)
        env = {**os.environ, **env_add} if env_add else None
        try:
            r = subprocess.run(
                argv, capture_output=True, text=True,
                timeout=self.ssh_timeout * 2, env=env, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TmuxctlError(f"timeout: {exc}") from exc
        except OSError as exc:
            raise TmuxctlError(f"{exc.__class__.__name__}: {exc}") from exc
        if r.returncode != 0:
            msg = r.stderr.strip() or r.stdout.strip() or f"exit {r.returncode}"
            raise TmuxctlError(msg.splitlines()[-1][:240])

    # ---------- attach ----------

    def attach_argv(
        self, host: Host, user: str, name: str
    ) -> tuple[list[str], dict[str, str]]:
        """Local argv (+ env for SSHPASS) to spawn under a PTY for a live
        attach. Uses ``tmux attach -t <name>`` without ``-d`` so other clients
        already attached stay attached (read-along)."""
        if user not in host.tmux_users:
            raise TmuxctlError(f"unknown user {user!r} on {host.key}")
        remote = f"{self._tmux_for(host, user)}attach -t {shlex.quote(name)}"
        return self._build_argv(host, remote, interactive=True)


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def _current_username() -> str:
    import getpass
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001
        return os.environ.get("USER", "root")

