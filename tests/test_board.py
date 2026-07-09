from flask import Flask

from muxboard import Host, Muxboard, Principal


def _app(authorize):
    board = Muxboard(
        hosts=[Host(key="local", hostname="localhost",
                    tmux_users=("alice", "bob"), local=True)],
        authorize=authorize,
        allowed_origins=["http://localhost"],
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


def test_kill_requires_confirm_echo():
    app, _ = _app(lambda r: Principal(name="admin"))
    client = app.test_client()
    r = client.post("/mux/api/local/alice/kill", data={"name": "sess", "confirm": "wrong"})
    assert r.status_code == 400
    assert r.get_json()["ok"] is False
