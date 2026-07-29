"""Mount muxboard under an existing login, same host.

Pattern used when you already have an ops dashboard (password, passkey, SSO)
and want muxboard at a path like ``/console/`` without a second password.

1. Your front app issues a signed cookie (itsdangerous URLSafeTimedSerializer
   is what Flask / many FastAPI apps already use).
2. Apache/nginx reverse-proxies ``/console/`` to this process (WebSocket upgrade
   required). Prefer a same-host path so the session cookie stays host-scoped.
3. This process validates that cookie with :func:`muxboard.signed_cookie_auth`.

See ``examples/deploy/`` for Apache + systemd templates.

Run (from a venv)::

    pip install "muxboard[deploy] @ git+https://github.com/JacobStephens2/muxboard"
    export SESSION_SECRET=...          # same secret the front app signs with
    export MUXBOARD_ORIGIN=https://ops.example.com
    gunicorn -k gevent -w 1 -b 127.0.0.1:3492 behind_existing_login:app
"""

from __future__ import annotations

import os

from flask import Flask

from muxboard import Host, Muxboard, signed_cookie_auth

SESSION_SECRET = os.environ["SESSION_SECRET"]
ORIGIN = os.environ.get("MUXBOARD_ORIGIN", "https://ops.example.com").rstrip("/")
PREFIX = os.environ.get("MUXBOARD_URL_PREFIX", "/console").rstrip("/") or "/console"
TMUX_USER = os.environ.get("MUXBOARD_TMUX_USER") or os.environ.get("USER") or "ubuntu"
# Match the front app's cookie name + itsdangerous salt + max_age.
COOKIE = os.environ.get("MUXBOARD_SESSION_COOKIE", "dash_session")
SALT = os.environ.get("MUXBOARD_SESSION_SALT", "dashboard-session")
MAX_AGE = int(os.environ.get("MUXBOARD_SESSION_MAX_AGE", str(60 * 60 * 24 * 30)))

app = Flask(__name__)

board = Muxboard(
    hosts=[
        Host(
            key="local",
            hostname="localhost",
            label="this host",
            tmux_users=(TMUX_USER,),
            local=True,
        ),
    ],
    authorize=signed_cookie_auth(
        SESSION_SECRET,
        cookie=COOKIE,
        salt=SALT,
        max_age=MAX_AGE,
        name=TMUX_USER,
        allowed_users=frozenset({TMUX_USER}),
    ),
    allowed_origins=[ORIGIN],
)
board.init_app(app, url_prefix=PREFIX)
board.start()
