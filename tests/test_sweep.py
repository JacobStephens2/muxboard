import threading

from muxboard.auth import Principal
from muxboard.inventory import Host
from muxboard.listing import ListedSession, Listing
from muxboard.sweep import Sweep


def _host(key, users=("alice", "bob")):
    return Host(key=key, hostname=f"{key}.example.com", ssh_user="ops", tmux_users=users)


def _ok(host):
    return Listing.worked(
        host,
        sessions={u: [ListedSession(user=u, name=f"{u}-1", windows=1, created=1,
                                    attached=False, activity=1, id="$1")]
                  for u in host.tmux_users},
        errors={u: "sudo refused" for u in host.tmux_users},
        sweep_ms=1,
    )


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _sweep(hosts, lister=_ok, clock=None):
    return Sweep(hosts, lister, interval=60, _clock=clock or _Clock())


_ADMIN = Principal(name="admin")


def _results(sweep):
    return {h["key"]: h["result"] for h in sweep.view(_ADMIN)["hosts"]}


def test_view_before_any_sweep_lists_hosts_with_no_results():
    view = _sweep([_host("a")]).view(_ADMIN)
    assert view == {
        "hosts": [{
            "key": "a", "hostname": "a.example.com", "label": "a.example.com",
            "users": ["alice", "bob"], "result": None,
        }],
        "last_sweep": None,
    }


def test_refresh_all_records_every_host_and_stamps_last_sweep():
    clock = _Clock()
    sweep = _sweep([_host("a"), _host("b")], clock=clock)
    assert sweep.refresh() is True
    view = sweep.view(_ADMIN)
    assert view["last_sweep"] == 1000.0
    results = _results(sweep)
    assert results["a"]["ok"] is True
    assert results["a"]["checked_at"] == 1000.0
    assert results["b"]["sessions"]["alice"][0]["name"] == "alice-1"


def test_single_host_refresh_records_one_host_and_leaves_last_sweep():
    clock = _Clock()
    sweep = _sweep([_host("a"), _host("b")], clock=clock)
    assert sweep.refresh("a") is True
    assert sweep.view(_ADMIN)["last_sweep"] is None
    results = _results(sweep)
    assert results["a"]["checked_at"] == 1000.0
    assert results["b"] is None


def test_single_host_refresh_keeps_the_earlier_last_sweep():
    clock = _Clock()
    sweep = _sweep([_host("a")], clock=clock)
    sweep.refresh()
    clock.now = 2000.0
    sweep.refresh("a")
    assert sweep.view(_ADMIN)["last_sweep"] == 1000.0
    assert _results(sweep)["a"]["checked_at"] == 2000.0


def test_refresh_unknown_key_returns_false():
    sweep = _sweep([_host("a")])
    assert sweep.refresh("nope") is False
    assert _results(sweep) == {"a": None}


def test_inventory_only_host_is_never_listed_or_refreshed():
    called = []

    def lister(host):
        called.append(host.key)
        return _ok(host)

    sweep = _sweep([_host("a"), _host("inv", users=())], lister=lister)
    assert sweep.refresh("inv") is False
    sweep.refresh()
    assert called == ["a"]
    assert [h["key"] for h in sweep.view(_ADMIN)["hosts"]] == ["a"]


def test_raising_lister_records_a_failure_and_other_hosts_still_recorded():
    def lister(host):
        if host.key == "bad":
            raise RuntimeError("ssh exploded")
        return _ok(host)

    sweep = _sweep([_host("bad"), _host("good")], lister=lister)
    sweep.refresh()
    results = _results(sweep)
    assert results["bad"]["ok"] is False
    assert "ssh exploded" in results["bad"]["error"]
    assert results["bad"]["users"] == ["alice", "bob"]
    assert results["good"]["ok"] is True
    assert sweep.view(_ADMIN)["last_sweep"] is not None


def test_raising_lister_on_single_host_refresh_is_recorded_not_raised():
    def lister(host):
        raise RuntimeError("ssh exploded")

    sweep = _sweep([_host("a")], lister=lister)
    assert sweep.refresh("a") is True
    result = _results(sweep)["a"]
    assert result["ok"] is False
    assert "ssh exploded" in result["error"]


def test_view_scopes_sessions_errors_and_both_users_lists():
    sweep = _sweep([_host("a")])
    sweep.refresh()
    scoped = Principal(name="u", allowed_users=frozenset({"alice"}))
    host = sweep.view(scoped)["hosts"][0]
    assert host["users"] == ["alice"]
    assert list(host["result"]["sessions"]) == ["alice"]
    assert list(host["result"]["errors"]) == ["alice"]
    assert host["result"]["users"] == ["alice"]


def test_view_scopes_hosts_with_no_result_yet():
    scoped = Principal(name="u", allowed_users=frozenset({"bob"}))
    host = _sweep([_host("a")]).view(scoped)["hosts"][0]
    assert host["users"] == ["bob"]
    assert host["result"] is None


def test_unscoped_principal_sees_everything():
    sweep = _sweep([_host("a")])
    sweep.refresh()
    host = sweep.view(_ADMIN)["hosts"][0]
    assert host["users"] == ["alice", "bob"]
    assert set(host["result"]["sessions"]) == {"alice", "bob"}
    assert set(host["result"]["errors"]) == {"alice", "bob"}
    assert host["result"]["users"] == ["alice", "bob"]


def test_editing_a_view_leaves_the_store_alone():
    sweep = _sweep([_host("a")])
    sweep.refresh()
    sweep.view(_ADMIN)["hosts"][0]["result"]["ok"] = False
    assert _results(sweep)["a"]["ok"] is True


def test_start_twice_starts_one_loop(monkeypatch):
    started = []
    real_thread = threading.Thread

    class _Thread(real_thread):
        def start(self):
            started.append(self.name)

    monkeypatch.setattr("muxboard.sweep.threading.Thread", _Thread)
    sweep = _sweep([_host("a")])
    sweep.start()
    sweep.start()
    assert started == ["muxboard-sweep"]

