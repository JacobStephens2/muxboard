"""The Attach bridge and the Attach runner, driven through their interfaces.

Each bridge test pairs a fake WebSocket with a real PTY running a real local
command, and runs the bridge in a thread with a bounded join so a hang fails
the test instead of stalling the suite. Attach runner tests reuse the fake
WebSocket and real short-lived processes, and check that a slot is free by
running a second Attach under caps of 1.
"""

import json
import os
import queue
import signal
import threading
import time

import pytest

from muxboard.ttyproxy import (
    AttachCapacityExceeded,
    AttachRunner,
    SlotManager,
    bridge,
    error_close,
)

_JOIN_SECONDS = 10

_ECHO_LINES = ["sh", "-c", 'while read l; do echo "got:$l"; done']
_PRINT_PTY_SIZE = ["sh", "-c", "while read l; do stty size; done"]


class FakeWebSocket:
    """``receive(timeout)`` reads from a queue; ``send`` records the frame.

    Queue an exception instance to have ``receive`` raise it.
    """

    def __init__(self, *, send_raises: bool = False, close_raises: bool = False) -> None:
        self.inbox: queue.Queue = queue.Queue()
        self.sent: list = []
        self.closed: list = []
        self.send_raises = send_raises
        self.close_raises = close_raises
        self._lock = threading.Lock()

    def push(self, msg) -> None:
        self.inbox.put(msg)

    def push_json(self, obj) -> None:
        self.inbox.put(json.dumps(obj))

    def receive(self, timeout=None):
        try:
            msg = self.inbox.get(timeout=timeout)
        except queue.Empty:
            return None
        if isinstance(msg, BaseException):
            raise msg
        return msg

    def send(self, data) -> None:
        if self.send_raises:
            raise ConnectionError("browser has gone")
        with self._lock:
            self.sent.append(data)

    def close(self, reason=None, message=None) -> None:
        if self.close_raises:
            raise ConnectionError("browser has gone")
        self.closed.append((reason, message))

    def output(self) -> str:
        with self._lock:
            frames = list(self.sent)
        return b"".join(frames).decode("utf-8", errors="replace")

    def wait_for(self, text: str, start: int = 0, timeout: float = 5.0) -> int:
        """Wait until ``text`` appears in the output after ``start``; return the end offset."""
        idx = _poll(lambda: self.output().find(text, start) + 1, timeout,
                    lambda: f"{text!r} never arrived; output was {self.output()[start:]!r}")
        return idx - 1 + len(text)


