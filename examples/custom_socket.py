"""Manage tmux servers that live on a non-default socket.

A tool that runs `tmux -S <path>` is invisible to a host entry that does not
name that socket. swarm-forge is the motivating case: it puts every agent role
on a private socket under /tmp/swarmforge-$USER/, so `tmux ls` shows nothing
while six agents are running.

The unit of a Host entry is one tmux *server*, not one machine. The same box
appears twice here - once for the default socket, once for the swarm-forge one
- with different keys, so /local/... and /swarm/... routes cannot collide and
`allowed_users={"jacob"}` scopes both.

Run it the same way as single_host.py:

    export MUXBOARD_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
    export MUXBOARD_TMUX_USER="$USER"      # the Unix user whose tmux to manage
    export SWARM_PROJECT=/srv/proj         # the swarm-forge project directory
    gunicorn -k gevent -w 1 -b 127.0.0.1:8000 custom_socket:app
"""

import os

from flask import Flask

from muxboard import Host, Muxboard, token_auth

# No default here on purpose: silently falling back to "root" would hand a
# passing principal a root shell. Fail loudly instead.
USER = os.environ["MUXBOARD_TMUX_USER"]
PROJECT = os.environ["SWARM_PROJECT"]

board = Muxboard(
    hosts=[
        Host(
            key="local",
            hostname="localhost",
            label="this host",
            tmux_users=(USER,),
            local=True,
        ),
        Host(
            key="swarm",
            hostname="localhost",
            label="this host (swarm-forge)",
            tmux_users=(USER,),
            local=True,
            # swarm-forge names its socket after a CRC32 of the working
            # directory, and publishes the result here. Reading the path out
            # of that file survives `swarm` restarts; hardcoding the hash does
            # not. Use tmux_socket="/tmp/.../abc123.sock" instead when the path
            # is fixed and you know it. The two are mutually exclusive.
            tmux_socket_file=f"{PROJECT}/.swarmforge/tmux-socket",
            # Alphabetical is wrong for a pipeline: the specifier is the entry
            # point and the only path back to master, and it sorts last. Spell
            # the pipeline order out - it is knowledge swarm-forge has and
            # muxboard cannot derive from the names. Sessions matching nothing
            # here follow these, numeric-aware-sorted among themselves.
            session_order=(
                "swarmforge-specifier",
                "swarmforge-coder",
                "swarmforge-cleaner",
                "swarmforge-architect",
                "swarmforge-hardender",
                "swarmforge-QA",
            ),
        ),
    ],
    authorize=token_auth(os.environ["MUXBOARD_TOKEN"]),
    allowed_origins=[os.environ.get("MUXBOARD_ORIGIN", "http://127.0.0.1:8000")],
)

app = Flask(__name__)
board.init_app(app, url_prefix="/mux")
board.start()
