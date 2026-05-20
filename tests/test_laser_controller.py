"""Unit tests for the new LaserController against a FakeWavemeterClient.

The controller is now a thin adapter over WavemeterClient: it forwards
setpoints (converting cm⁻¹ → nm at the client boundary), pushes PID
parameter changes, polls the wavemeter to declare lock, and toggles
server-side PID on start/stop. These tests drive the FakeClient and
inspect the call log.
"""

import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.control.laser_controller import LaserController
from src.utils.units import wn_to_nm_vacuum, nm_vacuum_to_wn


FAST_CONFIG = {
    "tolerance_wn": 1e-3,        # generous; we'll feed in-tolerance reads
    "poll_interval": 0.005,
    "required_stable_samples": 3,
    "continuous": False,
    "wm_averaging_samples": 1,
}


class FakeWavemeterClient:
    """Records every call. Wavemeter reading is governed by `reader`, which
    may be a fixed value or a function of the call count (in **cm⁻¹**)."""

    def __init__(self, reader_wn=12625.0):
        self.calls = []
        self.reader = reader_wn
        self.reads = 0
        self.setpoint_nm = None
        self.pid_params = {}
        self.pid_on = False
        self.read_on = False
        self._lock = threading.Lock()

    def _record(self, name, *args, **kwargs):
        with self._lock:
            self.calls.append((name, args, kwargs))

    def names(self):
        return [c[0] for c in self.calls]

    def get_wavenumber(self):
        self.reads += 1
        self._record("get_wavenumber")
        if callable(self.reader):
            return float(self.reader(self.reads))
        return float(self.reader)

    def set_setpoint_wn(self, target_wn):
        self._record("set_setpoint_wn", float(target_wn))
        self.setpoint_nm = wn_to_nm_vacuum(float(target_wn))

    def set_pid_param(self, key, value, channel=None):
        self._record("set_pid_param", key, float(value), channel)
        self.pid_params[key] = float(value)

    def enable_pid(self, channel=None):
        self._record("enable_pid", channel)
        self.pid_on = True

    def disable_pid(self, channel=None):
        self._record("disable_pid", channel)
        self.pid_on = False

    def enable_read(self, channel=None):
        self._record("enable_read", channel)
        self.read_on = True

    def disable_read(self, channel=None):
        self._record("disable_read", channel)
        self.read_on = False

    def get_status(self, channel=None):
        self._record("get_status", channel)
        return {}

    def close(self):
        self._record("close")


def _wait_for(predicate, timeout=2.0, interval=0.005):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _make_controller(client, **overrides):
    cfg = dict(FAST_CONFIG)
    cfg.update(overrides)
    return LaserController(client, channel=1, config=cfg)


# ---- Tests ----


def test_set_wavenumber_pushes_setpoint_in_nm():
    client = FakeWavemeterClient(reader_wn=12625.0)
    ctrl = _make_controller(client)
    target = 12625.0
    ctrl.set_wavenumber(target)
    assert _wait_for(lambda: ctrl.is_locked)
    ctrl.stop()

    # The client received set_setpoint_wn(target). The client itself does
    # the nm conversion — but we verify the cm⁻¹ target propagated.
    setpoint_calls = [c for c in client.calls if c[0] == "set_setpoint_wn"]
    assert setpoint_calls, "set_setpoint_wn never called"
    assert setpoint_calls[0][1][0] == pytest.approx(target)
    # And the simulated nm we landed at matches the conversion.
    assert client.setpoint_nm == pytest.approx(wn_to_nm_vacuum(target), rel=1e-12)


def test_is_stable_requires_n_consecutive_in_tolerance():
    """is_locked must not flip True until required_stable_samples have all
    been in-tolerance."""
    client = FakeWavemeterClient(reader_wn=12625.0)
    ctrl = _make_controller(client, required_stable_samples=4, poll_interval=0.01)
    ctrl.set_wavenumber(12625.0)
    assert _wait_for(lambda: ctrl.is_locked, timeout=2.0)
    # Each averaged read is one get_wavenumber call; the loop needed at least
    # 4 in-tolerance reads. (Extra reads come from is_stable polls.)
    assert client.reads >= 4
    ctrl.stop()