def _poll(check, timeout: float, failure):
    """Call ``check`` until it returns something truthy, and return that; fail after ``timeout``."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.02)
    raise AssertionError(failure())


def _start(ws, argv, **kwargs) -> threading.Thread:
    t = threading.Thread(target=bridge, args=(ws, argv, {}), kwargs=kwargs, daemon=True)
    t.start()
    return t


def _join(t: threading.Thread) -> None:
    t.join(timeout=_JOIN_SECONDS)
    assert not t.is_alive(), "bridge did not return"


def _running(pid: int) -> bool:
    """Whether ``pid`` exists and is not a zombie (``/proc`` where present, else signal 0)."""
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False
    except OSError:
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_gone(pid: int) -> None:
    try:
        _poll(lambda: not _running(pid), 5.0, lambda: f"pid {pid} outlived the bridge")
    except AssertionError:
        os.kill(pid, signal.SIGKILL)
        raise


def _read_pid(path) -> int:
    text = _poll(lambda: path.exists() and path.read_text().strip(), 5.0,
                 lambda: "grandchild pid never written")
    return int(text)


def _grandchild_argv(pid_file) -> list:
    """A shell that backgrounds a long ``sleep`` (our grandchild) and records its pid."""
    return ["sh", "-c", f'sleep 300 & echo $! > "{pid_file}"; echo ready; wait']


# ---------- frames ----------


def test_input_reaches_process_and_output_returns_as_bytes():
    ws = FakeWebSocket()
    t = _start(ws, _ECHO_LINES)
    try:
        ws.push_json({"type": "input", "data": "hello\n"})
        ws.wait_for("got:hello")
        assert ws.sent and all(isinstance(f, bytes) for f in ws.sent)
    finally:
        ws.push(ConnectionError("done"))
        _join(t)


def test_raw_text_and_binary_frames_are_written_through_as_input():
    ws = FakeWebSocket()
    t = _start(ws, _ECHO_LINES)
    try:
        ws.push("not json\n")
        end = ws.wait_for("got:not json")
        ws.push(b"binary\n")
        ws.wait_for("got:binary", end)
    finally:
        ws.push(ConnectionError("done"))
        _join(t)


def test_empty_input_does_nothing():
    ws = FakeWebSocket()
    t = _start(ws, _ECHO_LINES)
    try:
        ws.push_json({"type": "input", "data": ""})
        ws.push_json({"type": "input"})
        ws.push_json({"type": "input", "data": "after\n"})
        ws.wait_for("got:after")
        assert ws.output().count("got:") == 1
    finally:
        ws.push(ConnectionError("done"))
        _join(t)


@pytest.mark.parametrize(
    "frame, expected",
    [
        ({"rows": 40, "cols": 100}, "40 100"),
        ({"rows": 9999, "cols": 9999}, "500 1000"),
        ({"rows": -5, "cols": -5}, "1 1"),
        ({"rows": "tall", "cols": "wide"}, "24 120"),
        ({"rows": [1], "cols": {}}, "24 120"),
    ],
)
def test_resize_sets_clamped_pty_size(frame, expected):
    ws = FakeWebSocket()
    t = _start(ws, _PRINT_PTY_SIZE)
    try:
        # Move off the default first so a malformed resize is seen to reset it.
        ws.push_json({"type": "resize", "rows": 33, "cols": 77})
        ws.push_json({"type": "input", "data": "\n"})
        end = ws.wait_for("33 77")
        ws.push_json({"type": "resize", **frame})
        ws.push_json({"type": "input", "data": "\n"})
        ws.wait_for(expected, end)
    finally:
        ws.push(ConnectionError("done"))
        _join(t)


def test_ping_and_unknown_frames_are_ignored():
    ws = FakeWebSocket()
    t = _start(ws, _ECHO_LINES)
    try:
        ws.push_json({"type": "ping"})
        ws.push_json({"type": "nonsense", "data": "ignored\n"})
        ws.push_json({"type": "input", "data": "still here\n"})
        ws.wait_for("got:still here")
        assert "got:ignored" not in ws.output()
        assert t.is_alive()
    finally:
        ws.push(ConnectionError("done"))
        _join(t)


# ---------- teardown ----------


def test_returns_when_process_exits():
    ws = FakeWebSocket()
    t = _start(ws, ["sh", "-c", "exit 0"])
    _join(t)


def test_receive_failure_tears_down_whole_process_group(tmp_path):
    pid_file = tmp_path / "pid"
    ws = FakeWebSocket()
    t = _start(ws, _grandchild_argv(pid_file))
    grandchild = _read_pid(pid_file)
    assert _running(grandchild)
    ws.push(ConnectionError("browser has gone"))
    _join(t)
    _wait_gone(grandchild)


def test_send_failure_tears_process_down(tmp_path):
    pid_file = tmp_path / "pid"
    ws = FakeWebSocket(send_raises=True)
    t = _start(ws, _grandchild_argv(pid_file))
    grandchild = _read_pid(pid_file)
    _join(t)
    _wait_gone(grandchild)


def test_lifetime_cap_tears_process_down(tmp_path):
    pid_file = tmp_path / "pid"
    ws = FakeWebSocket()
    t = _start(ws, _grandchild_argv(pid_file), max_lifetime=0.5)
    grandchild = _read_pid(pid_file)
    _join(t)
    _wait_gone(grandchild)


# ---------- error frame ----------


def test_error_close_with_code_sends_frame_then_closes_with_truncated_reason():
    ws = FakeWebSocket()
    message = "x" * 200
    error_close(ws, message, code=4029)
    assert [json.loads(f) for f in ws.sent] == [{"type": "error", "message": message}]
    assert ws.closed == [(4029, "x" * 120)]


def test_error_close_without_code_sends_frame_then_closes_plainly():
    ws = FakeWebSocket()
    error_close(ws, "no such session")
    assert [json.loads(f) for f in ws.sent] == [{"type": "error", "message": "no such session"}]
    assert ws.closed == [(None, None)]


def test_error_close_swallows_a_failing_socket():
    ws = FakeWebSocket(send_raises=True)
    error_close(ws, "boom", code=4029)
    assert ws.sent == []

    ws = FakeWebSocket(close_raises=True)
    error_close(ws, "boom")
    assert len(ws.sent) == 1


# ---------- slot accounting ----------


def test_per_principal_cap_refuses_next_acquire():
    slots = SlotManager(max_per_user=2, max_global=10)
    slots.acquire("alice")
    slots.acquire("alice")
    with pytest.raises(AttachCapacityExceeded, match="max 2 per user"):
        slots.acquire("alice")
    slots.acquire("bob")


def test_global_cap_refuses_across_principals():
    slots = SlotManager(max_per_user=5, max_global=2)
    slots.acquire("alice")
    slots.acquire("bob")
    with pytest.raises(AttachCapacityExceeded, match="global cap of 2"):
        slots.acquire("carol")


def test_principal_name_ignores_case_and_whitespace():
    slots = SlotManager(max_per_user=1, max_global=10)
    slots.acquire("Alice")
    with pytest.raises(AttachCapacityExceeded):
        slots.acquire("  alice ")
    slots.release(" ALICE")
    slots.acquire("alice")


def test_release_frees_a_slot():
    slots = SlotManager(max_per_user=1, max_global=1)
    slots.acquire("alice")
    slots.release("alice")
    slots.acquire("bob")
    assert slots.snapshot()["total"] == 1


def test_release_never_drops_total_below_zero():
    slots = SlotManager()
    slots.release("alice")
    slots.release("alice")
    assert slots.snapshot()["total"] == 0
    slots.acquire("alice")
    assert slots.snapshot()["total"] == 1


def test_snapshot_reports_totals_and_limits():
    slots = SlotManager(max_per_user=3, max_global=7)
    slots.acquire("alice")
    slots.acquire("alice")
    slots.acquire("bob")
    assert slots.snapshot() == {
        "total": 3,
        "max_global": 7,
        "max_per_user": 3,
        "by_user": {"alice": 2, "bob": 1},
    }


# ---------- Attach runner ----------

_EXIT_NOW = ["sh", "-c", "exit 0"]
_FIELDS = {"host": "local", "target_user": "deploy", "session_name": "work", "by": "alice"}


class RecordingAudit:
    """Records each ``(event, fields)``; raises on the events named in ``raise_on``."""

    def __init__(self, *raise_on: str) -> None:
        self.events: list = []
        self.raise_on = set(raise_on)

    def __call__(self, event: str, **fields) -> None:
        self.events.append((event, fields))
        if event in self.raise_on:
            raise RuntimeError(f"audit sink down on {event}")

    def names(self) -> list:
        return [event for event, _ in self.events]


class AttachCommand:
    """The runner's zero-argument attach command callable; counts its calls."""

    def __init__(self, argv=None, env=None, raises=None) -> None:
        self.argv = argv or _EXIT_NOW
        self.env = env or {}
        self.raises = raises
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return self.argv, self.env


