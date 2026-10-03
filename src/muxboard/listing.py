"""A Host's Listing: what listing that Host's Sessions produces.

The tmux controller builds Listings and the Sweep keeps the latest one per
Host, so the shape lives here, between them, and depends on the Host inventory
alone. A Listing is built through :meth:`Listing.worked` or
:meth:`Listing.failed`, narrowed with :meth:`Listing.scoped`, stamped by the
Sweep with :meth:`Listing.stamped`, and handed out as :meth:`Listing.as_dict`,
the JSON ``api/sessions`` returns and the Dashboard template reads.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Optional

from .inventory import Host


@dataclass(frozen=True)
class ListedSession:
    """One Session as a listing saw it."""

    user: str
    name: str
    windows: int
    created: int
    attached: bool
    activity: int
    id: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "user": self.user,
            "name": self.name,
            "windows": self.windows,
            "created": self.created,
            "attached": self.attached,
            "activity": self.activity,
            "id": self.id,
        }


@dataclass(frozen=True)
class Listing:
    """Whether listing one Host worked, its Sessions and errors per Tmux user,
    its Tmux users, how long it took, and when the Sweep recorded it.

    Build one with :meth:`worked` or :meth:`failed`. ``checked_at`` stays
    ``None`` until the Sweep records the Listing with :meth:`stamped`.
    """

    ok: bool
    error: Optional[str]
    sessions: Mapping[str, tuple[ListedSession, ...]]
    errors: Mapping[str, str]
    users: tuple[str, ...]
    sweep_ms: int
    checked_at: Optional[float] = None

    @classmethod
    def worked(
        cls,
        host: Host,
        *,
        sessions: Mapping[str, Iterable[ListedSession]],
        errors: Mapping[str, str],
        sweep_ms: int,
    ) -> Listing:
        """A listing that reached the Host; per-user failures go in ``errors``."""
        return cls(
            ok=True,
            error=None,
            sessions=_frozen({u: tuple(ss) for u, ss in sessions.items()}),
            errors=_frozen(dict(errors)),
            users=tuple(host.tmux_users),
            sweep_ms=sweep_ms,
        )

    @classmethod
    def failed(cls, host: Host, error: str, *, sweep_ms: int = 0) -> Listing:
        """A listing that failed outright."""
        return cls(
            ok=False,
            error=error,
            sessions=_frozen({}),
            errors=_frozen({}),
            users=tuple(host.tmux_users),
            sweep_ms=sweep_ms,
        )

    def stamped(self, checked_at: float) -> Listing:
        """This Listing as the Sweep recorded it at ``checked_at``."""
        return replace(self, checked_at=checked_at)

    def scoped(self, allowed: Optional[frozenset[str]]) -> Listing:
        """This Listing narrowed to the ``allowed`` Tmux users; ``None`` allows all."""
        if allowed is None:
            return self
        return replace(
            self,
            sessions=_frozen({u: v for u, v in self.sessions.items() if u in allowed}),
            errors=_frozen({u: v for u, v in self.errors.items() if u in allowed}),
            users=tuple(u for u in self.users if u in allowed),
        )

    def as_dict(self) -> dict[str, Any]:
        """The JSON-ready form, built fresh on every call so a caller may edit it."""
        return {
            "ok": self.ok,
            "error": self.error,
            "sessions": {
                u: [s.as_dict() for s in ss] for u, ss in self.sessions.items()
            },
            "errors": dict(self.errors),
            "users": list(self.users),
            "sweep_ms": self.sweep_ms,
            "checked_at": self.checked_at,
        }


def _frozen(d: dict[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(d)