def test_is_stable_false_when_out_of_tolerance():
    """A drift larger than tolerance must take is_stable back to False."""
    client = FakeWavemeterClient(reader_wn=12625.0)
    ctrl = _make_controller(client, tolerance_wn=0.001, required_stable_samples=2,
                            poll_interval=0.01, continuous=True)
    ctrl.set_wavenumber(12625.0)
    assert _wait_for(lambda: ctrl.is_locked, timeout=2.0)
    # Now drift the reading hard.
    client.reader = 12625.5
    assert _wait_for(lambda: not ctrl.is_stable(), timeout=2.0)
    ctrl.stop()


def test_update_pid_config_pushes_each_key():
    client = FakeWavemeterClient(reader_wn=12625.0)
    ctrl = _make_controller(client)
    ctrl.update_pid_config({"kp": 2.0, "ki": 0.5, "kd": 0.01,
                            "vLow": -3.0, "vHigh": 3.0,
                            "gain": 8.0, "offset": 1.5,
                            "irrelevant_key": 42})
    keys_pushed = [args[0] for name, args, _ in client.calls if name == "set_pid_param"]
    # All seven valid keys propagate; the bogus one is filtered.
    assert set(keys_pushed) == {"kp", "ki", "kd", "vLow", "vHigh", "gain", "offset"}
    assert "irrelevant_key" not in keys_pushed
    # Values landed in the fake too.
    assert client.pid_params["kp"] == pytest.approx(2.0)
    assert client.pid_params["gain"] == pytest.approx(8.0)


def test_initial_pid_params_applied_from_config():
    """PID keys present in the constructor config should be pushed to the
    server once at startup — settings.json is the source of truth."""
    client = FakeWavemeterClient(reader_wn=12625.0)
    cfg = dict(FAST_CONFIG)
    cfg.update({"kp": 4.0, "ki": 0.2})
    ctrl = LaserController(client, channel=1, config=cfg)
    keys = [args[0] for name, args, _ in client.calls if name == "set_pid_param"]
    assert "kp" in keys and "ki" in keys
    assert client.pid_params["kp"] == pytest.approx(4.0)
    ctrl.stop()


def test_start_lock_enables_read_and_pid():
    client = FakeWavemeterClient(reader_wn=12625.0)
    ctrl = _make_controller(client)
    ctrl.start_lock(12625.0)
    assert _wait_for(lambda: ctrl.is_locked, timeout=2.0)
    assert client.read_on is True
    assert client.pid_on is True
    ctrl.stop_lock()
    assert client.pid_on is False


def test_stop_lock_clears_continuous():
    client = FakeWavemeterClient(reader_wn=12625.0)
    ctrl = _make_controller(client)
    ctrl.start_lock(12625.0)
    assert _wait_for(lambda: ctrl.is_locked, timeout=2.0)
    ctrl.stop_lock()
    # Loop should exit and continuous should be False.
    assert _wait_for(lambda: not (ctrl.control_thread and ctrl.control_thread.is_alive()),
                     timeout=2.0)
    assert ctrl.continuous is False


def test_set_wavenumber_while_locked_reaims():
    """Changing the target while already locked should drop is_locked to False
    until the new target is verified."""
    client = FakeWavemeterClient(reader_wn=12625.0)
    ctrl = _make_controller(client, continuous=True, required_stable_samples=2,
                            poll_interval=0.01)
    ctrl.set_wavenumber(12625.0)
    assert _wait_for(lambda: ctrl.is_locked, timeout=2.0)
    # Now move both target and (simulated) reading.
    client.reader = 12625.5
    ctrl.set_wavenumber(12625.5)
    # Reading matches new target → lock should re-engage.
    assert _wait_for(lambda: ctrl.is_locked, timeout=2.0)
    setpoint_calls = [c for c in client.calls if c[0] == "set_setpoint_wn"]
    assert len(setpoint_calls) == 2
    ctrl.stop()


def test_get_wavenumber_passthrough():
    client = FakeWavemeterClient(reader_wn=12625.5)
    ctrl = _make_controller(client)
    assert ctrl.get_wavenumber() == pytest.approx(12625.5)


def test_update_config_changes_tolerance_at_runtime():
    client = FakeWavemeterClient(reader_wn=12625.0)
    ctrl = _make_controller(client, tolerance_wn=0.001)
    assert ctrl.tolerance_wn == pytest.approx(0.001)
    ctrl.update_config({"tolerance_wn": 0.01, "required_stable_samples": 2})
    assert ctrl.tolerance_wn == pytest.approx(0.01)
    assert ctrl.required_stable_samples == 2
    ctrl.stop()
