"""The Sweep: the Board's periodic listing of every Host's Sessions.

The Dashboard and ``api/sessions`` read the Sweep's latest Listings and never a
live listing, so a request never blocks on SSH. Refresh runs a Sweep now, for
every Host or for one.

The seam is the *lister*, a callable from a :class:`~muxboard.inventory.Host`
to that Host's :class:`~muxboard.listing.Listing`. The Board passes the tmux
controller's ``list_host``; tests pass a fake. A lister that raises never
escapes: the exception becomes that Host's failed Listing, the same as any
other listing failure.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Optional

from .auth import Principal
from .inventory import Host
from .listing import Listing

log = logging.getLogger("muxboard.sweep")

Lister = Callable[[Host], Listing]


class Sweep:
    """Latest Listing per Host, the loop that renews it, and Refresh."""

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
        self._listings: dict[str, Listing] = {}
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
        """The latest Listings, scoped to the Tmux users ``principal`` may use.

        Each Host's ``result`` is its Listing's dict form, fresh on every call,
        so a caller holding the view cannot edit the store.
        """
        allowed = principal.allowed_users
        with self._lock:
            hosts = []
            for h in self._hosts:
                listing = self._listings.get(h.key)
                users = list(h.tmux_users)
                if allowed is not None:
                    users = [u for u in users if u in allowed]
                hosts.append({
                    "key": h.key,
                    "hostname": h.hostname,
                    "label": h.display,
                    "users": users,
                    "result": listing.scoped(allowed).as_dict() if listing is not None else None,
                })
            return {"hosts": hosts, "last_sweep": self._last_sweep}

    # ---------- internals ----------

    def _workers(self) -> int:
        return max(1, len(self._hosts))

    def _list(self, host: Host) -> Listing:
        try:
            return self._lister(host)
        except Exception as exc:  # noqa: BLE001
            log.exception("muxboard sweep failed for %s", host.key)
            return Listing.failed(host, f"{exc.__class__.__name__}: {exc}")

    def _record(self, host: Host, listing: Listing) -> None:
        with self._lock:
            self._listings[host.key] = listing.stamped(self._clock())

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

