"""Authorization for muxboard.

muxboard hands out **authenticated remote-shell access over the web**. A
misconfigured gate is a root shell for a stranger, so the contract is
default-deny: you must pass an ``authorize`` callable, and the only way to let
a request through is to return a :class:`Principal` from it.

An ``authorize`` callable takes the Flask :class:`~flask.Request` and returns
either a :class:`Principal` (allow) or ``None`` (deny). It runs on every
HTTP request and every WebSocket handshake.

Three building blocks ship in the box:

- :func:`deny_all` - the safe default; denies everything. Useful as a
  placeholder while you wire up real auth.
- :func:`token_auth` - a shared-secret gate (header or query param), compared
  in constant time. Adequate behind TLS for a single operator or a small
  trusted team; it is a *bearer* secret, so treat it like a password.
- :func:`allow_all` - opens the board to anyone who can reach it. Only sane
  behind another authenticating layer (an SSO proxy, an mTLS frontend, a
  localhost bind). It logs a loud warning on every construction.

For anything multi-user, write your own ``authorize`` that reads your existing
session/SSO and returns a Principal whose :attr:`Principal.allowed_users`
scopes which tmux users that person may touch.
"""

from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass
from typing import Callable, Optional

from flask import Request

log = logging.getLogger("muxboard.auth")

# An authorize callable: Request -> Principal (allow) or None (deny).
Authorizer = Callable[[Request], Optional["Principal"]]


@dataclass(frozen=True)
class Principal:
    """An authenticated caller.

    Args:
        name: Stable identifier for audit logs and per-user attach accounting
            (an email, a username, ``"token"``). Not shown to other users.
        allowed_users: tmux users this principal may manage, or ``None`` for
            "every tmux user configured on the host." Use a narrow set to give
            a person access only to their own sessions on a shared box.
    """

    name: str
    allowed_users: Optional[frozenset[str]] = None

    def may_use(self, tmux_user: str) -> bool:
        return self.allowed_users is None or tmux_user in self.allowed_users


def deny_all(_request: Request) -> Optional[Principal]:
    """Deny every request. The default when no authorizer is supplied."""
    return None


def allow_all(name: str = "anonymous") -> Authorizer:
    """Allow every request as ``name`` with access to all tmux users.

    Dangerous on its own. Only use this when a layer in front of muxboard has
    already authenticated the caller (SSO proxy, mTLS, a strict localhost
    bind). Logs a warning so this choice never happens silently.
    """
    log.warning(
        "muxboard: allow_all() authorizer is active - every reachable client "
        "gets shell access. Only safe behind another auth layer."
    )

    def _authorize(_request: Request) -> Optional[Principal]:
        return Principal(name=name, allowed_users=None)

    return _authorize


def token_auth(
    secret: str,
    *,
    header: str = "X-Muxboard-Token",
    query_param: str = "token",
    name: str = "token-user",
    allowed_users: Optional[frozenset[str]] = None,
) -> Authorizer:
    """Shared-secret gate.

    The caller must present ``secret`` in the ``header`` request header or the
    ``query_param`` query string. The comparison is constant-time
    (:func:`hmac.compare_digest`).

    The query-param path exists so a plain browser link can open an attach page
    without a custom header, but note that query strings land in proxy and
    server access logs - prefer the header where you control the client, and
    always run behind TLS.

    Args:
        secret: The shared secret. A short or empty secret is rejected.
        header / query_param: Where to look for the secret.
        name: Principal name recorded in audit logs.
        allowed_users: Optional tmux-user scope for the token's principal.
    """
    if not secret or len(secret) < 16:
        raise ValueError(
            "token_auth secret must be at least 16 characters - it is a "
            "bearer credential equivalent to a password"
        )
    secret_b = secret.encode("utf-8")

    def _authorize(request: Request) -> Optional[Principal]:
        presented = request.headers.get(header) or request.args.get(query_param)
        if not presented:
            return None
        if hmac.compare_digest(presented.encode("utf-8"), secret_b):
            return Principal(name=name, allowed_users=allowed_users)
        return None

    return _authorize
