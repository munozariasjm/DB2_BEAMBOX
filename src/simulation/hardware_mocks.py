"""Simulation stand-in for the wavemeter server.

`MockWavemeterClient` mirrors the public surface of
`src/devices/wavemeter_client.py:WavemeterClient` so `DAQSystem` can swap
either one in without code path changes. Everything is in-process — no
real socket, no real Bristol — but the slew dynamics are good enough for
the lock logic to exercise its in-tolerance / N-sample gate.

When PID is enabled, `latest_reading` slews toward `setpoint` at a fixed
nm/s rate. When disabled, the reading drifts slowly with noise so a
no-lock scenario looks realistic. All times in seconds, wavelengths in nm.
"""

import random
import threading
import time

from src.utils.units import nm_vacuum_to_wn


class MockWavemeterClient:
    def __init__(
        self,
        host: str = "mock",
        port: int = 0,
        channel: int = 1,
        timeout: float = 2.0,
        slew_rate_nm_per_s: float = 0.05,
        noise_nm: float = 1e-7,
        initial_nm: float = 791.96,
    ):
        self.host = host
        self.port = int(port)
        self.channel = int(channel)
        self.timeout = float(timeout)
        self._slew_rate = float(slew_rate_nm_per_s)
        self._noise = float(noise_nm)
        self._latest_nm = float(initial_nm)
        self._last_tick = time.time()
        self._setpoint_nm = float(initial_nm)
        self._active_pid = False
        self._active_read = True
        self._pid_params = {"kp": 1.0, "ki": 0.0, "kd": 0.0,
                            "vLow": -5.0, "vHigh": 5.0,
                            "gain": 10.0, "offset": 0.0}
        self._lock = threading.Lock()

    # ---- Mirror WavemeterClient surface ----

    def get_wavenumber(self) -> float:
        nm = self.get_reading_nm()
        if nm <= 0:
            return 0.0
        return nm_vacuum_to_wn(nm)

    def get_reading_nm(self, channel=None) -> float:
        with self._lock:
            self._advance_locked()
            return self._latest_nm + random.uniform(-self._noise, self._noise)

    def set_setpoint_wn(self, target_wn: float) -> None:
        from src.utils.units import wn_to_nm_vacuum
        self.set_pid_param("setpoint", wn_to_nm_vacuum(float(target_wn)))

    def set_pid_param(self, key: str, value: float, channel=None) -> None:
        with self._lock:
            self._advance_locked()
            if key == "setpoint":
                self._setpoint_nm = float(value)
            elif key in self._pid_params:
                self._pid_params[key] = float(value)
            else:
                raise ValueError(f"unknown PID key {key}")

    def enable_pid(self, channel=None) -> None:
        with self._lock:
            if not self._active_read:
                raise RuntimeError("read must be enabled before PID")
            self._active_pid = True

    def disable_pid(self, channel=None) -> None:
        with self._lock:
            self._active_pid = False

    def enable_read(self, channel=None) -> None:
        with self._lock:
            self._active_read = True

    def disable_read(self, channel=None) -> None:
        with self._lock:
            self._active_read = False
            self._active_pid = False

    def get_status(self, channel=None) -> dict:
        with self._lock:
            self._advance_locked()
            return {
                "kp": self._pid_params["kp"],
                "ki": self._pid_params["ki"],
                "kd": self._pid_params["kd"],
                "setpoint": self._setpoint_nm,
                "vLow": self._pid_params["vLow"],
                "vHigh": self._pid_params["vHigh"],
                "gain": self._pid_params["gain"],
                "offset": self._pid_params["offset"],
                "active_pid": float(self._active_pid),
                "active_read": float(self._active_read),
                "latest_reading": self._latest_nm,
                "latest_error": self._latest_nm - self._setpoint_nm,
                "latest_output": 0.0,
            }

    def close(self) -> None:
        pass

    # ---- Sim physics ----

    def _advance_locked(self):
        now = time.time()
        dt = max(0.0, now - self._last_tick)
        self._last_tick = now
        if not self._active_pid:
            return
        diff = self._setpoint_nm - self._latest_nm
        step = self._slew_rate * dt
        if abs(diff) <= step:
            self._latest_nm = self._setpoint_nm
        else:
            self._latest_nm += step if diff > 0 else -step
