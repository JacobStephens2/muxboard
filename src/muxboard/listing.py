"""A Host's Listing: what listing that Host's Sessions produces.

The tmux controller builds Listings and the Sweep keeps the latest one per
Host, so the shape lives here, between them, and depends on the Host inventory
alone. A Listing is built through :meth:`Listing.worked` or
:meth:`Listing.failed`, narrowed with :meth:`Listing.scoped`, stamped by the
Sweep with :meth:`Listing.stamped`, and handed out as :meth:`Listing.as_dict`,
the JSON ``api/sessions`` returns and the Dashboard template reads.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, replace
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
        # Field order is the wire order of a Session in ``api/sessions``.
        return asdict(self)


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
    elapsed_ms: int
    checked_at: Optional[float] = None

    # Equal by value, but not hashable: ``sessions`` and ``errors`` are
    # read-only mappings, which cannot be hashed.
    __hash__ = None

    @classmethod
    def worked(
        cls,
        host: Host,
        *,
        sessions: Mapping[str, Iterable[ListedSession]],
        errors: Mapping[str, str],
        elapsed_ms: int,
    ) -> Listing:
        """A listing that reached the Host; per-user failures go in ``errors``.

        Each Tmux user's Sessions are put in the Host's ``session_order``,
        then naturally, whatever order they arrive in.
        """
        order = tuple(host.session_order)
        return cls(
            ok=True,
            error=None,
            sessions=MappingProxyType({
                u: tuple(sorted(ss, key=lambda s: _order_key(s.name, order)))
                for u, ss in sessions.items()
            }),
            errors=MappingProxyType(dict(errors)),
            users=tuple(host.tmux_users),
            elapsed_ms=elapsed_ms,
        )

    @classmethod
    def failed(cls, host: Host, error: str, *, elapsed_ms: int = 0) -> Listing:
        """A listing that failed outright."""
        return cls(
            ok=False,
            error=error,
            sessions=MappingProxyType({}),
            errors=MappingProxyType({}),
            users=tuple(host.tmux_users),
            elapsed_ms=elapsed_ms,
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
            sessions=MappingProxyType({u: v for u, v in self.sessions.items() if u in allowed}),
            errors=MappingProxyType({u: v for u, v in self.errors.items() if u in allowed}),
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
            # Named for the Sweep on the wire; it times this one Host's listing.
            "sweep_ms": self.elapsed_ms,
            "checked_at": self.checked_at,
        }


def _natural_key(name: str) -> list[tuple[int, int, str]]:
    """Numeric-aware sort key for Session names.

    Names like ``1``, ``2``, ``22`` are common in operator dashboards; natural
    ordering keeps them in numeric order instead of lexicographic order.
    """
    return [
        (0, int(part), "") if part.isdigit() else (1, 0, part.lower())
        for part in re.split(r"(\d+)", name)
        if part
    ]


def _order_key(
    name: str, session_order: tuple[str, ...]
) -> tuple[int, list[tuple[int, int, str]]]:
    """Sort key honouring a Host's explicit ``session_order``.

    The rank is the index of the first entry in ``session_order`` that equals
    ``name`` or is a prefix of it; a name matching no entry ranks after every
    named one. Ties - including every name on a Host that configures no order -
    fall through to :func:`_natural_key`, so an unconfigured Host sorts exactly
    as it always has.

    First-entry-wins is what makes an overlapping order predictable: with
    ``("build-final", "build")`` the specific entry is reachable, and with the
    two swapped ``build-final`` is absorbed by the prefix. Documented rather
    than resolved by longest-match, because the operator wrote the order down
    and reading it top to bottom should be the whole rule.
    """
    if not session_order:
        return (0, _natural_key(name))
    rank = len(session_order)
    for i, entry in enumerate(session_order):
        if name.startswith(entry):
            rank = i
            break
    return (rank, _natural_key(name))
