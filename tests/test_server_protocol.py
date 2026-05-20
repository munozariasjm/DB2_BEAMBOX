"""Wire-protocol tests for LASERLABCOMPUTER/wmServer.py.

The server's hardware dependencies (Bristol SCPI wavemeter, mcculw analog
out) are import-guarded in wmServer.py so the module imports cleanly on
dev machines. These tests drive only the `SocketServer` half via a real
localhost socket; the `WavemeterMultiplexer` is never started, so we
manipulate `AppState.wavePorts[ch].latest_reading` directly to simulate
wavemeter readings for the GET reply.

Channels are **1-indexed externally** across the whole protocol (GET/SET/
PID_ON/STATUS); the server translates to its 0-indexed wavePorts dict
internally. So `SET 1 ...` mutates `wavePorts[0]`, `STATUS 2` reads
`wavePorts[1]`, etc.
"""

import os
import socket
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "LASERLABCOMPUTER")))

import wmServer


def _free_port() -> int:
    """Grab an ephemeral port the OS guarantees no one else is on."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server():
    state = wmServer.AppState()
    state.running = True
    port = _free_port()
    srv = wmServer.SocketServer(state, host="127.0.0.1", port=port)
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.02)
    else:
        raise RuntimeError("server did not come up")
    yield state, port
    state.running = False
    srv.close()
    t.join(timeout=2.0)


def _query(port: int, command: str) -> str:
    """Open a fresh socket, send one newline-terminated command, return reply line."""
    with socket.create_connection(("127.0.0.1", port), timeout=2.0) as s:
        s.sendall(command.encode() + b"\n")
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        return buf.split(b"\n", 1)[0].decode()


def test_get_returns_active_channel_reading(server):
    state, port = server
    # Internal wavePorts[0] starts active_read=True. Seed a known reading.
    state.wavePorts[0].latest_reading = 398.91111
    reply = _query(port, "GET")
    # Format: "time:t,1:398.91111"  — externally 1-indexed.
    assert "time:" in reply
    assert "1:398.91111" in reply


def test_set_setpoint_updates_state(server):
    state, port = server
    assert _query(port, "SET 1 setpoint=399.5") == "OK"
    assert state.wavePorts[0].pid.setpoint == pytest.approx(399.5)


def test_set_kp_updates_pid(server):
    state, port = server
    assert _query(port, "SET 1 kp=2.5") == "OK"
    assert state.wavePorts[0].pid.kp == pytest.approx(2.5)


def test_set_rejects_unknown_key(server):
    _state, port = server
    reply = _query(port, "SET 1 not_a_key=1.0")
    assert reply.startswith("ERR")
    assert "not_a_key" in reply


def test_set_rejects_unknown_channel(server):
    _state, port = server
    reply = _query(port, "SET 99 kp=1.0")
    assert reply.startswith("ERR")
    assert "99" in reply


def test_set_rejects_zero_channel(server):
    """Channels are 1-indexed externally; `0` is below the floor."""
    _state, port = server
    reply = _query(port, "SET 0 kp=1.0")
    assert reply.startswith("ERR")


def test_set_rejects_non_numeric_value(server):
    _state, port = server
    reply = _query(port, "SET 1 kp=notafloat")
    assert reply.startswith("ERR")


def test_pid_on_requires_read_first(server):
    state, port = server
    # External channel 2 = internal wavePorts[1], starts active_read=False.
    assert state.wavePorts[1].active_read is False
    reply = _query(port, "PID_ON 2")
    assert reply.startswith("ERR")
    assert state.wavePorts[1].active_pid is False


def test_pid_on_after_read_on(server):
    state, port = server
    assert _query(port, "READ_ON 2") == "OK"
    assert state.wavePorts[1].active_read is True
    assert _query(port, "PID_ON 2") == "OK"
    assert state.wavePorts[1].active_pid is True


def test_pid_off(server):
    state, port = server
    _query(port, "READ_ON 2")
    _query(port, "PID_ON 2")
    assert state.wavePorts[1].active_pid is True
    assert _query(port, "PID_OFF 2") == "OK"
    assert state.wavePorts[1].active_pid is False


def test_read_off_also_disables_pid(server):
    state, port = server
    _query(port, "READ_ON 2")
    _query(port, "PID_ON 2")
    assert state.wavePorts[1].active_pid is True
    assert _query(port, "READ_OFF 2") == "OK"
    assert state.wavePorts[1].active_pid is False
    assert state.wavePorts[1].active_read is False


def test_status_returns_pid_params(server):
    state, port = server
    state.wavePorts[0].pid.kp = 1.5
    state.wavePorts[0].pid.setpoint = 398.0
    reply = _query(port, "STATUS 1")
    # Reply is `kp:1.5,ki:..,kd:..,setpoint:398.0,...`
    fields = dict(kv.split(":", 1) for kv in reply.split(","))
    assert float(fields["kp"]) == pytest.approx(1.5)
    assert float(fields["setpoint"]) == pytest.approx(398.0)
    assert int(fields["active_read"]) == 1


def test_status_unknown_channel(server):
    _state, port = server
    reply = _query(port, "STATUS 99")
    assert reply.startswith("ERR")


def test_unknown_command(server):
    _state, port = server
    reply = _query(port, "MAKE_COFFEE")
    assert reply.startswith("ERR")


def test_pipelined_commands_on_one_socket(server):
    """Several commands in one connection — the server must dispatch each."""
    state, port = server
    with socket.create_connection(("127.0.0.1", port), timeout=2.0) as s:
        s.sendall(b"SET 1 kp=3.0\nSET 1 ki=0.1\nSTATUS 1\n")
        buf = b""
        # Wait for three newline-terminated replies.
        while buf.count(b"\n") < 3:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
    lines = buf.decode().split("\n")
    assert lines[0] == "OK"
    assert lines[1] == "OK"
    assert "kp:3.0" in lines[2]
    assert state.wavePorts[0].pid.kp == pytest.approx(3.0)
    assert state.wavePorts[0].pid.ki == pytest.approx(0.1)


def test_legacy_get_without_newline(server):
    """The collaborators' existing client_class.py sends bare `GET` (no \\n).
    We must still respond to that form."""
    state, port = server
    state.wavePorts[0].latest_reading = 400.0
    with socket.create_connection(("127.0.0.1", port), timeout=2.0) as s:
        s.sendall(b"GET")
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
    line = buf.split(b"\n", 1)[0].decode()
    assert "time:" in line
    assert "1:400.0" in line
