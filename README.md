# muxboard

muxboard is a Flask blueprint that puts a web dashboard over `tmux ls` / `new-session` / `kill-session` / `attach` for one host or a fleet of hosts, with a live in-browser terminal backed by xterm.js. The single-host case is the trivial `n = 1` instance of the same inventory model the fleet case uses - there is no separate code path for "just my laptop."

It exists because the alternative - SSH into each box, remember which `tmux` socket belongs to which service account, `attach` by hand - does not scale past about two machines, and because a running agent or build session is far easier to babysit from a browser tab than from a fan-out of terminals.

**Read the threat model below before you deploy this.** muxboard hands out authenticated remote-shell access over the web. A misconfigured gate is a root shell for a stranger. The defaults are built to fail closed, but the security of your deployment is a property of *your* configuration, not of this README.

## What you get

- A dashboard at `/<prefix>/` listing every configured host, each host's managed tmux users, and each user's sessions (window count, created time, last activity, attached flag).
- Create a session (optionally with a startup command), kill a session (behind a type-the-name confirm gate), and attach a live terminal in a new tab.
- A background sweep that refreshes the inventory every 60 seconds so the dashboard reads from a cache and never blocks on SSH.
- Numeric-aware session ordering, so operator-style names like `1`, `2`, `3`, `22` render in the order humans expect.
- A post-create spotlight in the dashboard so the new session is scrolled into view and marked after the page reloads.
- Per-principal and global caps on concurrent attaches, so one account cannot exhaust file descriptors, PIDs, or RAM.

## Install

muxboard is a library you embed in a Flask app, so install it into that app's virtual environment - not into the system Python. On Debian/Ubuntu (PEP 668), bare `pip install` against the distro Python fails with `externally-managed-environment`; that is intentional. Do not pass `--break-system-packages`.

```bash
# once per machine on Debian/Ubuntu, if `python3 -m venv` is missing:
#   sudo apt install python3-venv python3-full
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install "muxboard[deploy] @ git+https://github.com/JacobStephens2/muxboard"
# not yet on PyPI; when it is: pip install "muxboard[deploy]"
# bare `muxboard` omits gunicorn/gevent; the [deploy] extra pulls both
```

If you already have a project venv, activate it and run only the `pip install` line.

You also need, on the machine muxboard runs on: an SSH client (for remote hosts), `sshpass` (only if you use password auth), and `tmux` on every managed host.

## Quickstart - single host

The `n = 1` case: manage tmux on the same box the app runs on.

```python
import os
from flask import Flask
from muxboard import Host, Muxboard, token_auth

board = Muxboard(
    hosts=[Host(key="local", hostname="localhost",
                tmux_users=(os.environ["USER"],), local=True)],
    authorize=token_auth(os.environ["MUXBOARD_TOKEN"]),
    allowed_origins=["https://ops.example.com"],
)
app = Flask(__name__)
board.init_app(app, url_prefix="/mux")
board.start()
```

```bash
export MUXBOARD_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
gunicorn -k gevent -w 1 -b 127.0.0.1:8000 app:app
```

`flask-sock` needs a worker that can hold a WebSocket open for the lifetime of an attach. Use a single gevent (or eventlet) worker - the `[deploy]` extra installs `gunicorn` and `gevent>=24.10.1` - not the default sync worker. Put it behind a TLS-terminating reverse proxy. See `examples/single_host.py` and `examples/fleet.py`.

## Deploy behind an existing login (recommended for ops boxes)

If you already have a dashboard with passkey/password/SSO, **do not stand up a second hostname and a second password.** Mount muxboard on a same-host path and reuse the session cookie:

```python
from muxboard import Host, Muxboard, signed_cookie_auth

board = Muxboard(
    hosts=[Host(key="local", hostname="localhost",
                tmux_users=("deploy",), local=True)],
    authorize=signed_cookie_auth(
        os.environ["SESSION_SECRET"],   # same secret the front app signs with
        cookie="dash_session",
        salt="dashboard-session",       # same itsdangerous salt
        name="ops",
        allowed_users=frozenset({"deploy"}),
    ),
    allowed_origins=["https://ops.example.com"],
)
board.init_app(app, url_prefix="/console")
```

Then reverse-proxy `/console/` (with WebSocket upgrade) to a localhost gunicorn. Full checklist, Apache snippet, and systemd unit: **`examples/deploy/`**. Runnable app: **`examples/behind_existing_login.py`**.

Why this shape:

