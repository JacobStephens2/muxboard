"""Single-host muxboard: manage tmux on the same machine the app runs on.

The n=1 case. Run:

    export MUXBOARD_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
    pip install muxboard gunicorn
    gunicorn -k gevent -w 1 -b 127.0.0.1:8000 single_host:app

Then open http://127.0.0.1:8000/mux/?token=YOUR_TOKEN

For a real deployment put this behind a TLS-terminating reverse proxy and set
allowed_origins to your public origin. Never expose it over plain HTTP - the
token is a bearer credential.
"""

import os

from flask import Flask

from muxboard import Host, Muxboard, token_auth

board = Muxboard(
    hosts=[
        Host(
            key="local",
            hostname="localhost",
            label="this host",
            # The Unix user whose tmux sessions to manage. If it differs from
            # the user this process runs as, that user needs a NOPASSWD sudo
            # rule from the process user.
            tmux_users=(os.environ.get("USER", "root"),),
            local=True,
        ),
    ],
    authorize=token_auth(os.environ["MUXBOARD_TOKEN"]),
    allowed_origins=[os.environ.get("MUXBOARD_ORIGIN", "http://127.0.0.1:8000")],
)

app = Flask(__name__)
board.init_app(app, url_prefix="/mux")
board.start()
