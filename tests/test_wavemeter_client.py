"""End-to-end test of WavemeterClient against the real wmServer.

Spins up wmServer.SocketServer on a free localhost port (no Bristol or
mcculw hardware needed — the multiplexer is not started). Drives the
client through GET / SET / PID / STATUS and verifies cm^-1 ↔ nm
conversion at the boundary.
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
from src.devices.wavemeter_client import WavemeterClient, WavemeterClientError
from src.utils.units import nm_vacuum_to_wn, wn_to_nm_vacuum


def _free_port() -> int:
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
    yield state, port
    state.running = False
    srv.close()
    t.join(timeout=2.0)


def test_get_wavenumber_returns_cm_inverse(server):
    """Server speaks nm. Client must convert to cm^-1."""
    state, port = server
    nm_reading = 791.96
    state.wavePorts[0].latest_reading = nm_reading
    client = WavemeterClient("127.0.0.1", port, channel=1)
    try:
        wn = client.get_wavenumber()
        assert wn == pytest.approx(nm_vacuum_to_wn(nm_reading), rel=1e-9)
    finally:
        client.close()


def test_get_wavenumber_returns_zero_when_channel_inactive(server):
    """Channel 2 isn't in the active_read default set — GET won't carry it."""
    _state, port = server
    client = WavemeterClient("127.0.0.1", port, channel=2)
    try:
        assert client.get_wavenumber() == 0.0
    finally:
        client.close()


def test_set_setpoint_wn_converts_to_nm(server):
    """Setpoint pushed as wavenumber should land in the server as nm."""
    state, port = server
    target_wn = 12625.0
    client = WavemeterClient("127.0.0.1", port, channel=1)
    try:
        client.set_setpoint_wn(target_wn)
        assert state.wavePorts[0].pid.setpoint == pytest.approx(
            wn_to_nm_vacuum(target_wn), rel=1e-9
        )
    finally:
        client.close()


def test_set_pid_params_propagate(server):
    state, port = server
    client = WavemeterClient("127.0.0.1", port, channel=1)
    try:
        client.set_pid_param("kp", 2.0)
        client.set_pid_param("ki", 0.5)
        client.set_pid_param("kd", 0.01)
        client.set_pid_param("gain", 8.0)
        client.set_pid_param("offset", 1.0)
        wp = state.wavePorts[0]
        assert wp.pid.kp == pytest.approx(2.0)
        assert wp.pid.ki == pytest.approx(0.5)
        assert wp.pid.kd == pytest.approx(0.01)
        assert wp.gain == pytest.approx(8.0)
        assert wp.offset == pytest.approx(1.0)
    finally:
        client.close()


def test_enable_pid_roundtrip(server):
    state, port = server
    client = WavemeterClient("127.0.0.1", port, channel=1)
    try:
        client.enable_pid()
        assert state.wavePorts[0].active_pid is True
        client.disable_pid()
        assert state.wavePorts[0].active_pid is False
    finally:
        client.close()


def test_enable_pid_without_read_raises(server):
    """Channel 2 has active_read=False by default; PID_ON must fail."""
    _state, port = server
    client = WavemeterClient("127.0.0.1", port, channel=2)
    try:
        with pytest.raises(WavemeterClientError):
            client.enable_pid()
    finally:
        client.close()


def test_enable_pid_after_enable_read(server):
    state, port = server
    client = WavemeterClient("127.0.0.1", port, channel=2)
    try:
        client.enable_read()
        client.enable_pid()
        assert state.wavePorts[1].active_read is True
        assert state.wavePorts[1].active_pid is True
    finally:
        client.close()


def test_set_unknown_key_raises(server):
    _state, port = server
    client = WavemeterClient("127.0.0.1", port, channel=1)
    try:
        with pytest.raises(WavemeterClientError):
            client.set_pid_param("frobnicator", 1.0)
    finally:
        client.close()


def test_get_status_returns_dict(server):
    state, port = server
    state.wavePorts[0].pid.kp = 3.0
    state.wavePorts[0].pid.setpoint = 400.0
    client = WavemeterClient("127.0.0.1", port, channel=1)
    try:
        st = client.get_status()
        assert st["kp"] == pytest.approx(3.0)
        assert st["setpoint"] == pytest.approx(400.0)
        assert st["active_read"] == pytest.approx(1.0)
    finally:
        client.close()


def test_unreachable_server_raises():
    """Hitting a closed port should surface a WavemeterClientError, not hang."""
    port = _free_port()  # nobody is listening here
    client = WavemeterClient("127.0.0.1", port, channel=1, timeout=0.3)
    with pytest.raises(WavemeterClientError):
        client.set_pid_param("kp", 1.0)


def test_reconnect_after_server_drop(server):
    """If the server closes our socket between commands, the next call must reconnect."""
    state, port = server
    client = WavemeterClient("127.0.0.1", port, channel=1, timeout=1.0)
    try:
        client.set_pid_param("kp", 1.0)
        # Force-close the client's socket from underneath it (simulates a
        # server-side disconnect, network glitch, etc).
        client._close_locked()
        # The next call should reconnect transparently.
        client.set_pid_param("kp", 2.0)
        assert state.wavePorts[0].pid.kp == pytest.approx(2.0)
    finally:
        client.close()
