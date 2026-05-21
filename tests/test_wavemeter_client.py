"""End-to-end tests for WavemeterClient against a fake JSON wmServer.

The real LASERLABCOMPUTER/new_wmServer.py needs Bristol hardware drivers
to import, so we run an in-process fake server here that speaks just the
JSON wire format the client needs (GET / SET, newline-terminated). This
exercises:

- 1-indexed client channel → 0-indexed server channel translation
- cm⁻¹ ↔ nm conversion at the boundary (set_setpoint_wn / get_wavenumber)
- SET fire-and-forget: no reply is sent, client must not block reading one
- get_status flattening from nested telemetry+config dicts
- enable_read / enable_pid / disable_* sent as SET active_read / active_pid
- ping() succeeds when the server is alive, raises on transport error
"""

from __future__ import annotations

import json
import socket
import threading
import time

import pytest

from src.devices.wavemeter_client import WavemeterClient, WavemeterClientError
from src.utils.units import nm_vacuum_to_wn, wn_to_nm_vacuum


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeJsonServer:
    """Mimics new_wmServer.py's JSON protocol just enough to drive the client.

    State: per-channel dict of {active_read, active_pid, pid, vLow, vHigh,
    gain, offset, latest_reading, latest_error, latest_output, latest_time}.
    All channels 0-indexed (matches the real server). Sets are recorded so
    tests can assert on them.
    """

    def __init__(self, host="127.0.0.1", port=0, n_channels=8):
        self.host = host
        self.port = port or _free_port()
        self.running = False
        self.set_calls: list[dict] = []
        self.ports: dict[int, dict] = {}
        for ch in range(n_channels):
            self.ports[ch] = {
                "active_read": False,
                "active_pid": False,
                "pid": {"kp": 1.0, "ki": 0.0, "kd": 0.0,
                        "setpoint": 0.0, "integral": 0.0},
                "vLow": -5.0, "vHigh": 5.0, "gain": 10.0, "offset": 0.0,
                "last_config": time.time(),
                "latest_reading": 0.0,
                "latest_error": 0.0,
                "latest_output": 0.0,
                "latest_time": time.time(),
            }
        self._srv: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def set_reading(self, ch: int, nm: float):
        with self._lock:
            self.ports[ch]["latest_reading"] = float(nm)
            self.ports[ch]["latest_time"] = time.time()

    def total_dict(self) -> dict:
        with self._lock:
            telemetry = {
                str(ch): {
                    "latest_time": p["latest_time"],
                    "latest_reading": p["latest_reading"],
                    "latest_error": p["latest_error"],
                    "latest_output": p["latest_output"],
                } for ch, p in self.ports.items()
            }
            config = {
                str(ch): {
                    "channel": ch,
                    "active_read": p["active_read"],
                    "active_pid": p["active_pid"],
                    "pid": dict(p["pid"]),
                    "vLow": p["vLow"], "vHigh": p["vHigh"],
                    "gain": p["gain"], "offset": p["offset"],
                    "last_config": p["last_config"],
                } for ch, p in self.ports.items()
            }
            return {"telemetry": telemetry, "config": config}

    def _apply_change(self, ch: int, change: dict):
        with self._lock:
            self.set_calls.append({"channel": ch, "change": dict(change)})
            port = self.ports[ch]
            for k, v in change.items():
                if k in port["pid"]:
                    port["pid"][k] = v
                elif k in port:
                    port[k] = v
                # Unknown keys are silently ignored to mirror the real
                # server's `updateParams` raise-and-eat behavior (caller
                # gets no reply either way).
            port["last_config"] = time.time()

    def _serve(self, conn):
        buf = b""
        try:
            while self.running:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
                # The real server uses `json.loads(data.decode())` on a
                # single recv — i.e. each recv must be a full JSON object.
                # The client never coalesces multiple SETs into one recv
                # (it sends each via sendall), so this is correct.
                try:
                    msg = json.loads(buf.decode())
                    buf = b""
                except json.JSONDecodeError:
                    continue
                cmd = msg.get("cmd")
                if cmd == "GET":
                    reply = json.dumps({"type": "total", "data": self.total_dict()}) + "\n"
                    conn.sendall(reply.encode())
                elif cmd == "SET":
                    ch = int(msg["channel"])
                    self._apply_change(ch, msg.get("change") or {})
                    # No reply — matches new_wmServer.py.
                elif cmd == "CONFIG":
                    reply = json.dumps({"type": "config",
                                         "data": self.total_dict()}) + "\n"
                    conn.sendall(reply.encode())
                else:
                    # Unknown verb — the real server replies with no newline
                    # so a real client would block; we mirror that by not
                    # replying at all (tests that need unknown verbs can
                    # assert on a timeout).
                    pass
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _accept_loop(self):
        self._srv.settimeout(0.1)
        while self.running:
            try:
                conn, _addr = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            t = threading.Thread(target=self._serve, args=(conn,), daemon=True)
            t.start()

    def start(self):
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind((self.host, self.port))
        self._srv.listen(8)
        self.running = True
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()
        # Wait until the listener is actually accepting.
        for _ in range(50):
            try:
                with socket.create_connection((self.host, self.port), timeout=0.2):
                    return
            except OSError:
                time.sleep(0.02)

    def stop(self):
        self.running = False
        try:
            self._srv.close()
        except Exception:
            pass
        if self._thread:
            self._thread.join(timeout=2.0)


