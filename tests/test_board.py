import pytest
from flask import Flask

from muxboard import Host, Muxboard, Principal


def _app(authorize, **kwargs):
    board = Muxboard(
        hosts=[Host(key="local", hostname="localhost",
                    tmux_users=("alice", "bob"), local=True)],
        authorize=authorize,
        allowed_origins=["http://localhost"],
        **kwargs,
    )
    app = Flask(__name__)
    app.testing = True
    board.init_app(app, url_prefix="/mux")
    return app, board


def test_deny_all_returns_401():
    app, _ = _app(lambda r: None)
    client = app.test_client()
    assert client.get("/mux/").status_code == 401
    assert client.get("/mux/api/sessions").status_code == 401
    assert client.post("/mux/api/local/alice/kill", data={"name": "x", "confirm": "x"}).status_code == 401


def test_admin_renders_dashboard():
    app, _ = _app(lambda r: Principal(name="admin"))
    client = app.test_client()
    r = client.get("/mux/")
    assert r.status_code == 200
    assert b"muxboard" in r.data
    assert b"alice" in r.data
    assert b"bob" in r.data


def test_scoped_principal_filters_sessions_json():
    app, board = _app(lambda r: Principal(name="u", allowed_users=frozenset({"alice"})))
    # Seed the store as if a sweep had run.
    board.controller._store.update("local", {
        "ok": True, "error": None,
        "sessions": {"alice": [{"name": "a", "windows": 1, "created": 1,
                                 "attached": False, "activity": 1, "id": "$1"}],
                     "bob": [{"name": "b", "windows": 1, "created": 1,
                              "attached": False, "activity": 1, "id": "$2"}]},
        "errors": {}, "users": ["alice", "bob"], "sweep_ms": 1,
    })
    client = app.test_client()
    data = client.get("/mux/api/sessions").get_json()
    host = data["hosts"][0]
    assert host["users"] == ["alice"]
    assert "alice" in host["result"]["sessions"]
    assert "bob" not in host["result"]["sessions"]


def test_scoped_principal_forbidden_user_403():
    app, _ = _app(lambda r: Principal(name="u", allowed_users=frozenset({"alice"})))
    client = app.test_client()
    # bob is a known user but out of scope -> 403, not 404.
    r = client.post("/mux/api/local/bob/create", data={"name": "x"})
    assert r.status_code == 403


def test_create_scope_can_be_narrower_than_use_scope():
    app, _ = _app(lambda r: Principal(
        name="u",
        allowed_users=frozenset({"alice", "bob"}),
        create_users=frozenset({"alice"}),
    ))
    client = app.test_client()
    r = client.post("/mux/api/local/bob/create", data={"name": "x"})
    assert r.status_code == 403


def test_create_returns_created_name():
    app, board = _app(lambda r: Principal(
        name="u",
        allowed_users=frozenset({"alice", "bob"}),
        create_users=frozenset({"alice"}),
    ))
    called = {}

    def fake_create(host, user, name, command=None):
        called.update(host=host.key, user=user, name=name, command=command)

    board.controller.create_session = fake_create
    board.controller.refresh_host = lambda key: {"ok": True}

    client = app.test_client()
    r = client.post("/mux/api/local/alice/create", data={"name": "new1"})
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "name": "new1"}
    assert called == {"host": "local", "user": "alice", "name": "new1", "command": None}


def test_unknown_host_404():
    app, _ = _app(lambda r: Principal(name="admin"))
    client = app.test_client()
    r = client.post("/mux/api/nope/alice/kill", data={"name": "x", "confirm": "x"})
    assert r.status_code == 404


def test_kill_missing_name_400():
    app, _ = _app(lambda r: Principal(name="admin"))
    client = app.test_client()
    r = client.post("/mux/api/local/alice/kill", data={"confirm": "x"})
    assert r.status_code == 400


def test_kill_requires_confirm_echo():
    app, _ = _app(lambda r: Principal(name="admin"))
    client = app.test_client()
    r = client.post("/mux/api/local/alice/kill", data={"name": "sess", "confirm": "wrong"})
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


def test_kill_success_returns_ok():
    app, board = _app(lambda r: Principal(name="admin"))
    called = {}

    def fake_kill(host, user, name):
        called.update(host=host.key, user=user, name=name)

    board.controller.kill_session = fake_kill
    board.controller.refresh_host = lambda key: {"ok": True}

    client = app.test_client()
    r = client.post("/mux/api/local/alice/kill", data={"name": "work", "confirm": "work"})
    assert r.status_code == 200
    assert r.get_json() == {"ok": True}
    assert called == {"host": "local", "user": "alice", "name": "work"}


