# Deploy: muxboard under an existing authenticated site

This is the path that worked in production for a solo-operator ops box:

1. Front app already has login (password, passkey, SSO) and sets a signed cookie.
2. muxboard runs as its own process on localhost.
3. The reverse proxy mounts it at a **same-host path** (`/console/`) so the
   session cookie is sent without widening `Domain=`.
4. muxboard's `authorize` is :func:`muxboard.signed_cookie_auth` with the
   **same signing secret + salt + max_age** as the front app.

## Checklist

| Step | Why |
| --- | --- |
| `python3 -m venv .venv` then `pip install "muxboard[deploy] @ git+..."` | PEP 668; gevent comes with the deploy extra |
| `authorize=signed_cookie_auth(...)` or a custom authorizer | Browser fetch + WebSocket need a cookie, not `?token=` |
| `url_prefix="/console"` matching the public path | Links and `/ws/...` stay under the mount |
| `allowed_origins=["https://ops.example.com"]` | Blocks cross-site WebSocket hijacks |
| Apache/nginx WebSocket upgrade to the backend | Attach is a long-lived WS |
| `gunicorn -k gevent -w 1` | flask-sock needs a non-sync worker; one worker is enough |
| systemd `PrivateTmp=false` when `local=True` | Otherwise `/tmp/tmux-*` sockets are invisible |
| Least-privilege `tmux_users` | A passing principal can run any command as those users |

## Files here

- `apache-console.conf.example` - ProxyPass + WebSocket rules to drop into an existing vhost
- `console.service.example` - systemd unit
- sibling example app: `../behind_existing_login.py`

## Auth alternatives

| Gate | When |
| --- | --- |
| `signed_cookie_auth` | Front app already issues itsdangerous cookies (Flask/FastAPI common case) |
| `header_auth("X-Remote-User")` | SSO proxy sets the header **after stripping** client values; bind muxboard to localhost |
| `allow_all()` | Only if the proxy is the sole way in **and** already enforces auth (loud warning) |
| `token_auth` | Scripts/curl; awkward for full browser UX because fetch/WS do not carry query tokens |

## What not to do

- Do not `pip install` into the system Python on Debian/Ubuntu (PEP 668).
- Do not pass `--break-system-packages` to paper over that.
- Do not bind gunicorn on `0.0.0.0` if the only intended client is the local reverse proxy.
- Do not put a second public hostname on muxboard unless you also expand cookie
  `Domain` (or accept a second login). Prefer a path under the app people already use.
