"""WebSocket <-> PTY bridge for a live terminal attach.

The board's ``/ws/<key>/<user>/<name>`` route hands its WebSocket connection
to :func:`bridge`. We spawn the attach command (``ssh -tt ... tmux attach`` or
a local ``tmux attach`` under :mod:`pty`) and shuttle bytes:

  - Browser -> server:  text JSON ``{"type":"input","data":"..."}`` for
                        keystrokes and ``{"type":"resize","cols":N,"rows":N}``
                        for terminal resizing. ``{"type":"ping"}`` is a no-op
                        keepalive.
  - Server -> browser:  binary frames of raw bytes from the PTY master fd.

A background drain thread reads the PTY; the main thread reads the WebSocket.
Either side disconnecting tears down the SSH/tmux process group so we never
leak file descriptors or zombie processes.

:class:`SlotManager` caps concurrent attaches per principal and globally, so a
single (or compromised) account cannot exhaust FDs/PIDs/RAM by opening
hundreds of shells.
"""

from __future__ import annotations

import errno
import fcntl
import json
import logging
import os
import pty
import select
import signal
import struct
import subprocess
import termios
import threading
import time
from typing import Any

log = logging.getLogger("muxboard.ttyproxy")

# Max bytes per PTY read. Big enough to slurp a screenful, small enough that
# one slow ws send does not block other clients for long.
_READ_CHUNK = 16 * 1024

# WebSocket receive() poll timeout. Keeps the loop responsive to subprocess
# exit without burning CPU.
_WS_RECV_TIMEOUT = 0.25

# Cap on queued output when the WebSocket is slow. Hitting it closes the
# connection rather than ballooning memory.
_MAX_QUEUE_BYTES = 4 * 1024 * 1024  # 4 MiB

# Hard lifetime cap on one attach. Belt-and-braces against a leaked subprocess
# if neither the browser nor SSH ever closes cleanly.
_MAX_ATTACH_SECONDS = 6 * 3600  # 6h


class AttachCapacityExceeded(Exception):
    """Raised by :meth:`SlotManager.acquire` when an attach would exceed a cap."""


class SlotManager:
    """Process-wide concurrent-attach accounting.

    The limits guard real OS resources (fds, pids, RAM) shared by every attach
    in the process, so one manager per process is the right granularity.
    """

    def __init__(self, *, max_per_user: int = 5, max_global: int = 30) -> None:
        self.max_per_user = max_per_user
        self.max_global = max_global
        self._lock = threading.Lock()
        self._by_user: dict[str, int] = {}
        self._total = 0

    def acquire(self, name: str) -> None:
        key = (name or "").strip().lower()
        with self._lock:
            per_user = self._by_user.get(key, 0)
            if per_user >= self.max_per_user:
                raise AttachCapacityExceeded(
                    f"you already have {per_user} attaches open "
                    f"(max {self.max_per_user} per user) - close one and retry"
                )
            if self._total >= self.max_global:
                raise AttachCapacityExceeded(
                    f"the host is at its global cap of {self.max_global} "
                    "concurrent attaches - try again shortly"
                )
            self._by_user[key] = per_user + 1
            self._total += 1

    def release(self, name: str) -> None:
        key = (name or "").strip().lower()
        with self._lock:
            per_user = self._by_user.get(key, 0)
            if per_user <= 1:
                self._by_user.pop(key, None)
            else:
                self._by_user[key] = per_user - 1
            self._total = max(0, self._total - 1)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "total": self._total,
                "max_global": self.max_global,
                "max_per_user": self.max_per_user,
                "by_user": dict(self._by_user),
            }


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    """TIOCSWINSZ on the PTY master so SIGWINCH reaches the child.

    Values are bounded so a malicious/buggy client cannot request absurd
    window sizes that overflow the struct or wedge ssh/tmux.
    """
    rows = max(1, min(rows, 500))
    cols = max(1, min(cols, 1000))
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    except OSError as exc:
        log.debug("TIOCSWINSZ failed: %s", exc)


