import json

import pytest

from muxboard.inventory import Host
from muxboard.listing import ListedSession, Listing


def _host(users=("alice", "bob")):
    return Host(key="a", hostname="a.example.com", ssh_user="ops", tmux_users=users)


def _session(user, name="s", sid="$1"):
    return ListedSession(user=user, name=name, windows=2, created=10,
                         attached=True, activity=20, id=sid)


def _worked():
    return Listing.worked(
        _host(),
        sessions={"alice": [_session("alice", "a1")], "bob": [_session("bob", "b1", "$2")]},
        errors={"alice": "sudo refused", "bob": "socket file unreadable"},
        sweep_ms=12,
    )


def test_worked_takes_users_from_the_host():
    listing = _worked()
    assert listing.ok is True
    assert listing.error is None
    assert listing.users == ("alice", "bob")
    assert listing.sweep_ms == 12
    assert listing.checked_at is None
    assert listing.sessions["alice"] == (_session("alice", "a1"),)


def test_worked_for_an_inventory_only_host_is_empty():
    listing = Listing.worked(_host(users=()), sessions={}, errors={}, sweep_ms=0)
    assert listing.as_dict() == {
        "ok": True, "error": None, "sessions": {}, "errors": {},
        "users": [], "sweep_ms": 0, "checked_at": None,
    }


def test_failed_defaults_to_no_elapsed_time():
    listing = Listing.failed(_host(), "boom")
    assert listing.as_dict() == {
        "ok": False, "error": "boom", "sessions": {}, "errors": {},
        "users": ["alice", "bob"], "sweep_ms": 0, "checked_at": None,
    }
    assert Listing.failed(_host(), "boom", sweep_ms=7).sweep_ms == 7


def test_dict_form_is_the_api_sessions_wire_shape():
    # Pins the JSON `api/sessions` returns for one Host, key order included.
    out = _worked().stamped(1000.0).as_dict()
    assert json.dumps(out) == json.dumps({
        "ok": True,
        "error": None,
        "sessions": {
            "alice": [{"user": "alice", "name": "a1", "windows": 2, "created": 10,
                       "attached": True, "activity": 20, "id": "$1"}],
            "bob": [{"user": "bob", "name": "b1", "windows": 2, "created": 10,
                     "attached": True, "activity": 20, "id": "$2"}],
        },
        "errors": {"alice": "sudo refused", "bob": "socket file unreadable"},
        "users": ["alice", "bob"],
        "sweep_ms": 12,
        "checked_at": 1000.0,
    })


def test_stamped_returns_a_new_listing():
    listing = _worked()
    stamped = listing.stamped(5.0)
    assert stamped.checked_at == 5.0
    assert listing.checked_at is None


def test_scoped_to_no_restriction_keeps_everything():
    listing = _worked()
    assert listing.scoped(None) == listing


def test_scoped_to_some_users_narrows_sessions_errors_and_users():
    out = _worked().scoped(frozenset({"alice"})).as_dict()
    assert list(out["sessions"]) == ["alice"]
    assert list(out["errors"]) == ["alice"]
    assert out["users"] == ["alice"]


def test_scoped_to_every_user_keeps_everything():
    listing = _worked()
    assert listing.scoped(frozenset({"alice", "bob"})) == listing


def test_scoped_to_no_users_leaves_nothing():
    out = _worked().scoped(frozenset()).as_dict()
    assert out["sessions"] == {}
    assert out["errors"] == {}
    assert out["users"] == []


def test_scoping_keeps_stamp_and_outcome():
    listing = Listing.failed(_host(), "boom", sweep_ms=3).stamped(9.0)
    scoped = listing.scoped(frozenset({"bob"}))
    assert (scoped.ok, scoped.error, scoped.sweep_ms, scoped.checked_at) == (
        False, "boom", 3, 9.0,
    )


def test_each_dict_form_is_independent():
    listing = _worked()
    first = listing.as_dict()
    first["ok"] = False
    first["sessions"]["alice"][0]["name"] = "edited"
    first["sessions"]["alice"].clear()
    first["errors"].clear()
    first["users"].append("mallory")
    assert listing.as_dict() == _worked().as_dict()


def test_listing_cannot_be_edited_after_construction():
    sessions = {"alice": [_session("alice")]}
    listing = Listing.worked(_host(), sessions=sessions, errors={}, sweep_ms=0)
    sessions["alice"].append(_session("alice", "late"))
    assert len(listing.sessions["alice"]) == 1
    with pytest.raises(TypeError):
        listing.sessions["bob"] = ()
    with pytest.raises(AttributeError):
        listing.ok = False