@pytest.fixture
def server():
    s = FakeJsonServer()
    s.start()
    yield s
    s.stop()


# ---- Channel translation ----------------------------------------------------

def test_client_channel_is_one_indexed_on_the_wire(server):
    """settings.json `channel: 1` → server wavePort 0."""
    client = WavemeterClient("127.0.0.1", server.port, channel=1)
    client.enable_read()
    client.close()

    # Give the server a moment to apply the SET (fire-and-forget; the
    # client's close races with the server's recv).
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and not server.set_calls:
        time.sleep(0.01)

    assert len(server.set_calls) == 1
    assert server.set_calls[0]["channel"] == 0
    assert server.set_calls[0]["change"] == {"active_read": True}


def test_client_channel_zero_raises(server):
    """The legacy convention starts at 1; channel=0 from settings would
    underflow the server's wavePort index."""
    client = WavemeterClient("127.0.0.1", server.port, channel=0)
    with pytest.raises(WavemeterClientError):
        client.enable_read()
    client.close()


# ---- GET / readings ---------------------------------------------------------

def test_get_wavenumber_converts_nm_to_wn(server):
    server.set_reading(0, 791.96)  # nm vacuum on wavePort 0
    client = WavemeterClient("127.0.0.1", server.port, channel=1)
    try:
        wn = client.get_wavenumber()
        assert wn == pytest.approx(nm_vacuum_to_wn(791.96), rel=1e-12)
    finally:
        client.close()


def test_get_wavenumber_returns_zero_when_no_reading(server):
    """A wavePort that's never been read reports latest_reading=0.0."""
    client = WavemeterClient("127.0.0.1", server.port, channel=1)
    try:
        assert client.get_wavenumber() == 0.0
    finally:
        client.close()


def test_get_reading_nm_specific_channel(server):
    server.set_reading(2, 760.5)
    client = WavemeterClient("127.0.0.1", server.port, channel=1)
    try:
        # channel=3 on the wire is wavePort 2
        assert client.get_reading_nm(channel=3) == pytest.approx(760.5)
    finally:
        client.close()


# ---- SET fire-and-forget ----------------------------------------------------

