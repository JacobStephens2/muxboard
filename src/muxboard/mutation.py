"""The Mutation runner: one Kill or create, from raw fields to audit.

The board's Kill and create routes check access, then hand the resolved Host,
the Tmux user, the raw request fields and the Principal's name to
:class:`MutationRunner`. The runner strips and validates the fields, runs the
tmux controller, refreshes that Host's Listing, and emits the audit event. It
returns a :class:`MutationResult` rather than raising, so the routes only turn
it into JSON.

Only a success refreshes and audits. The audit comes after the tmux change has
happened, so a raising audit hook is logged and swallowed and the result is
still success: reporting failure would have the operator retry a Kill of a
Session that is already gone.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Optional

from .inventory import Host
from .ttyproxy import AuditHook

if TYPE_CHECKING:
    from .tmuxctl import TmuxController

log = logging.getLogger("muxboard.mutation")

# Refresh one Host's Listing by key.
Refresh = Callable[[str], Any]


@dataclass(frozen=True)
class MutationResult:
    """A success, or a refusal with an error message.

    A successful create carries the created Session name.
    """

    ok: bool
    error: Optional[str] = None
    name: Optional[str] = None

    @classmethod
    def success(cls, name: Optional[str] = None) -> MutationResult:
        return cls(ok=True, name=name)

    @classmethod
    def refused(cls, error: str) -> MutationResult:
        return cls(ok=False, error=error)


class MutationRunner:
    """Runs one Kill or create on a Host the caller has already authorized."""

    def __init__(
        self,
        controller: TmuxController,
        *,
        refresh: Refresh,
        audit: AuditHook,
    ) -> None:
        self._controller = controller
        self._refresh = refresh
        self._audit = audit

    def kill(
        self,
        host: Host,
        user: str,
        *,
        name: Optional[str],
        confirm: Optional[str],
        by: str,
    ) -> MutationResult:
        """Kill Session ``name``; ``confirm`` must echo the name."""
        name = (name or "").strip()
        if not name:
            return MutationResult.refused("missing name")
        # Defense-in-depth: the client must echo the session name in
        # `confirm`. Blocks accidental same-site POSTs (a future XSS, a
        # fat-fingered curl, a malicious browser extension).
        confirm = (confirm or "").strip()
        if confirm != name:
            return MutationResult.refused(
                f"confirm must echo the session name ({name!r}); got {confirm!r}"
            )
        try:
            self._controller.kill_session(host, user, name)
        except Exception as exc:  # noqa: BLE001
            return MutationResult.refused(str(exc))
        self._succeeded("muxboard.kill", key=host.key, target_user=user,
                        session_name=name, by=by)
        return MutationResult.success()

    def create(
        self,
        host: Host,
        user: str,
        *,
        name: Optional[str],
        command: Optional[str],
        by: str,
    ) -> MutationResult:
        """Create Session ``name``, running ``command`` if it is not blank."""
        name = (name or "").strip()
        command = (command or "").strip() or None
        if not name:
            return MutationResult.refused("missing name")
        try:
            self._controller.create_session(host, user, name, command)
        except Exception as exc:  # noqa: BLE001
            return MutationResult.refused(str(exc))
        self._succeeded("muxboard.create", key=host.key, target_user=user,
                        session_name=name, command=command, by=by)
        return MutationResult.success(name)

    def _succeeded(self, event: str, *, key: str, **fields: Any) -> None:
        self._refresh(key)
        try:
            self._audit(event, host=key, **fields)
        except Exception:  # noqa: BLE001
            log.exception("%s audit failed", event)
