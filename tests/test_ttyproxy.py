"""The Attach bridge, driven through its interface.

Each bridge test pairs a fake WebSocket with a real PTY running a real local
command, and runs the bridge in a thread with a bounded join so a hang fails
the test instead of stalling the suite.
"""

import json
import os
import queue
import signal
import threading
import time

import pytest

from muxboard.ttyproxy import AttachCapacityExceeded, SlotManager, bridge, error_close

_JOIN_SECONDS = 10

# Echoes each line it reads back with a prefix, so input is observable.
_ECHO = ["sh", "-c", 'while read l; do echo "got:$l"; done']
# Prints the PTY size for each line it reads.
_STTY = ["sh", "-c", "while read l; do stty size; done"]


class FakeWebSocket:
    """``receive(timeout)`` reads from a queue; ``send`` records the frame.

    Queue an exception instance to have ``receive`` raise it.
    """

    def __init__(self, *, send_raises: bool = False) -> None:
        self.inbox: queue.Queue = queue.Queue()
        self.sent: list = []
        self.closed: list = []
        self.send_raises = send_raises
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
        self.closed.append((reason, message))

    def output(self) -> str:
        with self._lock:
            frames = list(self.sent)
        return b"".join(frames).decode("utf-8", errors="replace")

    def wait_for(self, text: str, start: int = 0, timeout: float = 5.0) -> int:
        """Wait until ``text`` appears in the output after ``start``; return the end offset."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            idx = self.output().find(text, start)
            if idx >= 0:
                return idx + len(text)
            time.sleep(0.02)
        raise AssertionError(f"{text!r} never arrived; output was {self.output()[start:]!r}")


def _start(ws, argv, **kwargs) -> threading.Thread:
    t = threading.Thread(target=bridge, args=(ws, argv, {}), kwargs=kwargs, daemon=True)
    t.start()
    return t


def _join(t: threading.Thread) -> None:
    t.join(timeout=_JOIN_SECONDS)
    assert not t.is_alive(), "bridge did not return"


def _alive(pid: int) -> bool:
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


def _wait_dead(pid: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _alive(pid):
            return
        time.sleep(0.05)
    os.kill(pid, signal.SIGKILL)
    raise AssertionError(f"pid {pid} outlived the bridge")


def _read_pid(path, timeout: float = 5.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            text = path.read_text().strip()
        except FileNotFoundError:
            text = ""
        if text:
            return int(text)
        time.sleep(0.02)
    raise AssertionError("grandchild pid never written")


def _grandchild_argv(pid_file) -> list:
    """A shell that backgrounds a long ``sleep`` (our grandchild) and records its pid."""
    return ["sh", "-c", f'sleep 300 & echo $! > "{pid_file}"; echo ready; wait']


# ---------- frames ----------


def test_input_reaches_process_and_output_returns_as_bytes():
    ws = FakeWebSocket()
    t = _start(ws, _ECHO)
    try:
        ws.push_json({"type": "input", "data": "hello\n"})
        ws.wait_for("got:hello")
        assert ws.sent and all(isinstance(f, bytes) for f in ws.sent)
    finally:
        ws.push(ConnectionError("done"))
        _join(t)


def test_raw_text_and_binary_frames_are_written_through_as_input():
    ws = FakeWebSocket()
    t = _start(ws, _ECHO)
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
    t = _start(ws, _ECHO)
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
    t = _start(ws, _STTY)
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
    t = _start(ws, _ECHO)
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


def test_receive_failure_kills_whole_process_group(tmp_path):
    pid_file = tmp_path / "pid"
    ws = FakeWebSocket()
    t = _start(ws, _grandchild_argv(pid_file))
    grandchild = _read_pid(pid_file)
    assert _alive(grandchild)
    ws.push(ConnectionError("browser has gone"))
    _join(t)
    _wait_dead(grandchild)


def test_send_failure_tears_process_down(tmp_path):
    pid_file = tmp_path / "pid"
    ws = FakeWebSocket(send_raises=True)
    t = _start(ws, _grandchild_argv(pid_file))
    grandchild = _read_pid(pid_file)
    _join(t)
    _wait_dead(grandchild)


def test_lifetime_cap_tears_process_down(tmp_path):
    pid_file = tmp_path / "pid"
    ws = FakeWebSocket()
    t = _start(ws, _grandchild_argv(pid_file), max_lifetime=0.5)
    grandchild = _read_pid(pid_file)
    _join(t)
    _wait_dead(grandchild)


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