def test_set_setpoint_wn_converts_and_does_not_block(server):
    client = WavemeterClient("127.0.0.1", server.port, channel=1)
    try:
        # If the client erroneously waited for a reply, this would block
        # until the socket timeout (2 s default) and fail the test slowly.
        t0 = time.monotonic()
        client.set_setpoint_wn(12624.91)
        elapsed = time.monotonic() - t0
        assert elapsed < 0.5, f"set_setpoint_wn took {elapsed:.3f}s — blocking?"
    finally:
        client.close()

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and not server.set_calls:
        time.sleep(0.01)

    expected_nm = wn_to_nm_vacuum(12624.91)
    assert len(server.set_calls) == 1
    assert server.set_calls[0]["channel"] == 0
    sent = server.set_calls[0]["change"]
    assert "setpoint" in sent
    assert sent["setpoint"] == pytest.approx(expected_nm, rel=1e-12)


def test_pid_and_read_toggles_become_set_calls(server):
    client = WavemeterClient("127.0.0.1", server.port, channel=2)
    try:
        client.enable_read()
        client.enable_pid()
        client.disable_pid()
        client.disable_read()
    finally:
        client.close()

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and len(server.set_calls) < 4:
        time.sleep(0.01)

    keys_sent = [(c["channel"], next(iter(c["change"].items())))
                 for c in server.set_calls]
    assert keys_sent == [
        (1, ("active_read", True)),
        (1, ("active_pid", True)),
        (1, ("active_pid", False)),
        (1, ("active_read", False)),
    ]


def test_set_params_batches_multiple_keys_into_one_message(server):
    """The whole point of set_params is to avoid back-to-back recvs on the
    server, which would otherwise coalesce and break json.loads."""
    client = WavemeterClient("127.0.0.1", server.port, channel=1)
    try:
        client.set_params({"kp": 2.0, "ki": 0.1, "kd": 0.0, "vLow": -6.0})
    finally:
        client.close()

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and not server.set_calls:
        time.sleep(0.01)

    assert len(server.set_calls) == 1
    assert server.set_calls[0]["channel"] == 0
    assert server.set_calls[0]["change"] == {
        "kp": 2.0, "ki": 0.1, "kd": 0.0, "vLow": -6.0,
    }


def test_set_pid_param_pushes_arbitrary_keys(server):
    client = WavemeterClient("127.0.0.1", server.port, channel=1)
    try:
        client.set_pid_param("kp", 2.5)
        client.set_pid_param("vLow", -7.0)
    finally:
        client.close()

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and len(server.set_calls) < 2:
        time.sleep(0.01)

    assert server.set_calls[0]["change"] == {"kp": 2.5}
    assert server.set_calls[1]["change"] == {"vLow": -7.0}


# ---- get_status flattening --------------------------------------------------

def test_get_status_flattens_nested_telemetry_and_config(server):
    server.set_reading(0, 791.96)
    server.ports[0]["pid"]["kp"] = 3.0
    server.ports[0]["pid"]["setpoint"] = 791.96
    server.ports[0]["vLow"] = -8.0
    server.ports[0]["active_pid"] = True
    server.ports[0]["active_read"] = True
    server.ports[0]["latest_error"] = 1e-6
    server.ports[0]["latest_output"] = 0.42

    client = WavemeterClient("127.0.0.1", server.port, channel=1)
    try:
        status = client.get_status()
    finally:
        client.close()

    assert status["kp"] == pytest.approx(3.0)
    assert status["setpoint"] == pytest.approx(791.96)
    assert status["vLow"] == pytest.approx(-8.0)
    assert status["active_pid"] == 1.0
    assert status["active_read"] == 1.0
    assert status["latest_reading"] == pytest.approx(791.96)
    assert status["latest_error"] == pytest.approx(1e-6)
    assert status["latest_output"] == pytest.approx(0.42)


# ---- ping -------------------------------------------------------------------

def test_ping_succeeds_against_live_server(server):
    client = WavemeterClient("127.0.0.1", server.port, channel=1)
    try:
        client.ping()  # must not raise
    finally:
        client.close()


def test_ping_raises_against_dead_server():
    # Bind a port just to claim an unused one, then close it.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    client = WavemeterClient("127.0.0.1", port, channel=1, timeout=0.2)
    with pytest.raises(WavemeterClientError):
        client.ping()
    client.close()
