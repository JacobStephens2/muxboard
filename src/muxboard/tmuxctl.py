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

from .inventory import Host, index_by_key

log = logging.getLogger("muxboard.tmuxctl")

# Field separator for `tmux ls -F`. ASCII US (Unit Separator, 0x1F) never
# appears in a legitimate session name - tmux forbids only `.` and `:`, but
# allows `|`, spaces, etc. - so splitting on it is unambiguous.
# Order: name <US> windows <US> created <US> attached <US> activity <US> id
_SEP = "\x1f"
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


class TmuxctlError(Exception):
    """Recoverable failure surfaced to the API caller."""


def valid_new_session_name(name: str) -> bool:
    return bool(_NEW_SESSION_RE.match(name))


def _as_user_prefix(target_user: str, login_user: str) -> str:
    """Shell prefix that elevates from login_user to target_user.

    Returns ``"sudo -n -u <user> "`` (with trailing space), or ``""`` when the
    target is the login user.
    """
    if target_user == login_user:
        return ""
    return f"sudo -n -u {shlex.quote(target_user)} "


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

    # ---------- list ----------

    def _list_script(self, host: Host) -> str:
        """Remote script emitting one line per session:
            <user><US><name><US><windows><US><created><US><attached><US><activity><US><id>
        plus ``__MUXBOARD_ERR__<US><user><US><msg>`` marker lines when a
        user's sudo is refused (so the UI shows "permission denied" instead of
        a deceptively empty list). The literal 0x1F byte survives SSH transport
        unchanged inside the single-quoted format string.
        """
        login = self._login_user(host)
        parts: list[str] = []
        for u in host.tmux_users:
            prefix = _as_user_prefix(u, login)
            per_user_fmt = f"{u}{_SEP}{_FMT}"
            if prefix:
                parts.append(
                    f"if {prefix}true 2>/dev/null; then "
                    f"  {prefix}tmux ls -F '{per_user_fmt}' 2>/dev/null || true; "
                    f"else echo '__MUXBOARD_ERR__{_SEP}{u}{_SEP}sudo refused'; fi"
                )
            else:
                parts.append(f"tmux ls -F '{per_user_fmt}' 2>/dev/null || true")
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
        return {"sessions": sessions_by_user, "errors": errors}

    def list_host(self, host: Host) -> dict[str, Any]:
        """Return ``{ok, error, sessions:{user:[...]}, errors:{user:msg}, users, sweep_ms}``."""
        start = time.monotonic()
        if not host.tmux_users:
            return {
                "ok": True, "error": None, "sessions": {}, "errors": {},
                "users": [], "sweep_ms": 0,
            }
        argv, env_add = self._build_argv(host, self._list_script(host))
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
            "sessions": parsed["sessions"], "errors": parsed["errors"],
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
        prefix = _as_user_prefix(user, self._login_user(host))
        script = f"{prefix}tmux kill-session -t {shlex.quote(name)}"
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
        prefix = _as_user_prefix(user, self._login_user(host))
        script = f"{prefix}tmux new-session -d -s {shlex.quote(name)}"
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
        prefix = _as_user_prefix(user, self._login_user(host))
        remote = f"{prefix}tmux attach -t {shlex.quote(name)}"
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
