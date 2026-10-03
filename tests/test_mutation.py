"""The Mutation runner, driven through its interface.

Each test pairs the runner with a fake tmux controller, a recording refresh
and a recording audit hook: no Flask app and no subprocess.
"""

from muxboard.inventory import Host
from muxboard.mutation import MutationRunner

_HOST = Host(key="local", hostname="localhost", tmux_users=("alice",), local=True)


class FakeController:
    """Records each Kill and create; raises ``error`` instead when it is set."""

    def __init__(self, error=None) -> None:
        self.error = error
        self.calls: list = []

    def kill_session(self, host, user, name):
        self.calls.append(("kill", host.key, user, name))
        if self.error:
            raise self.error

    def create_session(self, host, user, name, command=None):
        self.calls.append(("create", host.key, user, name, command))
        if self.error:
            raise self.error


def _runner(error=None, audit_raises=False):
    controller = FakeController(error)
    refreshed: list = []
    audited: list = []

    def audit(event, **fields):
        audited.append((event, fields))
        if audit_raises:
            raise RuntimeError("audit sink down")

    runner = MutationRunner(controller, refresh=refreshed.append, audit=audit)
    return runner, controller, refreshed, audited


# ---------- Kill ----------


def test_kill_missing_name_is_refused():
    runner, controller, refreshed, audited = _runner()
    result = runner.kill(_HOST, "alice", name="  ", confirm="x", by="admin")
    assert not result.ok
    assert result.error == "missing name"
    assert controller.calls == refreshed == audited == []


def test_kill_absent_name_is_refused():
    runner, *_ = _runner()
    result = runner.kill(_HOST, "alice", name=None, confirm=None, by="admin")
    assert not result.ok
    assert result.error == "missing name"


def test_kill_confirm_mismatch_calls_nothing():
    runner, controller, refreshed, audited = _runner()
    result = runner.kill(_HOST, "alice", name="sess", confirm="wrong", by="admin")
    assert not result.ok
    assert result.error == "confirm must echo the session name ('sess'); got 'wrong'"
    assert controller.calls == refreshed == audited == []


def test_kill_controller_error_is_refused_without_refresh_or_audit():
    runner, controller, refreshed, audited = _runner(error=RuntimeError("no session"))
    result = runner.kill(_HOST, "alice", name="work", confirm="work", by="admin")
    assert not result.ok
    assert result.error == "no session"
    assert controller.calls == [("kill", "local", "alice", "work")]
    assert refreshed == audited == []


def test_kill_success_refreshes_the_host_and_audits():
    runner, controller, refreshed, audited = _runner()
    result = runner.kill(_HOST, "alice", name=" work ", confirm="work ", by="admin")
    assert result.ok
    assert result.error is None
    assert controller.calls == [("kill", "local", "alice", "work")]
    assert refreshed == ["local"]
    assert audited == [("muxboard.kill", {"host": "local", "target_user": "alice",
                                          "session_name": "work", "by": "admin"})]


def test_kill_raising_audit_hook_is_still_success():
    runner, _, refreshed, audited = _runner(audit_raises=True)
    result = runner.kill(_HOST, "alice", name="work", confirm="work", by="admin")
    assert result.ok
    assert refreshed == ["local"]
    assert len(audited) == 1


# ---------- create ----------


def test_create_missing_name_is_refused():
    runner, controller, refreshed, audited = _runner()
    result = runner.create(_HOST, "alice", name=None, command="top", by="admin")
    assert not result.ok
    assert result.error == "missing name"
    assert controller.calls == refreshed == audited == []


def test_create_blank_command_becomes_none():
    runner, controller, _, audited = _runner()
    result = runner.create(_HOST, "alice", name="new1", command="   ", by="admin")
    assert result.ok
    assert controller.calls == [("create", "local", "alice", "new1", None)]
    assert audited[0][1]["command"] is None


def test_create_passes_the_command_through_and_audits_it():
    runner, controller, refreshed, audited = _runner()
    result = runner.create(_HOST, "alice", name="new1", command=" htop ", by="admin")
    assert result.ok
    assert controller.calls == [("create", "local", "alice", "new1", "htop")]
    assert refreshed == ["local"]
    assert audited == [("muxboard.create", {"host": "local", "target_user": "alice",
                                            "session_name": "new1", "command": "htop",
                                            "by": "admin"})]


def test_create_controller_error_is_refused_without_refresh_or_audit():
    runner, _, refreshed, audited = _runner(error=ValueError("bad session name"))
    result = runner.create(_HOST, "alice", name="new1", command=None, by="admin")
    assert not result.ok
    assert result.error == "bad session name"
    assert result.name is None
    assert refreshed == audited == []


def test_create_success_carries_the_name():
    runner, *_ = _runner()
    result = runner.create(_HOST, "alice", name=" new1 ", command=None, by="admin")
    assert result.ok
    assert result.name == "new1"


def test_create_raising_audit_hook_is_still_success():
    runner, _, refreshed, _ = _runner(audit_raises=True)
    result = runner.create(_HOST, "alice", name="new1", command=None, by="admin")
    assert result.ok
    assert result.name == "new1"
    assert refreshed == ["local"]