| Choice | Reason |
| --- | --- |
| Same-host path (`/console/`) | Session cookie stays host-scoped; no `Domain=.example.com` expansion |
| `signed_cookie_auth` | Browser `fetch` and WebSocket handshakes send cookies; query-string `token_auth` does not |
| `PrivateTmp=false` in systemd when `local=True` | Otherwise the service cannot see `/tmp/tmux-*` sockets from login shells |
| One gevent worker | Attach holds a long-lived WS; multi-worker sync is wrong |

Auth gates at a glance: `token_auth` (scripts), `signed_cookie_auth` (reuse an app login), `header_auth` (SSO proxy that sets `X-Remote-User` after stripping client values), `allow_all` (only behind a gate you fully trust), or a custom `authorize`.

## Quickstart - a fleet

More than one `Host`. Two transports are shown - an SSH key and a password from an environment variable:

```python
hosts = [
    Host(key="web1", hostname="web1.internal", ssh_user="ops",
         password_env="WEB1_SSH_PASS", tmux_users=("ops", "deploy")),
    Host(key="db1", hostname="db1.internal", ssh_user="ops",
         ssh_key="/home/muxboard/.ssh/id_ed25519", tmux_users=("ops",)),
]
```

## How user scoping works

muxboard reaches each host as one SSH login user (`ssh_user`, or the local process user when `local=True`). For that user's own tmux socket, it runs `tmux` directly. To read *another* user's socket on the same host - say `deploy` when you logged in as `ops` - it runs:

```
sudo -n -u deploy tmux ls ...
```

So every tmux user other than the login user needs a NOPASSWD sudo rule granting the login user the ability to run commands as them. A minimal `/etc/sudoers.d/muxboard` on a managed host might be:

```
ops ALL=(deploy) NOPASSWD: /usr/bin/tmux
```

If sudo is refused for a user, muxboard shows that user's row with a "sudo refused" badge rather than a deceptively empty session list. That distinction is deliberate: empty and forbidden are not the same fact.

---

# Threat model

This is the section that matters. muxboard's whole job is to turn an HTTP request into a process running on a host. Treat it with the seriousness you would treat `sshd`.

## What an attacker gets if they get in

A principal who passes your `authorize` gate can run arbitrary commands as any of the `tmux_users` you configured for a host - by creating a session with a startup command, or by attaching to a session and typing. There is no "read-only" mode. If `tmux_users` includes `root` or a sudo-capable account, a passing principal has root. **Configure `tmux_users` as the least-privileged set that does the job.**

## The auth contract: default-deny

`authorize` is required in spirit and defaulted to `deny_all` in fact. An `authorize` callable receives the Flask request and returns either a `Principal` (allow) or `None` (deny). It runs on every HTTP route and on the WebSocket handshake - there is no route that skips it. Three gates ship in the box:

| Gate | When it is appropriate | What it is not |
| :-- | :-- | :-- |
| `deny_all` | The default. A board you have not finished configuring is inert. | Not a real gate. |
| `token_auth(secret)` | Scripts/curl, or a single operator behind TLS. Constant-time compared; the secret is a bearer credential. Query-string tokens land in access logs and are not sent by browser `fetch`/WebSocket. | Not a full browser session; not per-user. |
| `signed_cookie_auth(secret, ...)` | Reuse a login your ops app already issues (itsdangerous cookie). Preferred for "muxboard under `/console/` on the same host." | Not a login UI - the front app still signs people in. |
| `header_auth("X-Remote-User")` | SSO proxy sets the header after stripping client-supplied values; muxboard bound to localhost. | Not safe if clients can reach muxboard and forge the header. |
| `allow_all()` | Only when a layer *in front* of muxboard already authenticated the caller - an SSO reverse proxy, mTLS, or a strict `127.0.0.1` bind. It logs a warning on every construction. | Never safe facing the open internet. |

For anything multi-user, write your own `authorize` that reads your existing session or SSO and returns a `Principal` whose `allowed_users` scopes which tmux users that person may touch. `examples/fleet.py` shows the pattern. A scoped principal's dashboard, JSON API, attach page, and WebSocket are all filtered to their `allowed_users`; an out-of-scope user returns 403, not 404, because hiding the existence of the user buys nothing once you are authenticated.

Session creation can be narrower than listing, attaching, and killing. Set `Principal(create_users=frozenset({...}))` when a person may inspect shared service-account sessions but should spawn new work only as their own Unix account. If `create_users` is `None`, creation follows `allowed_users` for backwards compatibility; if it is an empty set, that principal cannot create sessions at all.

## The attack surface, and what is already mitigated