def _spawn_pty(
    argv: list[str],
    env_add: dict[str, str],
    initial_rows: int,
    initial_cols: int,
) -> tuple[subprocess.Popen, int]:
    master_fd, slave_fd = pty.openpty()
    try:
        _set_winsize(master_fd, initial_rows, initial_cols)
    except Exception:  # noqa: BLE001
        pass
    proc_env = {**os.environ, **env_add, "TERM": "xterm-256color"}
    try:
        proc = subprocess.Popen(  # noqa: S603 - argv built by tmuxctl, never shell=True
            argv,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            env=proc_env,
            close_fds=True,
            preexec_fn=os.setsid,
        )
    finally:
        # The child holds its own copy via stdin/out/err; closing ours means
        # master_fd hits EIO/EOF once the child exits.
        os.close(slave_fd)
    return proc, master_fd


def _drain_pty_thread(master_fd: int, ws: Any, stop_event: threading.Event) -> None:
    """Background: read PTY master, send to WebSocket as binary frames."""
    queued = 0
    while not stop_event.is_set():
        try:
            ready, _, _ = select.select([master_fd], [], [], 0.5)
        except (OSError, ValueError):
            break
        if not ready:
            continue
        try:
            data = os.read(master_fd, _READ_CHUNK)
        except OSError as exc:
            if exc.errno in (errno.EIO, errno.EBADF):
                break
            log.debug("pty read error: %s", exc)
            continue
        if not data:
            break
        queued += len(data)
        if queued > _MAX_QUEUE_BYTES:
            log.warning("muxboard: queue cap %d exceeded; closing", _MAX_QUEUE_BYTES)
            break
        try:
            ws.send(data)  # bytes -> binary frame on flask-sock / simple-websocket
        except Exception as exc:  # noqa: BLE001
            log.debug("ws send failed: %s", exc)
            break
        queued = 0
    stop_event.set()


def bridge(ws: Any, argv: list[str], env_add: dict[str, str]) -> None:
    """Drive the bridge until either end disconnects.

    Called synchronously from the flask-sock route handler thread.
    """
    # The client sends a resize as soon as xterm.js measures its container, so
    # these defaults are short-lived.
    proc, master_fd = _spawn_pty(argv, env_add, initial_rows=24, initial_cols=120)
    stop = threading.Event()
    started = time.monotonic()
    drainer = threading.Thread(
        target=_drain_pty_thread,
        args=(master_fd, ws, stop),
        name="muxboard-pty-drain",
        daemon=True,
    )
    drainer.start()
    log.info("muxboard: spawned argv=%s pid=%s", argv[0], proc.pid)
    try:
        while not stop.is_set():
            if proc.poll() is not None:
                break
            if time.monotonic() - started > _MAX_ATTACH_SECONDS:
                log.info("muxboard: max attach lifetime reached")
                break
            try:
                msg = ws.receive(timeout=_WS_RECV_TIMEOUT)
            except Exception as exc:  # noqa: BLE001
                log.debug("ws receive failed: %s", exc)
                break
            if msg is None:
                continue  # timeout - re-check process/stop
            if isinstance(msg, (bytes, bytearray)):
                try:
                    os.write(master_fd, bytes(msg))
                except OSError:
                    break
                continue
            try:
                obj = json.loads(msg)
            except (ValueError, TypeError):
                try:
                    os.write(master_fd, msg.encode("utf-8", errors="replace"))
                except OSError:
                    break
                continue
            t = obj.get("type")
            if t == "input":
                data = obj.get("data") or ""
                if data:
                    try:
                        os.write(master_fd, data.encode("utf-8", errors="replace"))
                    except OSError:
                        break
            elif t == "resize":
                try:
                    rows = int(obj.get("rows") or 24)
                    cols = int(obj.get("cols") or 120)
                except (TypeError, ValueError):
                    rows, cols = 24, 120
                _set_winsize(master_fd, rows, cols)
            elif t == "ping":
                pass
            else:
                log.debug("muxboard: ignoring unknown msg type=%r", t)
    finally:
        stop.set()
        try:
            os.close(master_fd)
        except OSError:
            pass
        if proc.poll() is None:
            try:
                # SIGTERM the whole process group so ssh + children die, not
                # just the parent. setsid made group id == pid.
                os.killpg(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        try:
            drainer.join(timeout=1)
        except Exception:  # noqa: BLE001
            pass
        log.info("muxboard: bridge closed (exit=%s)", proc.returncode)
