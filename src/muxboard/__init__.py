"""muxboard - a Flask-embeddable web dashboard for managing tmux sessions
across one host or a fleet of hosts, with live in-browser attach.

This package grants authenticated remote-shell access over the web. Read the
threat model in the README before deploying.
"""

from __future__ import annotations

from .auth import (
    Authorizer,
    Principal,
    allow_all,
    deny_all,
    header_auth,
    signed_cookie_auth,
    token_auth,
)
from .board import Muxboard
from .inventory import Host
from .tmuxctl import TmuxctlError
from .ttyproxy import AttachCapacityExceeded

__version__ = "0.1.4"

__all__ = [
    "Muxboard",
    "Host",
    "Principal",
    "Authorizer",
    "token_auth",
    "signed_cookie_auth",
    "header_auth",
    "deny_all",
    "allow_all",
    "AttachCapacityExceeded",
    "TmuxctlError",
    "__version__",
]