def test_kill_controller_error_returns_ok_false():
    app, board = _app(lambda r: Principal(name="admin"))

    def fake_kill(host, user, name):
        raise RuntimeError("no session")

    board.controller.kill_session = fake_kill

    client = app.test_client()
    r = client.post("/mux/api/local/alice/kill", data={"name": "work", "confirm": "work"})
    assert r.status_code == 400
    body = r.get_json()
    assert body["ok"] is False
    assert "no session" in body["error"]


def test_attach_page_includes_kill_control():
    app, _ = _app(lambda r: Principal(name="admin"))
    client = app.test_client()
    r = client.get("/mux/local/alice/work/attach")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "← Muxboard" in html
    assert 'data-mb-attach-kill' in html
    assert "mb-btn-danger" in html
    assert "work" in html


def test_attach_page_kill_posts_name_and_confirm():
    app, _ = _app(lambda r: Principal(name="admin"))
    client = app.test_client()
    html = client.get("/mux/local/alice/work/attach").get_data(as_text=True)
    assert "var SESSION_NAME = " in html
    assert '"work"' in html
    assert "body.set('name', SESSION_NAME)" in html
    assert "body.set('confirm', SESSION_NAME)" in html


def test_attach_page_kill_success_closes_or_shows_killed_state():
    app, _ = _app(lambda r: Principal(name="admin"))
    client = app.test_client()
    html = client.get("/mux/local/alice/work/attach").get_data(as_text=True)
    assert "window.close()" in html
    assert 'id="mb-killed"' in html
    assert "session killed" in html.lower()


def test_attach_page_kill_failure_shows_error():
    app, _ = _app(lambda r: Principal(name="admin"))
    client = app.test_client()
    html = client.get("/mux/local/alice/work/attach").get_data(as_text=True)
    assert 'id="mb-kill-error"' in html
    assert "mb-error" in html


def test_osc52_defaults_off():
    _, board = _app(lambda r: Principal(name="admin"))
    assert board.osc52 == "off"


def test_osc52_rejects_unknown_value():
    with pytest.raises(ValueError, match="osc52"):
        _app(lambda r: Principal(name="admin"), osc52="on")


def test_osc52_accepts_write_and_read_write():
    _, write_board = _app(lambda r: Principal(name="admin"), osc52="write")
    _, rw_board = _app(lambda r: Principal(name="admin"), osc52="read-write")
    assert write_board.osc52 == "write"
    assert rw_board.osc52 == "read-write"


def _attach(osc52="off"):
    app, _ = _app(lambda r: Principal(name="admin"), osc52=osc52)
    return app.test_client().get("/mux/local/alice/job/attach")


def test_attach_page_has_copy_control():
    r = _attach()
    assert r.status_code == 200
    html = r.data.decode()
    assert 'data-mb-copy' in html
    assert ">Copy<" in html
    assert 'data-mb-osc52="off"' in html


def test_attach_page_loads_attach_client_after_wrap_url_helper():
    r = _attach()
    assert r.status_code == 200
    html = r.data.decode()
    assert "muxboard/wrap-url.js" in html
    assert "muxboard/attach.js" in html
    assert html.index("muxboard/wrap-url.js") < html.index("muxboard/attach.js")
    assert "addon-web-links" not in html


def test_attach_omits_clipboard_addon_when_osc52_off():
    r = _attach("off")
    assert r.status_code == 200
    assert b"addon-clipboard" not in r.data
    assert b"<dialog data-mb-clip-query" not in r.data


def test_attach_includes_clipboard_addon_when_osc52_write():
    r = _attach("write")
    assert r.status_code == 200
    html = r.data.decode()
    assert "addon-clipboard" in html
    assert 'data-mb-osc52="write"' in html
    assert "<dialog data-mb-clip-query" not in html
    assert "65536" in html


def test_attach_query_prompt_when_osc52_read_write():
    r = _attach("read-write")
    assert r.status_code == 200
    html = r.data.decode()
    assert "addon-clipboard" in html
    assert 'data-mb-osc52="read-write"' in html
    assert "<dialog data-mb-clip-query" in html
    assert "data-mb-clip-allow" in html
    assert "data-mb-clip-deny" in html