def _runner(audit=None, *, max_per_user=1, max_global=1) -> AttachRunner:
    return AttachRunner(max_per_user=max_per_user, max_global=max_global,
                        audit=audit or RecordingAudit())


def _run(runner, ws, command, by="alice") -> None:
    t = threading.Thread(
        target=runner.run,
        args=(ws,),
        kwargs={"by": by, "host": "local", "target_user": "deploy",
                "session_name": "work", "attach_command": command},
        daemon=True,
    )
    t.start()
    _join(t)


def _errors(ws) -> list:
    return [json.loads(f)["message"] for f in ws.sent if isinstance(f, str)]


def _assert_slot_free(runner, by="alice") -> None:
    command = AttachCommand()
    ws = FakeWebSocket()
    _run(runner, ws, command, by=by)
    assert command.calls == 1, f"slot still held: {ws.sent!r} {ws.closed!r}"


def test_runner_normal_run_emits_start_and_end_and_frees_the_slot():
    audit = RecordingAudit()
    runner = _runner(audit)
    command = AttachCommand(argv=_ECHO_LINES)
    ws = FakeWebSocket()
    ws.push_json({"type": "input", "data": "hi\n"})
    t = threading.Thread(
        target=runner.run, args=(ws,),
        kwargs={**_FIELDS, "attach_command": command}, daemon=True,
    )
    t.start()
    try:
        ws.wait_for("got:hi")
        assert audit.events == [("muxboard.attach.start", _FIELDS)]
    finally:
        ws.push(ConnectionError("done"))
        _join(t)
    assert command.calls == 1
    assert audit.events == [("muxboard.attach.start", _FIELDS), ("muxboard.attach.end", _FIELDS)]
    assert _errors(ws) == []
    _assert_slot_free(runner)