- **Command injection.** Every session name and startup command coming from a client is passed through `shlex.quote`, and no command on the muxboard side ever runs through a shell (`shell=True` is never used). Session-name *creation* is further restricted to `[A-Za-z0-9_-]{1,64}`. Attach and kill operate on existing names, which tmux itself constrains.
- **Cross-site WebSocket hijacking.** Set `allowed_origins` and the WebSocket handshake rejects any browser `Origin` not on the list. If you leave it unset the check is disabled and muxboard logs a warning - do not ship to production that way. Non-browser clients (which omit `Origin`) are allowed through, which is fine for ops tooling but means the Origin check is a defense for browser victims, not an authentication mechanism.
- **Accidental destructive POSTs.** A kill requires the client to echo the exact session name in a `confirm` field, so a stray same-site POST (a future XSS, a fat-fingered curl, a malicious extension) cannot silently kill a session.
- **Create-as attribution drift.** For shared boxes, prefer a narrow `create_users` scope so new sessions are attributed to the operator's own Unix account, not a shared service account. This does not make existing shared sessions read-only; it only gates creation.
- **Resource exhaustion.** Concurrent attaches are capped per principal (default 5) and globally (default 30). Each attach has a 6-hour hard lifetime and a 4 MiB output-queue ceiling, after which the bridge tears down the whole SSH/tmux process group - no leaked fds, no zombies.
- **The authorize callable itself throwing.** If your `authorize` raises, muxboard logs it and denies. Failure is closed.

## What muxboard does *not* do, and you must

- **TLS.** muxboard speaks plain WSGI/WebSocket. Terminate TLS in front of it. Over plain HTTP, a `token_auth` secret and every keystroke are on the wire in cleartext.
- **Rate limiting / brute-force protection on the token.** `token_auth` is a constant-time compare, but it does not lock out after N failures. Put a rate limiter in your proxy if the board is internet-facing.
- **Audit storage.** muxboard emits `kill`, `create`, `attach.start`, and `attach.end` events to an optional `audit` callback with the principal name attached. It does not persist them - wire the callback to your logging.
- **Securing the SSH keys and passwords.** A `password_env` secret lives in the process environment; an `ssh_key` lives on disk. Both are as exposed as the muxboard process. Run it as a dedicated, unprivileged service user.

## The supply-chain question: xterm.js

The attach page loads xterm.js and its fit addon. By default it pulls pinned versions (`@xterm/xterm@5.5.0`, `@xterm/addon-fit@0.10.0`) from jsDelivr. That is a third-party script running on a page that grants shell access - a real supply-chain surface. Two ways to close it, in increasing order of paranoia:

1. Add Subresource Integrity. Pass your own `xterm_js_url` / `xterm_css_url` / `xterm_fit_url` pointing at URLs you have pinned with SRI hashes in your own template, or front the CDN with a CSP that pins hashes.
2. Self-host. Copy the three assets into your own static directory and point the `*_url` kwargs at them. Then no external origin is in the trust path at all.

I have not shipped SRI hashes baked into the template because a wrong hash silently breaks the terminal and a right-but-stale hash rots on the next xterm release; pushing that decision to the deployer who controls their own CSP seemed more honest than pretending the default is hardened. This is the part of the threat model I am least settled on - if you have a cleaner default, open an issue.

## A deployment gotcha: systemd PrivateTmp

If you run muxboard under systemd with `PrivateTmp=true` and use a `local=True` host, the service gets its own `/tmp` namespace - and the tmux sockets created by ordinary login shells in the real `/tmp` are invisible to it, so the dashboard shows no sessions even though `tmux ls` in a normal shell lists them. Two fixes: set `PrivateTmp=false` for the unit, or configure the "local" host as a *remote* host pointed at `localhost` over an SSH key, which forks a fresh login session in the real `/tmp` namespace. The original system muxboard was extracted from used the SSH-to-localhost trick for exactly this reason.

## Open questions

- Is a bearer token plus TLS the right default gate, or should the shipped default refuse to start without an explicit `authorize`? Right now `deny_all` makes an unconfigured board inert, which is safe but silently useless.
- Should the Origin check fail closed (reject `Origin`-less clients) when `allowed_origins` is set, at the cost of breaking curl-based ops scripts? The current choice favors tooling over browser-victim defense-in-depth, and I am not certain that is the right trade for every deployment.

---

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
ruff check src tests
pytest -q
```

The tests cover inventory validation, the auth gates, argv construction and `tmux ls` parsing (no SSH or tmux required), and the blueprint's deny/allow/scope/confirm behavior through a Flask test client.

## License

MIT. See `LICENSE`.
