"""Laser stabilisation by sequencing the wavemeter server's PID.

The DAQ does not run a software PID on the laser. The wavemeter server
runs the PID per channel (kp/ki/kd, plus voltage clamping via vLow/vHigh/
gain/offset). This controller is a thin adapter:

    Scanner.set_wavenumber(wn)  →  client.set_setpoint_wn(wn)
    Scanner.is_stable()         →  client.get_wavenumber()  vs  target
                                   (after `required_stable_samples` consecutive
                                   in-tolerance polls, `is_locked := True`)
    GUI.update_pid_config(...)  →  client.set_pid_param(...) per key

The cm⁻¹ ↔ nm conversion happens inside `WavemeterClient.set_setpoint_wn`
and `WavemeterClient.get_wavenumber`, so this layer stays in cm⁻¹
throughout — what the Scanner already speaks.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Optional


# Keys the GUI is allowed to push through `update_pid_config`. Mapped 1:1
# to the wavemeter server's WavePort.updateParams allow-list.
_PID_KEYS = ("kp", "ki", "kd", "vLow", "vHigh", "gain", "offset")


class LaserController:
    def __init__(self, wavemeter_client, channel: int = 1, config: Optional[dict] = None):
        self.client = wavemeter_client
        self.channel = int(channel)
        self.config = dict(config or {})

        # Lock-judgment parameters (client-side; not pushed to server).
        # `tolerance_wn` is in cm⁻¹ since that's what the Scanner sweeps in.
        self.tolerance_wn = float(self.config.get("tolerance_wn", 1e-5))
        self.poll_interval = float(self.config.get("poll_interval", 0.1))
        self.required_stable_samples = int(self.config.get("required_stable_samples", 4))
        self.continuous = bool(self.config.get("continuous", False))
        self.wm_avg_samples = max(1, int(self.config.get("wm_averaging_samples", 5)))

        # State
        self._wm_buffer = deque(maxlen=self.wm_avg_samples)
        self.target_wn = 0.0
        self.is_locked = False

        # Threading
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.control_thread: Optional[threading.Thread] = None
        # Cleared by the loop whenever it picks up a new target.
        self._target_changed = threading.Event()

        # Push initial PID params from config if any are present so a fresh
        # session lands the server in a known state.
        self._apply_initial_pid_params()

    # ---- Public API used by GUI / DAQSystem / Scanner ----

    def set_wavenumber(self, target_wn: float):
        """Set the lock target (cm⁻¹) and start (or wake) the polling thread."""
        target_wn = float(target_wn)
        with self.lock:
            self.target_wn = target_wn
            print(f"[Laser] set_wavenumber({target_wn:.6f})")
        try:
            self.client.set_setpoint_wn(target_wn)
        except Exception as e:
            print(f"[Laser] set_setpoint_wn failed: {e}")
            return
        with self.lock:
            if self.control_thread and self.control_thread.is_alive():
                self._target_changed.set()
                return
            self.stop_event.clear()
            self._wm_buffer.clear()
            self.is_locked = False
            self.control_thread = threading.Thread(
                target=self._control_loop, daemon=True
            )
            self.control_thread.start()

    def start_lock(self, target_wn: float):
        """Engage and hold a wavenumber indefinitely by turning on read and PID
        on the server, then driving the setpoint."""
        with self.lock:
            self.continuous = True
        try:
            self.client.enable_read(self.channel)
            self.client.enable_pid(self.channel)
        except Exception as e:
            print(f"[Laser] enable_pid failed: {e}")
        self.set_wavenumber(target_wn)

    def stop_lock(self):
        with self.lock:
            self.continuous = False
        try:
            self.client.disable_pid(self.channel)
        except Exception as e:
            print(f"[Laser] disable_pid failed: {e}")
        self.stop()

    def is_stable(self, tolerance: Optional[float] = None) -> bool:
        """True when the most recent wavemeter read is within tolerance of
        target AND the verify loop has cleared `required_stable_samples`."""
        tol = self.tolerance_wn if tolerance is None else float(tolerance)
        try:
            wn = self.client.get_wavenumber()
        except Exception as e:
            print(f"[Laser] get_wavenumber failed: {e}")
            return False
        with self.lock:
            target = self.target_wn
            locked = self.is_locked
        return locked and abs(wn - target) < tol

    def get_wavenumber(self) -> float:
        """Single raw wavemeter read (cm⁻¹) on the configured channel."""
        try:
            return float(self.client.get_wavenumber())
        except Exception as e:
            print(f"[Laser] get_wavenumber error: {e}")
            return 0.0

    def stop(self):
        self.stop_event.set()
        self._target_changed.set()  # wake any pause inside the loop
        thread = self.control_thread
        if thread and thread.is_alive():
            thread.join(timeout=max(2.0, self.poll_interval * 20))
        with self.lock:
            self.is_locked = False

    def update_config(self, new_config: dict):
        """Update client-side lock-judgment parameters at runtime."""
        with self.lock:
            self.config.update(new_config)
            self.tolerance_wn = float(self.config.get("tolerance_wn", self.tolerance_wn))
            self.poll_interval = float(self.config.get("poll_interval", self.poll_interval))
            self.required_stable_samples = int(
                self.config.get("required_stable_samples", self.required_stable_samples)
            )
            self.continuous = bool(self.config.get("continuous", self.continuous))
            new_avg = max(1, int(self.config.get("wm_averaging_samples", self.wm_avg_samples)))
            if new_avg != self.wm_avg_samples:
                self.wm_avg_samples = new_avg
                self._wm_buffer = deque(maxlen=new_avg)
        print(
            f"[Laser] config updated: tol_wn={self.tolerance_wn}, "
            f"poll={self.poll_interval}, stable={self.required_stable_samples}, "
            f"cont={self.continuous}"
        )

    def update_pid_config(self, pid_config: dict):
        """Push PID parameters (kp/ki/kd/vLow/vHigh/gain/offset) to the server.
        Unknown keys are dropped silently — the GUI may pass through dict
        entries that aren't PID-related. All known keys are sent in a SINGLE
        SET so the server's recv loop sees one JSON object, not N
        back-to-back ones (the new server's per-recv json.loads otherwise
        chokes and drops the connection)."""
        batch = {k: float(v) for k, v in pid_config.items() if k in _PID_KEYS}
        if not batch:
            return
        if hasattr(self.client, "set_params"):
            try:
                self.client.set_params(batch, channel=self.channel)
            except Exception as e:
                print(f"[Laser] failed to push PID batch: {e}")
            return
        # Fallback for clients without the batch API (the mock and the null
        # stub mirror set_pid_param but not set_params).
        for key, value in batch.items():
            try:
                self.client.set_pid_param(key, value, channel=self.channel)
            except Exception as e:
                print(f"[Laser] failed to push {key}={value}: {e}")

    # ---- Internal helpers ----

    def _apply_initial_pid_params(self):
        """If the constructor's config includes any PID keys, push them once at
        startup so the server matches our settings.json."""
        pid_block = {k: self.config[k] for k in _PID_KEYS if k in self.config}
        if not pid_block:
            return
        self.update_pid_config(pid_block)

    def _averaged_wavemeter(self) -> float:
        try:
            reading = self.client.get_wavenumber()
        except Exception as e:
            print(f"[Laser] _averaged_wavemeter: {e}")
            return 0.0
        self._wm_buffer.append(reading)
        return sum(self._wm_buffer) / len(self._wm_buffer)

    def _sleep(self, seconds: float) -> bool:
        """Sleep, return early if stop_event fires. Returns True on stop."""
        if seconds <= 0:
            return self.stop_event.is_set()
        return self.stop_event.wait(seconds)

    # ---- Main verification loop ----

    def _control_loop(self):
        print(f"[Laser] control loop starting (target={self.target_wn:.6f})")
        while not self.stop_event.is_set():
            self._target_changed.clear()
            with self.lock:
                self.is_locked = False
                self._wm_buffer.clear()

            stable_samples = 0
            locked_once = False
            while not self.stop_event.is_set():
                if self._target_changed.is_set():
                    break  # outer loop re-aims on the new target
                wn = self._averaged_wavemeter()
                with self.lock:
                    target = self.target_wn
                if abs(wn - target) < self.tolerance_wn:
                    stable_samples += 1
                    if stable_samples >= self.required_stable_samples:
                        with self.lock:
                            self.is_locked = True
                        locked_once = True
                        if not self.continuous:
                            break
                        if self._sleep(self.poll_interval):
                            break
                        continue
                else:
                    stable_samples = 0
                    if locked_once:
                        with self.lock:
                            self.is_locked = False
                if self._sleep(self.poll_interval):
                    break

            if self._target_changed.is_set():
                continue
            break

        print(f"[Laser] control loop exiting (locked={self.is_locked})")
