"""The Sweep: the Board's periodic listing of every Host's Sessions.

The Dashboard and ``api/sessions`` read the Sweep's latest result and never a
live listing, so a request never blocks on SSH. Refresh runs a Sweep now, for
every Host or for one.

The seam is the *lister*, a callable from a :class:`~muxboard.inventory.Host`
to that Host's list result. The Board passes the tmux controller's
``list_host``; tests pass a fake. A lister that raises never escapes: the
exception becomes that Host's failed result, the same as any other listing
failure.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Optional

from .auth import Principal
from .inventory import Host

log = logging.getLogger("muxboard.sweep")

# A Host's list result: ``{ok, error, sessions:{user:[...]}, errors:{user:msg},
# users, sweep_ms}``. Kept a dict because ``api/sessions`` returns it as JSON
# and the Dashboard template reads its keys.
Lister = Callable[[Host], "dict[str, Any]"]


def failed_result(host: Host, error: str, *, sweep_ms: int = 0) -> dict[str, Any]:
    """The list result for a Host whose listing failed outright."""
    return {
        "ok": False, "error": error, "sessions": {}, "errors": {},
        "users": list(host.tmux_users),
        "sweep_ms": sweep_ms,
    }


class Sweep:
    """Latest list result per Host, the loop that renews it, and Refresh."""

    def __init__(
        self,
        hosts: list[Host],
        lister: Lister,
        interval: int,
        *,
        _clock: Callable[[], float] = time.time,
    ) -> None:
        # Inventory-only Hosts have nothing to list; drop them once here so no
        # view or Refresh has to ask again.
        self._hosts = [h for h in hosts if h.tmux_users]
        self._by_key = {h.key: h for h in self._hosts}
        self._lister = lister
        self._interval = interval
        self._clock = _clock
        self._lock = threading.Lock()
        self._results: dict[str, dict[str, Any]] = {}
        self._last_sweep: Optional[float] = None
        self._thread_started = False
        self._thread_lock = threading.Lock()

    # ---------- interface ----------

    def start(self) -> None:
        """Start the background loop. Calling it again does nothing."""
        with self._thread_lock:
            if self._thread_started:
                return
            t = threading.Thread(target=self._loop, name="muxboard-sweep", daemon=True)
            t.start()
            self._thread_started = True

    def refresh(self, key: Optional[str] = None) -> bool:
        """Refresh every Host now, or only ``key``.

        Returns whether ``key`` named a Host with Tmux users (always true with
        no key). Only refreshing every Host stamps ``last_sweep``.
        """
        if key is None:
            with ThreadPoolExecutor(max_workers=self._workers()) as ex:
                self._sweep_all(ex)
            return True
        host = self._by_key.get(key)
        if host is None:
            return False
        self._record(host, self._list(host))
        return True

    def view(self, principal: Principal) -> dict[str, Any]:
        """The latest results, scoped to the Tmux users ``principal`` may use."""
        allowed = principal.allowed_users
        with self._lock:
            hosts = []
            for h in self._hosts:
                result = self._results.get(h.key)
                users = list(h.tmux_users)
                if allowed is not None:
                    users = [u for u in users if u in allowed]
                    if result:
                        result = _scope_result(result, allowed)
                hosts.append({
                    "key": h.key,
                    "hostname": h.hostname,
                    "label": h.display,
                    "users": users,
                    "result": result,
                })
            return {"hosts": hosts, "last_sweep": self._last_sweep}

    # ---------- internals ----------

    def _workers(self) -> int:
        return max(1, len(self._hosts))

    def _list(self, host: Host) -> dict[str, Any]:
        try:
            return self._lister(host)
        except Exception as exc:  # noqa: BLE001
            log.exception("muxboard sweep failed for %s", host.key)
            return failed_result(host, f"{exc.__class__.__name__}: {exc}")

    def _record(self, host: Host, result: dict[str, Any]) -> None:
        with self._lock:
            self._results[host.key] = {**result, "checked_at": self._clock()}

    def _sweep_all(self, executor: ThreadPoolExecutor) -> None:
        futures = {executor.submit(self._list, h): h for h in self._hosts}
        for fut in as_completed(futures):
            self._record(futures[fut], fut.result())
        with self._lock:
            self._last_sweep = self._clock()

    def _loop(self) -> None:
        with ThreadPoolExecutor(max_workers=self._workers()) as ex:
            while True:
                try:
                    self._sweep_all(ex)
                except Exception:  # noqa: BLE001
                    log.exception("muxboard sweep loop iteration failed")
                time.sleep(self._interval)


def _scope_result(result: dict[str, Any], allowed: frozenset[str]) -> dict[str, Any]:
    return {
        **result,
        "sessions": {u: v for u, v in (result.get("sessions") or {}).items() if u in allowed},
        "errors": {u: v for u, v in (result.get("errors") or {}).items() if u in allowed},
        "users": [u for u in (result.get("users") or []) if u in allowed],
    }
