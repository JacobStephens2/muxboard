"""tmux session management across a muxboard's hosts.

For each :class:`~muxboard.inventory.Host` with ``tmux_users`` set, the board
can list those users' tmux sessions, create one, kill one, and (via
:mod:`muxboard.ttyproxy`) attach a live web terminal.

User scoping:
  - For the host's own SSH login user, tmux runs directly.
  - To reach a *different* user's tmux socket we run ``sudo -n -u <user> tmux
    ...``. That user therefore needs a NOPASSWD sudo rule from the login user.
  - A ``local=True`` host shells out locally with no SSH hop.

A background sweep refreshes an in-memory store every ``interval`` seconds;
request handlers read the store and never block on a sweep.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

from .inventory import Host, index_by_key, valid_socket_path

log = logging.getLogger("muxboard.tmuxctl")

# Field separator for `tmux ls -F`. Must be printable: tmux 3.4+ escapes
# non-printable bytes in format output as octal (so a raw US 0x1F becomes the
# four characters "\037" and parsing silently fails). Session names cannot
# contain `.` or `:`, so a double-colon is unambiguous across every field we
# emit (name, ints, `$id`, and the user prefix we prepend).
# Order: name <SEP> windows <SEP> created <SEP> attached <SEP> activity <SEP> id
_SEP = "::"
_FMT = (
    "#{session_name}" + _SEP +
    "#{session_windows}" + _SEP +
    "#{session_created}" + _SEP +
    "#{session_attached}" + _SEP +
    "#{session_activity}" + _SEP +
    "#{session_id}"
)

# New-session name whitelist: liberal, but no shell metas, no `.` (pane/window
# selector), `:` (target separator), or spaces.
_NEW_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Marker for the socket-path lines a `tmux_socket_file` host emits, and the
# ceiling on how much of that file we are willing to read. The file belongs to
# whatever tool minted the socket, so treat it as untrusted input: capped,
# first line only, and validated against the same whitelist as a literal path.
_SOCK_MARK = "__MUXBOARD_SOCK__"
_SOCKET_FILE_MAX_BYTES = 4096

# Stand-in the read script emits when sudo itself is refused, so "cannot become
# this user" stays distinguishable from "the file is not there". Not a valid
# socket path (no leading /), so it can never be mistaken for one.
_SOCK_DENIED = "__MUXBOARD_SUDO_REFUSED__"

_SOCKET_UNREADABLE = "socket file unreadable (missing or empty)"
_SOCKET_INVALID = "socket file does not contain a valid absolute socket path"


class TmuxctlError(Exception):
    """Recoverable failure surfaced to the API caller."""


def valid_new_session_name(name: str) -> bool:
    return bool(_NEW_SESSION_RE.match(name))


def _natural_key(name: str) -> list[tuple[int, int, str]]:
    """Numeric-aware sort key for tmux session names.

    Names like ``1``, ``2``, ``22`` are common in operator dashboards; natural
    ordering keeps them in numeric order instead of lexicographic order.
    """
    return [
        (0, int(part), "") if part.isdigit() else (1, 0, part.lower())
        for part in re.split(r"(\d+)", name)
        if part
    ]


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


class TmuxController:
    """Owns a board's host inventory, the background sweep, and the store."""

    def __init__(
        self,
        hosts: list[Host],
        *,
        ssh_key: Optional[str] = None,
        ssh_timeout: int = 8,
        interval: int = 60,
        strict_host_key_checking: bool = True,
    ) -> None:
        self.hosts = hosts
        self.by_key = index_by_key(hosts)
        self.default_ssh_key = ssh_key
        self.ssh_timeout = ssh_timeout
        self.interval = interval
        self.strict = strict_host_key_checking
        self._store = _TmuxStore(hosts)
        self._thread_started = False
        self._thread_lock = threading.Lock()

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

    def _socket_read_script(self, host: Host, users: tuple[str, ...]) -> str:
        """Remote script emitting ``__MUXBOARD_SOCK__<SEP><user><SEP><path>``
        for each of ``users`` on a ``tmux_socket_file`` host.

        The file is read *as the tmux user* - the same account the tmux command
        will run as - so muxboard never reads a path out of a file that user
        could not read itself. ``head -c`` caps the read and ``head -n 1``
        enforces the single-line rule; the value is validated in Python by
        :func:`~muxboard.inventory.valid_socket_path` before it is ever
        interpolated into a tmux command.

        Sudo refusal is probed separately, the same way :meth:`_list_script`
        does it, so "cannot become this user" and "the file is not there" stay
        two different facts in the UI instead of collapsing into one empty read.
        """
        login = self._login_user(host)
        quoted_file = shlex.quote(host.tmux_socket_file)
        parts: list[str] = []
        for u in users:
            prefix = _as_user_prefix(u, login)
            read = (
                f"{prefix}head -c {_SOCKET_FILE_MAX_BYTES} -- {quoted_file} "
                f"2>/dev/null | head -n 1"
            )
            emit = f"printf '{_SOCK_MARK}{_SEP}%s{_SEP}%s\\n' {shlex.quote(u)}"
            if prefix:
                parts.append(
                    f"if {prefix}true 2>/dev/null; then {emit} \"$({read})\"; "
                    f"else {emit} '{_SOCK_DENIED}'; fi"
                )
            else:
                parts.append(f"{emit} \"$({read})\"")
        return "; ".join(parts)

    @staticmethod
    def _parse_socket_output(
        text: str, users: tuple[str, ...]
    ) -> tuple[dict[str, str], dict[str, str]]:
        """Split socket-read output into ``(sockets_by_user, errors_by_user)``.

        Every requested user lands in exactly one of the two dicts, so a user
        whose socket file is missing shows up as an error in the UI rather than
        as a deceptively empty session list.
        """
        seen: dict[str, str] = {}
        mark = _SOCK_MARK + _SEP
        for raw in text.splitlines():
            line = raw.rstrip("\r")
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
            if path == _SOCK_DENIED:
                errors[u] = "sudo refused"
            elif not path:
                errors[u] = _SOCKET_UNREADABLE
            elif not valid_socket_path(path):
                log.warning("muxboard: rejected socket path %r from socket file", path)
                errors[u] = _SOCKET_INVALID
            else:
                sockets[u] = path
        return sockets, errors

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
        script = self._socket_read_script(host, wanted)
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
        return self._parse_socket_output(r.stdout, wanted)

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
        prefix = _as_user_prefix(user, self._login_user(host))
        return f"{prefix}tmux {_socket_flag(sockets[user])}"

    # ---------- list ----------

    def _list_script(self, host: Host, sockets: dict[str, str]) -> str:
        """Remote script emitting one line per session:
            <user><US><name><US><windows><US><created><US><attached><US><activity><US><id>
        plus ``__MUXBOARD_ERR__<US><user><US><msg>`` marker lines when a
        user's sudo is refused (so the UI shows "permission denied" instead of
        a deceptively empty list). The literal 0x1F byte survives SSH transport
        unchanged inside the single-quoted format string.

        ``sockets`` maps user to the already-resolved socket path (empty string
        for the default socket). Only the users it contains are listed; a user
        whose socket could not be resolved is reported as an error instead.
        """
        login = self._login_user(host)
        parts: list[str] = []
        for u in host.tmux_users:
            if u not in sockets:
                continue
            prefix = _as_user_prefix(u, login)
            sock = _socket_flag(sockets[u])
            per_user_fmt = f"{u}{_SEP}{_FMT}"
            if prefix:
                parts.append(
                    f"if {prefix}true 2>/dev/null; then "
                    f"  {prefix}tmux {sock}ls -F '{per_user_fmt}' 2>/dev/null || true; "
                    f"else echo '__MUXBOARD_ERR__{_SEP}{u}{_SEP}sudo refused'; fi"
                )
            else:
                parts.append(f"tmux {sock}ls -F '{per_user_fmt}' 2>/dev/null || true")
        return "; ".join(parts)

    @staticmethod
    def _parse_list_output(text: str, *, host_key: str) -> dict[str, Any]:
        sessions_by_user: dict[str, list[dict[str, Any]]] = {}
        errors: dict[str, str] = {}
        err_prefix = "__MUXBOARD_ERR__" + _SEP
        for raw in text.splitlines():
            line = raw.rstrip("\r")
            if not line:
                continue
            if line.startswith(err_prefix):
                _, user, msg = line.split(_SEP, 2)
                errors[user] = msg
                continue
            cols = line.split(_SEP)
            if len(cols) < 7:
                log.debug("tmux list (%s): unparseable line %r", host_key, raw)
                continue
            user, name, windows, created, attached, activity, sid = cols[:7]
            try:
                entry = {
                    "user": user,
                    "name": name,
                    "windows": int(windows),
                    "created": int(created),
                    "attached": int(attached) > 0,
                    "activity": int(activity),
                    "id": sid,
                }
            except ValueError:
                log.debug("tmux list (%s): bad ints in %r", host_key, raw)
                continue
            sessions_by_user.setdefault(user, []).append(entry)
        for sessions in sessions_by_user.values():
            sessions.sort(key=lambda e: _natural_key(e["name"]))
        return {"sessions": sessions_by_user, "errors": errors}

    def list_host(self, host: Host) -> dict[str, Any]:
        """Return ``{ok, error, sessions:{user:[...]}, errors:{user:msg}, users, sweep_ms}``."""
        start = time.monotonic()
        if not host.tmux_users:
            return {
                "ok": True, "error": None, "sessions": {}, "errors": {},
                "users": [], "sweep_ms": 0,
            }
        try:
            sockets, socket_errors = self._resolve_sockets(host)
        except TmuxctlError as exc:
            return self._list_fail(host, str(exc), start)
        if not sockets:
            # Every user's socket file failed to resolve; there is nothing to
            # ask tmux, but the reason belongs in the UI.
            return {
                "ok": True, "error": None, "sessions": {}, "errors": socket_errors,
                "users": list(host.tmux_users),
                "sweep_ms": int((time.monotonic() - start) * 1000),
            }
        argv, env_add = self._build_argv(host, self._list_script(host, sockets))
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
        parsed = self._parse_list_output(r.stdout, host_key=host.key)
        return {
            "ok": True, "error": None,
            "sessions": parsed["sessions"],
            "errors": {**socket_errors, **parsed["errors"]},
            "users": list(host.tmux_users),
            "sweep_ms": int((time.monotonic() - start) * 1000),
        }

    @staticmethod
    def _list_fail(host: Host, error: str, start: float) -> dict[str, Any]:
        return {
            "ok": False, "error": error, "sessions": {}, "errors": {},
            "users": list(host.tmux_users),
            "sweep_ms": int((time.monotonic() - start) * 1000),
        }

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

    # ---------- store + background sweep ----------

    def snapshot(self) -> dict[str, Any]:
        return self._store.snapshot()

    def refresh_host(self, key: str) -> Optional[dict[str, Any]]:
        h = self.by_key.get(key)
        if not h or not h.tmux_users:
            return None
        result = self.list_host(h)
        self._store.update(key, result)
        return result

    def force_sweep(self) -> None:
        tmux_hosts = [h for h in self.hosts if h.tmux_users]
        workers = max(1, len(tmux_hosts))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            self._sweep_once(ex)

    def _sweep_once(self, executor: ThreadPoolExecutor) -> None:
        tmux_hosts = [h for h in self.hosts if h.tmux_users]
        futures = {executor.submit(self.list_host, h): h for h in tmux_hosts}
        for fut in as_completed(futures):
            h = futures[fut]
            try:
                self._store.update(h.key, fut.result())
            except Exception as exc:  # noqa: BLE001
                log.exception("muxboard sweep failed for %s", h.key)
                self._store.update(h.key, self._list_fail(h, f"{exc.__class__.__name__}: {exc}", time.monotonic()))
        self._store.mark_swept()

    def _loop(self) -> None:
        tmux_hosts = [h for h in self.hosts if h.tmux_users]
        workers = max(1, len(tmux_hosts))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            while True:
                try:
                    self._sweep_once(ex)
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(self.interval)

    def start_background_loop(self) -> None:
        with self._thread_lock:
            if self._thread_started:
                return
            t = threading.Thread(target=self._loop, name="muxboard-sweep", daemon=True)
            t.start()
            self._thread_started = True


def _current_username() -> str:
    import getpass
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001
        return os.environ.get("USER", "root")


class _TmuxStore:
    """Thread-safe latest-result cache for the background sweep."""

    def __init__(self, hosts: list[Host]) -> None:
        self._hosts = hosts
        self._lock = threading.Lock()
        self._results: dict[str, dict[str, Any]] = {}
        self._last_sweep: Optional[float] = None

    def update(self, key: str, result: dict[str, Any]) -> None:
        with self._lock:
            self._results[key] = {**result, "checked_at": time.time()}

    def mark_swept(self) -> None:
        with self._lock:
            self._last_sweep = time.time()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            hosts = []
            for h in self._hosts:
                if not h.tmux_users:
                    continue
                hosts.append({
                    "key": h.key,
                    "hostname": h.hostname,
                    "label": h.display,
                    "users": list(h.tmux_users),
                    "result": self._results.get(h.key),
                })
            return {"hosts": hosts, "last_sweep": self._last_sweep}