def test_runner_at_cap_refuses_with_4029_before_building_the_command():
    audit = RecordingAudit()
    runner = _runner(audit)
    holder = FakeWebSocket()
    t = threading.Thread(
        target=runner.run, args=(holder,),
        kwargs={**_FIELDS, "attach_command": AttachCommand(argv=_ECHO_LINES)}, daemon=True,
    )
    t.start()
    try:
        holder.push_json({"type": "input", "data": "up\n"})
        holder.wait_for("got:up")
        command = AttachCommand()
        ws = FakeWebSocket()
        _run(runner, ws, command)
        assert command.calls == 0
        assert len(_errors(ws)) == 1 and "max 1 per user" in _errors(ws)[0]
        assert [code for code, _ in ws.closed] == [4029]
        assert audit.names() == ["muxboard.attach.start"]
    finally:
        holder.push(ConnectionError("done"))
        _join(t)
    _assert_slot_free(runner)


def test_runner_command_failure_closes_plainly_with_its_text_and_frees_the_slot():
    audit = RecordingAudit()
    runner = _runner(audit)
    ws = FakeWebSocket()
    _run(runner, ws, AttachCommand(raises=ValueError("no such session: work")))
    assert _errors(ws) == ["no such session: work"]
    assert ws.closed == [(None, None)]
    assert audit.events == []
    _assert_slot_free(runner)


def test_runner_start_audit_failure_blocks_the_bridge_and_frees_the_slot(tmp_path):
    marker = tmp_path / "ran"
    audit = RecordingAudit("muxboard.attach.start")
    runner = _runner(audit)
    ws = FakeWebSocket()
    _run(runner, ws, AttachCommand(argv=["sh", "-c", f'touch "{marker}"']))
    assert not marker.exists()
    assert len(_errors(ws)) == 1
    assert ws.closed == [(None, None)]
    assert audit.names() == ["muxboard.attach.start"]
    audit.raise_on.clear()
    _assert_slot_free(runner)


def test_runner_bridge_failure_sends_error_frame_emits_end_and_frees_the_slot():
    audit = RecordingAudit()
    runner = _runner(audit)
    ws = FakeWebSocket()
    _run(runner, ws, AttachCommand(argv=["muxboard-no-such-command-29"]))
    assert _errors(ws) == ["attach failed"]
    assert ws.closed == [(None, None)]
    assert audit.events == [("muxboard.attach.start", _FIELDS), ("muxboard.attach.end", _FIELDS)]
    _assert_slot_free(runner)


def test_runner_end_audit_failure_is_swallowed_and_the_slot_is_free():
    audit = RecordingAudit("muxboard.attach.end")
    runner = _runner(audit)
    ws = FakeWebSocket()
    _run(runner, ws, AttachCommand())
    assert audit.names() == ["muxboard.attach.start", "muxboard.attach.end"]
    assert _errors(ws) == []
    _assert_slot_free(runner)
