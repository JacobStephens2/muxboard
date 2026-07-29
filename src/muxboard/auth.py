"""Authorization for muxboard.

muxboard hands out **authenticated remote-shell access over the web**. A
misconfigured gate is a root shell for a stranger, so the contract is
default-deny: you must pass an ``authorize`` callable, and the only way to let
a request through is to return a :class:`Principal` from it.

An ``authorize`` callable takes the Flask :class:`~flask.Request` and returns
either a :class:`Principal` (allow) or ``None`` (deny). It runs on every
HTTP request and every WebSocket handshake.

Building blocks that ship in the box:

- :func:`deny_all` - the safe default; denies everything. Useful as a
  placeholder while you wire up real auth.
- :func:`token_auth` - a shared-secret gate (header or query param), compared
  in constant time. Adequate behind TLS for a single operator or a small
  trusted team; it is a *bearer* secret, so treat it like a password.
  Note: browser ``fetch`` and WebSocket handshakes do **not** automatically
  carry a query-string token - prefer a cookie session (your own, or
  :func:`signed_cookie_auth`) for full-browser use.
- :func:`signed_cookie_auth` - trust an existing itsdangerous-signed cookie
  (the pattern used when muxboard is reverse-proxied under an app that
  already logs people in). Same-host path mounts keep the cookie host-scoped;
  no cookie-domain expansion required.
- :func:`header_auth` - trust a request header set by a front proxy after it
  has authenticated the caller (``X-Remote-User``, etc.). Only safe when the
  proxy strips client-supplied values of that header.
- :func:`allow_all` - opens the board to anyone who can reach it. Only sane
  behind another authenticating layer (an SSO proxy, an mTLS frontend, a
  localhost bind). It logs a loud warning on every construction.

For anything multi-user, write your own ``authorize`` that reads your existing
session/SSO and returns a Principal whose :attr:`Principal.allowed_users`
scopes which tmux users that person may touch. If session creation should be
narrower than attach/kill/list, set :attr:`Principal.create_users` too.
"""

from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

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
        create_users: tmux users this principal may create *new* sessions as.
            ``None`` means "same as ``allowed_users``" for backwards
            compatibility. Set an empty frozenset to allow attach/kill/list
            while denying session creation.
    """

    name: str
    allowed_users: Optional[frozenset[str]] = None
    create_users: Optional[frozenset[str]] = None

    def may_use(self, tmux_user: str) -> bool:
        return self.allowed_users is None or tmux_user in self.allowed_users

    def may_create(self, tmux_user: str) -> bool:
        if not self.may_use(tmux_user):
            return False
        if self.create_users is None:
            return True
        return tmux_user in self.create_users


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
    create_users: Optional[frozenset[str]] = None,
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
        create_users: Optional narrower scope for creating new sessions.
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
            return Principal(
                name=name,
                allowed_users=allowed_users,
                create_users=create_users,
            )
        return None

    return _authorize


def signed_cookie_auth(
    secret: str,
    *,
    cookie: str = "session",
    salt: str = "muxboard-session",
    max_age: int = 60 * 60 * 24 * 30,
    name: str = "cookie-user",
    name_claim: Optional[str] = None,
    allowed_users: Optional[frozenset[str]] = None,
    create_users: Optional[frozenset[str]] = None,
) -> Authorizer:
    """Trust an itsdangerous-signed cookie issued by another app on this host.

    This is the building block for "muxboard lives under
    ``https://ops.example.com/console/`` and reuses the ops app's login." The
    front app issues a signed cookie; muxboard validates the same
    ``secret`` + ``salt`` + ``max_age``. Mount muxboard on a same-host path so
    the cookie stays host-scoped (do not widen ``Domain=`` unless you mean to).

    Compatible with :class:`itsdangerous.URLSafeTimedSerializer` payloads.
    If ``name_claim`` is set and the payload is a mapping, that key is used as
    :attr:`Principal.name`; otherwise ``name`` is used.

    Requires the ``itsdangerous`` package (already pulled in by Flask).
    """
    if not secret or len(secret) < 16:
        raise ValueError(
            "signed_cookie_auth secret must be at least 16 characters - it is "
            "the signing key of an existing session cookie"
        )
    try:
        from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
    except ImportError as exc:  # pragma: no cover - Flask always depends on it
        raise RuntimeError(
            "signed_cookie_auth requires itsdangerous (install Flask)"
        ) from exc

    signer = URLSafeTimedSerializer(secret, salt=salt)

    def _authorize(request: Request) -> Optional[Principal]:
        token = request.cookies.get(cookie)
        if not token:
            return None
        try:
            payload: Any = signer.loads(token, max_age=max_age)
        except (BadSignature, SignatureExpired):
            return None
        principal_name = name
        if name_claim and isinstance(payload, dict):
            claim = payload.get(name_claim)
            if claim:
                principal_name = str(claim)
        return Principal(
            name=principal_name,
            allowed_users=allowed_users,
            create_users=create_users,
        )

    return _authorize


def header_auth(
    header: str = "X-Remote-User",
    *,
    allowed_users: Optional[frozenset[str]] = None,
    create_users: Optional[frozenset[str]] = None,
) -> Authorizer:
    """Trust a header set by a reverse proxy after it authenticated the caller.

    Only safe when the proxy **strips** any client-supplied value of ``header``
    before setting its own. If a client can reach muxboard directly and forge
    the header, they get shell access. Bind muxboard to localhost and put the
    proxy in front.

    The header value becomes :attr:`Principal.name`.
    """
    header_key = header

    def _authorize(request: Request) -> Optional[Principal]:
        value = (request.headers.get(header_key) or "").strip()
        if not value:
            return None
        return Principal(
            name=value,
            allowed_users=allowed_users,
            create_users=create_users,
        )

    return _authorize
