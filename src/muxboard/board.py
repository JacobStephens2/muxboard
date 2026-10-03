"""The :class:`Muxboard` - a Flask-embeddable tmux dashboard.

Typical use::

    from flask import Flask
    from muxboard import Muxboard, Host, token_auth

    board = Muxboard(
        hosts=[Host(key="local", hostname="localhost",
                    tmux_users=("deploy",), local=True)],
        authorize=token_auth(os.environ["MUXBOARD_TOKEN"]),
    )
    app = Flask(__name__)
    board.init_app(app, url_prefix="/mux")
    board.start()

The board registers a blueprint (HTML dashboard + JSON API + static assets)
and, because tmux attach needs a live socket, a set of WebSocket routes on the
same app via flask-sock.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from flask import (
    Blueprint,
    Flask,
    Request,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from .auth import Authorizer, Principal, deny_all
from .inventory import Host
from .mutation import MutationRunner
from .sweep import Sweep
from .tmuxctl import TmuxController
from .ttyproxy import AttachRunner, AuditHook

log = logging.getLogger("muxboard")

# Pinned xterm.js assets. Self-host these (copy into your own static dir and
# override the *_url kwargs) if you do not want a third-party CDN in the
# supply chain of a page that grants shell access. See the README.
_XTERM_JS = "https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/lib/xterm.min.js"
_XTERM_CSS = "https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/css/xterm.min.css"
_XTERM_FIT = "https://cdn.jsdelivr.net/npm/@xterm/addon-fit@0.10.0/lib/addon-fit.min.js"
_XTERM_WEBLINKS = (
    "https://cdn.jsdelivr.net/npm/@xterm/addon-web-links@0.11.0/lib/addon-web-links.min.js"
)
_XTERM_CLIPBOARD = (
    "https://cdn.jsdelivr.net/npm/@xterm/addon-clipboard@0.1.0/lib/addon-clipboard.min.js"
)

OSC52_OFF = "off"
OSC52_WRITE = "write"
OSC52_READ_WRITE = "read-write"
OSC52_MODES = frozenset({OSC52_OFF, OSC52_WRITE, OSC52_READ_WRITE})
# Clipboard push cap: a login URL fits; a dumped secret file does not.
OSC52_PUSH_MAX_BYTES = 64 * 1024


def _noop_audit(_event: str, **_fields: Any) -> None:
    pass


class Muxboard:
    def __init__(
        self,
        hosts: list[Host],
        authorize: Authorizer = deny_all,
        *,
        ssh_key: Optional[str] = None,
        ssh_timeout: int = 8,
        sweep_interval: int = 60,
        strict_host_key_checking: bool = True,
        attach_max_per_user: int = 5,
        attach_max_global: int = 30,
        allowed_origins: Optional[list[str]] = None,
        audit: Optional[AuditHook] = None,
        home_url: Optional[str] = None,
        home_label: str = "Dashboard",
        osc52: str = OSC52_OFF,
        xterm_js_url: str = _XTERM_JS,
        xterm_css_url: str = _XTERM_CSS,
        xterm_fit_url: str = _XTERM_FIT,
        xterm_weblinks_url: str = _XTERM_WEBLINKS,
        xterm_clipboard_url: str = _XTERM_CLIPBOARD,
    ) -> None:
        """Construct a board.

        Args:
            hosts: The inventory. One host is the n=1 case; a fleet is the rest.
            authorize: Default-deny gate. See :mod:`muxboard.auth`. Defaults to
                :func:`~muxboard.auth.deny_all` so an unconfigured board is
                inert rather than open.
            ssh_key: Board-level default private key for key-based hosts.
            allowed_origins: Exact origins permitted on the WebSocket handshake
                (e.g. ``["https://ops.example.com"]``). ``None`` disables the
                Origin check and logs a warning - set this in production to
                block cross-site WebSocket hijacking.
            audit: Optional ``(event, **fields)`` callback for kill/create/
                attach events. Defaults to a no-op.
            home_url: Optional absolute or root-relative URL for a "back to
                parent app" link in the top bar (e.g. ``"/"`` when mounted
                under ``/console/`` on an ops dashboard). ``None`` hides it.
            home_label: Link text for ``home_url`` (default ``"Dashboard"``).
            osc52: Session clipboard via OSC 52. ``"off"`` (default) ignores
                it. ``"write"`` is clipboard push only. ``"read-write"`` is
                push plus clipboard query (query always prompts).
        """
        mode = (osc52 or "").strip()
        if mode not in OSC52_MODES:
            allowed = ", ".join(sorted(OSC52_MODES))
            raise ValueError(f"osc52 must be one of {allowed}; got {osc52!r}")
        self.controller = TmuxController(
            hosts,
            ssh_key=ssh_key,
            ssh_timeout=ssh_timeout,
            strict_host_key_checking=strict_host_key_checking,
        )
        self.sweep = Sweep(hosts, self.controller.list_host, sweep_interval)
        self.authorize = authorize
        self.allowed_origins = (
            {o.rstrip("/") for o in allowed_origins} if allowed_origins else None
        )
        self.audit = audit or _noop_audit
        self.attach_runner = AttachRunner(
            max_per_user=attach_max_per_user,
            max_global=attach_max_global,
            audit=self.audit,
        )
        # The refresh goes through self.sweep at call time, so a Sweep swapped
        # in after construction (as the tests do) is the one refreshed.
        self.mutation_runner = MutationRunner(
            self.controller,
            refresh=lambda key: self.sweep.refresh(key),
            audit=self.audit,
        )
        self.home_url = home_url
        self.home_label = home_label
        self.osc52 = mode
        self.xterm = {
            "js": xterm_js_url,
            "css": xterm_css_url,
            "fit": xterm_fit_url,
            "weblinks": xterm_weblinks_url,
            "clipboard": xterm_clipboard_url,
        }
        self._url_prefix = "/muxboard"
        if self.allowed_origins is None:
            log.warning(
                "muxboard: allowed_origins is unset - the WebSocket Origin "
                "check is disabled. Set allowed_origins in production."
            )

    # ---------- principal resolution ----------

    def _principal(self, req: Request) -> Optional[Principal]:
        try:
            return self.authorize(req)
        except Exception:  # noqa: BLE001
            log.exception("muxboard: authorize callable raised; denying")
            return None

    # ---------- lifecycle ----------

    def start(self) -> None:
        """Start the background sweep thread. Call once after init_app."""
        self.sweep.start()

    def init_app(self, app: Flask, url_prefix: str = "/muxboard") -> None:
        self._url_prefix = url_prefix.rstrip("/") or ""
        bp = Blueprint(
            "muxboard", __name__,
            template_folder="templates",
            static_folder="static",
            static_url_path=f"{self._url_prefix}/static",
        )
        self._register_http(bp)
        app.register_blueprint(bp, url_prefix=self._url_prefix)
        self._register_ws(app)

    # ---------- origin check ----------

    def _origin_ok(self, req: Request) -> bool:
        origin = (req.headers.get("Origin") or "").strip()
        if not origin:
            # Non-browser clients (curl, websocket-client) omit Origin; allow.
            return True
        if self.allowed_origins is None:
            return True
        return origin.rstrip("/") in self.allowed_origins

    # ---------- HTTP routes ----------

    def _register_http(self, bp: Blueprint) -> None:
        @bp.route("/")
        def dashboard():
            principal = self._principal(request)
            if principal is None:
                abort(401)
            return render_template(
                "muxboard/dashboard.html",
                snapshot=self.sweep.view(principal),
                base=self._url_prefix,
                principal=principal,
                home_url=self.home_url,
                home_label=self.home_label,
            )

        @bp.route("/api/sessions")
        def api_sessions():
            principal = self._principal(request)
            if principal is None:
                abort(401)
            return jsonify(self.sweep.view(principal))

        @bp.route("/api/refresh", methods=["POST"])
        def api_refresh():
            principal = self._principal(request)
            if principal is None:
                abort(401)
            key = (request.args.get("key") or "").strip()
            if key:
                if not self.sweep.refresh(key):
                    abort(404)
                return jsonify({"ok": True, "key": key})
            self.sweep.refresh()
            return jsonify({"ok": True})

        @bp.route("/api/<key>/<user>/kill", methods=["POST"])
        def api_kill(key: str, user: str):
            principal, host = self._guard_mutation(key, user)
            result = self.mutation_runner.kill(
                host, user,
                name=request.form.get("name"),
                confirm=request.form.get("confirm"),
                by=principal.name,
            )
            if not result.ok:
                return jsonify({"ok": False, "error": result.error}), 400
            return jsonify({"ok": True})

        @bp.route("/api/<key>/<user>/create", methods=["POST"])
        def api_create(key: str, user: str):
            principal, host = self._guard_mutation(key, user)
            if not principal.may_create(user):
                abort(403)
            result = self.mutation_runner.create(
                host, user,
                name=request.form.get("name"),
                command=request.form.get("command"),
                by=principal.name,
            )
            if not result.ok:
                return jsonify({"ok": False, "error": result.error}), 400
            return jsonify({"ok": True, "name": result.name})

        @bp.route("/<key>/<user>/<path:name>/attach")
        def attach_view(key: str, user: str, name: str):
            principal = self._principal(request)
            if principal is None:
                return redirect(url_for("muxboard.dashboard"))
            host = self.controller.get_host(key)
            if not host or not self.controller.known_user(key, user):
                abort(404)
            if not principal.may_use(user):
                abort(403)
            return render_template(
                "muxboard/attach.html",
                host_key=key,
                host_label=host.display,
                tmux_user=user,
                session_name=name,
                base=self._url_prefix,
                xterm=self.xterm,
                home_url=self.home_url,
                home_label=self.home_label,
                osc52=self.osc52,
                osc52_push_max=OSC52_PUSH_MAX_BYTES,
            )

    def _guard_mutation(self, key: str, user: str) -> tuple[Principal, Host]:
        principal = self._principal(request)
        if principal is None:
            abort(401)
        host = self.controller.get_host(key)
        if not host or not host.tmux_users:
            abort(404)
        if not self.controller.known_user(key, user):
            abort(404)
        if not principal.may_use(user):
            abort(403)
        return principal, host

    # ---------- WebSocket route ----------

    def _register_ws(self, app: Flask) -> None:
        from flask_sock import Sock

        sock = Sock(app)
        prefix = self._url_prefix

        @sock.route(f"{prefix}/ws/<key>/<user>/<path:name>")
        def ws_attach(ws, key: str, user: str, name: str):
            if not self._origin_ok(request):
                log.warning("muxboard ws: rejecting Origin %r", request.headers.get("Origin"))
                _ws_close(ws, 4003, "forbidden origin")
                return
            principal = self._principal(request)
            if principal is None:
                _ws_close(ws, 4001, "unauthenticated")
                return
            host = self.controller.get_host(key)
            if not host or not self.controller.known_user(key, user):
                _ws_close(ws, 4004, "unknown host/user")
                return
            if not principal.may_use(user):
                log.warning("muxboard ws: %s forbidden user %r on %s",
                            principal.name, user, key)
                _ws_close(ws, 4003, "forbidden user")
                return
            self.attach_runner.run(
                ws,
                by=principal.name,
                host=key,
                target_user=user,
                session_name=name,
                attach_command=lambda: self.controller.attach_argv(host, user, name),
            )


def _ws_close(ws: Any, code: int, message: str) -> None:
    try:
        ws.close(reason=code, message=message)
    except Exception:  # noqa: BLE001
        pass
