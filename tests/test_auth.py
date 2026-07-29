import pytest
from itsdangerous import URLSafeTimedSerializer
from werkzeug.test import EnvironBuilder

from muxboard.auth import (
    Principal,
    allow_all,
    deny_all,
    header_auth,
    signed_cookie_auth,
    token_auth,
)


def _req(headers=None, query="", cookies=None):
    hdrs = dict(headers or {})
    if cookies:
        hdrs["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    builder = EnvironBuilder(headers=hdrs, query_string=query)
    return builder.get_request()


def test_deny_all_denies():
    assert deny_all(_req()) is None


def test_allow_all_allows():
    p = allow_all("svc")(_req())
    assert p is not None
    assert p.name == "svc"
    assert p.allowed_users is None
    assert p.create_users is None


def test_token_rejects_short_secret():
    with pytest.raises(ValueError):
        token_auth("short")


def test_token_header_match():
    auth = token_auth("a" * 20)
    assert auth(_req(headers={"X-Muxboard-Token": "a" * 20})) is not None
    assert auth(_req(headers={"X-Muxboard-Token": "b" * 20})) is None
    assert auth(_req()) is None


def test_token_query_match():
    auth = token_auth("s" * 20)
    assert auth(_req(query="token=" + "s" * 20)) is not None
    assert auth(_req(query="token=nope")) is None


def test_principal_may_use_scope():
    p = Principal(name="u", allowed_users=frozenset({"deploy"}))
    assert p.may_use("deploy")
    assert not p.may_use("ops")
    assert Principal(name="admin").may_use("anything")


def test_principal_create_scope_defaults_to_use_scope():
    p = Principal(name="u", allowed_users=frozenset({"deploy"}))
    assert p.may_create("deploy")
    assert not p.may_create("ops")


def test_principal_create_scope_can_be_narrower():
    p = Principal(
        name="u",
        allowed_users=frozenset({"deploy", "ops"}),
        create_users=frozenset({"deploy"}),
    )
    assert p.may_use("ops")
    assert not p.may_create("ops")
    assert p.may_create("deploy")


def test_token_auth_create_scope():
    auth = token_auth(
        "a" * 20,
        allowed_users=frozenset({"alice", "shared"}),
        create_users=frozenset({"alice"}),
    )
    p = auth(_req(headers={"X-Muxboard-Token": "a" * 20}))
    assert p is not None
    assert p.may_use("shared")
    assert not p.may_create("shared")
    assert p.may_create("alice")


def test_signed_cookie_auth_accepts_valid_cookie():
    secret = "s" * 32
    salt = "dashboard-session"
    token = URLSafeTimedSerializer(secret, salt=salt).dumps({"admin": True})
    auth = signed_cookie_auth(
        secret,
        cookie="dash_session",
        salt=salt,
        name="jacob",
        allowed_users=frozenset({"jacob"}),
    )
    p = auth(_req(cookies={"dash_session": token}))
    assert p is not None
    assert p.name == "jacob"
    assert p.may_use("jacob")
    assert not p.may_use("root")


def test_signed_cookie_auth_name_claim():
    secret = "s" * 32
    token = URLSafeTimedSerializer(secret, salt="s").dumps({"email": "a@b.c"})
    auth = signed_cookie_auth(
        secret, cookie="sess", salt="s", name_claim="email", name="fallback",
    )
    p = auth(_req(cookies={"sess": token}))
    assert p is not None
    assert p.name == "a@b.c"


def test_signed_cookie_auth_rejects_bad_or_missing():
    secret = "s" * 32
    auth = signed_cookie_auth(secret, cookie="sess", salt="s")
    assert auth(_req()) is None
    assert auth(_req(cookies={"sess": "not-a-real-token"})) is None
    other = URLSafeTimedSerializer("t" * 32, salt="s").dumps({"admin": True})
    assert auth(_req(cookies={"sess": other})) is None


def test_signed_cookie_rejects_short_secret():
    with pytest.raises(ValueError):
        signed_cookie_auth("short")


def test_header_auth():
    auth = header_auth("X-Remote-User", allowed_users=frozenset({"deploy"}))
    assert auth(_req()) is None
    p = auth(_req(headers={"X-Remote-User": "alice"}))
    assert p is not None
    assert p.name == "alice"
    assert p.may_use("deploy")
