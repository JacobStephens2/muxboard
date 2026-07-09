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

import json
import logging
from typing import Any, Callable, Optional

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
from .tmuxctl import TmuxController
from .ttyproxy import AttachCapacityExceeded, SlotManager, bridge

log = logging.getLogger("muxboard")

# Pinned xterm.js assets. Self-host these (copy into your own static dir and
# override the *_url kwargs) if you do not want a third-party CDN in the
# supply chain of a page that grants shell access. See the README.
_XTERM_JS = "https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/lib/xterm.min.js"
_XTERM_CSS = "https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/css/xterm.min.css"
_XTERM_FIT = "https://cdn.jsdelivr.net/npm/@xterm/addon-fit@0.10.0/lib/addon-fit.min.js"

# Audit hook signature: (event_name, **fields) -> None.
AuditHook = Callable[..., None]


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
        xterm_js_url: str = _XTERM_JS,
        xterm_css_url: str = _XTERM_CSS,
        xterm_fit_url: str = _XTERM_FIT,
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
        """
        self.controller = TmuxController(
            hosts,
            ssh_key=ssh_key,
            ssh_timeout=ssh_timeout,
            interval=sweep_interval,
            strict_host_key_checking=strict_host_key_checking,
        )
        self.authorize = authorize
        self.slots = SlotManager(
            max_per_user=attach_max_per_user, max_global=attach_max_global
        )
        self.allowed_origins = (
            {o.rstrip("/") for o in allowed_origins} if allowed_origins else None
        )
        self.audit = audit or _noop_audit
        self.xterm = {
            "js": xterm_js_url, "css": xterm_css_url, "fit": xterm_fit_url,
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

    def _filter_snapshot(self, snap: dict[str, Any], principal: Principal) -> dict[str, Any]:
        if principal.allowed_users is None:
            return snap
        allowed = principal.allowed_users
        hosts = []
        for h in snap.get("hosts", []):
            result = h.get("result")
            if result:
                result = {
                    **result,
                    "sessions": {u: v for u, v in (result.get("sessions") or {}).items() if u in allowed},
                    "errors": {u: v for u, v in (result.get("errors") or {}).items() if u in allowed},
                    "users": [u for u in (result.get("users") or []) if u in allowed],
                }
            hosts.append({
                **h,
                "users": [u for u in h.get("users", []) if u in allowed],
                "result": result,
            })
        return {**snap, "hosts": hosts}

    # ---------- lifecycle ----------

    def start(self) -> None:
        """Start the background sweep thread. Call once after init_app."""
        self.controller.start_background_loop()

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
            snap = self._filter_snapshot(self.controller.snapshot(), principal)
            return render_template(
                "muxboard/dashboard.html",
                snapshot=snap,
                base=self._url_prefix,
                principal=principal,
            )

        @bp.route("/api/sessions")
        def api_sessions():
            principal = self._principal(request)
            if principal is None:
                abort(401)
            return jsonify(self._filter_snapshot(self.controller.snapshot(), principal))

        @bp.route("/api/refresh", methods=["POST"])
        def api_refresh():
            principal = self._principal(request)
            if principal is None:
                abort(401)
            key = (request.args.get("key") or "").strip()
            if key:
                result = self.controller.refresh_host(key)
                if result is None:
                    abort(404)
                return jsonify({"ok": True, "key": key})
            self.controller.force_sweep()
            return jsonify({"ok": True})

        @bp.route("/api/<key>/<user>/kill", methods=["POST"])
        def api_kill(key: str, user: str):
            principal, host = self._guard_mutation(key, user)
            name = (request.form.get("name") or "").strip()
            if not name:
                abort(400, description="missing name")
            # Defense-in-depth: the client must echo the session name in
            # `confirm`. Blocks accidental same-site POSTs (a future XSS, a
            # fat-fingered curl, a malicious browser extension).
            confirm = (request.form.get("confirm") or "").strip()
            if confirm != name:
                return jsonify({
                    "ok": False,
                    "error": f"confirm must echo the session name ({name!r}); got {confirm!r}",
                }), 400
            try:
                self.controller.kill_session(host, user, name)
            except Exception as exc:  # noqa: BLE001
                return jsonify({"ok": False, "error": str(exc)}), 400
            self._safe_refresh(key)
            self.audit("muxboard.kill", host=key, target_user=user,
                       session_name=name, by=principal.name)
            return jsonify({"ok": True})

        @bp.route("/api/<key>/<user>/create", methods=["POST"])
        def api_create(key: str, user: str):
            principal, host = self._guard_mutation(key, user)
            if not principal.may_create(user):
                abort(403)
            name = (request.form.get("name") or "").strip()
            command = (request.form.get("command") or "").strip() or None
            if not name:
                abort(400, description="missing name")
            try:
                self.controller.create_session(host, user, name, command)
            except Exception as exc:  # noqa: BLE001
                return jsonify({"ok": False, "error": str(exc)}), 400
            self._safe_refresh(key)
            self.audit("muxboard.create", host=key, target_user=user,
                       session_name=name, command=command, by=principal.name)
            return jsonify({"ok": True, "name": name})

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

    def _safe_refresh(self, key: str) -> None:
        try:
            self.controller.refresh_host(key)
        except Exception:  # noqa: BLE001
            log.exception("muxboard: post-mutation refresh failed for %s", key)

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
            try:
                argv, env_add = self.controller.attach_argv(host, user, name)
            except Exception as exc:  # noqa: BLE001
                _ws_error_close(ws, str(exc))
                return
            try:
                self.slots.acquire(principal.name)
            except AttachCapacityExceeded as exc:
                log.warning("muxboard attach refused (cap): %s by=%s", exc, principal.name)
                _ws_error_close(ws, str(exc), code=4029)
                return
            self.audit("muxboard.attach.start", host=key, target_user=user,
                       session_name=name, by=principal.name)
            try:
                bridge(ws, argv, env_add)
            except Exception:  # noqa: BLE001
                log.exception("muxboard bridge crashed")
            finally:
                self.slots.release(principal.name)
                self.audit("muxboard.attach.end", host=key, target_user=user,
                           session_name=name, by=principal.name)


def _ws_close(ws: Any, code: int, message: str) -> None:
    try:
        ws.close(reason=code, message=message)
    except Exception:  # noqa: BLE001
        pass


def _ws_error_close(ws: Any, message: str, code: Optional[int] = None) -> None:
    try:
        ws.send(json.dumps({"type": "error", "message": message}))
        if code is not None:
            ws.close(reason=code, message=message[:120])
        else:
            ws.close()
    except Exception:  # noqa: BLE001
        pass
