"""Fleet muxboard: manage tmux across several remote hosts over SSH.

The same model as single_host.py with more than one Host. Two transports are
shown: SSH key (db1) and SSH password from an env var (web1).

To read a *different* user's tmux socket on a host, muxboard runs
`sudo -n -u <user> tmux ...` as its SSH login user - so each such user needs a
NOPASSWD sudo rule for the login user on that host.

This example uses a custom authorizer that maps your existing SSO/session into
a Principal and scopes which tmux users each person may touch.
"""

import os

from flask import Flask, Request, session

from muxboard import Host, Muxboard, Principal

hosts = [
    Host(
        key="web1",
        hostname="web1.internal.example.com",
        label="web-1 (prod)",
        ssh_user="ops",
        password_env="WEB1_SSH_PASS",
        tmux_users=("ops", "deploy"),
    ),
    Host(
        key="db1",
        hostname="db1.internal.example.com",
        label="db-1 (primary)",
        ssh_user="ops",
        ssh_key="/home/muxboard/.ssh/id_ed25519",
        tmux_users=("ops",),
    ),
]


def authorize(request: Request) -> Principal | None:
    """Plug in your own auth. Here: a Flask session set by your SSO layer.

    Returns a Principal scoped to the tmux users this person may manage, or
    None to deny. Admins get all users (allowed_users=None). The deploy-team
    example can inspect both ops and deploy sessions, but can only create new
    sessions as deploy so new work does not appear under a shared ops account.
    """
    email = session.get("email")
    if not email:
        return None
    if email in {"sre-lead@example.com"}:
        return Principal(name=email, allowed_users=None)
    if email.endswith("@example.com"):
        return Principal(
            name=email,
            allowed_users=frozenset({"ops", "deploy"}),
            create_users=frozenset({"deploy"}),
        )
    return None


board = Muxboard(
    hosts=hosts,
    authorize=authorize,
    allowed_origins=["https://ops.example.com"],
)

app = Flask(__name__)
app.secret_key = os.environ["FLASK_SECRET_KEY"]
board.init_app(app, url_prefix="/muxboard")
board.start()
