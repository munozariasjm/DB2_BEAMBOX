import sys
import os
import time
import threading
import numpy as np
from collections import deque
import csv
import json

from src.simulation.sim_tagger import MockTagger
from src.simulation.hardware_mocks import MockWavemeterClient

from src.control.laser_controller import LaserController
from src.control.data_saver import DataSaver
from src.control.scanner import Scanner

# Real Hardware Imports
from src.devices.tagger import Tagger
from src.devices.wavemeter_client import WavemeterClient


class DAQSystem:
    def __init__(self, config=None):
        self.config = config or {}
        sim_config = self.config.get("simulation_settings", {})
        hw_config = self.config.get("hardware_settings", {})
        control_config = self.config.get("control_settings", {})
        wm_block = self.config.get("wavemeter_server", {})

        # Wavemeter network + lock-judgment parameters live under
        # wavemeter_server; PID parameters under control_settings.laser_pid.
        # Merge them for the LaserController constructor.
        self.wavechannel = int(wm_block.get("channel", 1))
        laser_pid = control_config.get("laser_pid", {})
        laser_cfg = dict(laser_pid)
        laser_cfg.update({
            "tolerance_wn": wm_block.get("tolerance_wn", 1e-5),
            "poll_interval": wm_block.get("poll_interval", 0.1),
            "required_stable_samples": wm_block.get("required_stable_samples", 4),
            "continuous": laser_pid.get("continuous", False),
            "wm_averaging_samples": wm_block.get("wm_averaging_samples", 5),
        })

        # Safe-by-default: a missing `simulation_mode` key means real
        # hardware. The opposite default (True) silently puts the rig into
        # simulation on a fresh install or after a typo, which has caused
        # confusion before. Flag the absence loudly so the operator knows.
        if "simulation_mode" not in self.config:
            print("[DAQ] WARNING: 'simulation_mode' key missing from settings.json — "
                  "defaulting to REAL HARDWARE. Add the key explicitly to silence this.")
        simulation_mode = bool(self.config.get("simulation_mode", False))
        self.simulation_mode = simulation_mode
        if simulation_mode:
            # Loud terminal banner so it is impossible to miss when scrolling
            # through DAQ output. The GUI also paints a banner across the top
            # of the main window — see MainWindow.
            banner = (
                "\n"
                "╔══════════════════════════════════════════════════════════════╗\n"
                "║                                                              ║\n"
                "║   ⚠   DAQ RUNNING IN SIMULATION MODE — NO REAL HARDWARE  ⚠   ║\n"
                "║                                                              ║\n"
                "║   All tagger events, wavemeter readings, and PID dynamics    ║\n"
                "║   are synthetic. Do NOT trust recorded data as physics.      ║\n"
                "║   Set simulation_mode=false in settings.json for a real run. ║\n"
                "║                                                              ║\n"
                "╚══════════════════════════════════════════════════════════════╝\n"
            )
            print(banner)
        else:
            print("[DAQ] System Model: REAL HARDWARE")

        if simulation_mode:
            self.tagger = MockTagger(initialization_params=sim_config.get("tagger", {}))
            wm_sim = sim_config.get("wavemeter", {})
            self.wavemeter = MockWavemeterClient(
                channel=self.wavechannel,
                slew_rate_nm_per_s=float(wm_sim.get("slew_rate_nm_per_s", 0.05)),
                noise_nm=float(wm_sim.get("noise_nm", 1e-7)),
                initial_nm=float(wm_sim.get("initial_nm", 791.96)),
            )
        else:
            print("Using real hardware")
            tagger_hw = hw_config.get("tagger", {})
            self.tagger = Tagger(index=0, initialization_params=tagger_hw)
            self.wavemeter = WavemeterClient(
                host=str(wm_block.get("host", "10.54.6.156")),
                port=int(wm_block.get("port", 5000)),
                channel=self.wavechannel,
            )
            # The wavemeter server boots with only channel 1 in active_read by
            # default. Issue READ_ON for the configured channel so a fresh
            # session is usable without manually flipping it in the dialog.
            # Best-effort: a missing server here is loud-logged but not fatal —
            # `wavemeter_connected` will flip false and the GUI will show that.
            try:
                self.wavemeter.enable_read()
                self.wavemeter_connected = True
            except Exception as e:
                print(f"[DAQ] Could not enable read on channel {self.wavechannel}: {e}")
                self.wavemeter_connected = False

        # In simulation the mock client is always "connected". In real
        # hardware the flag is updated by `_daq_loop` after every poll.
        if simulation_mode:
            self.wavemeter_connected = True

        self.laser = LaserController(self.wavemeter, channel=self.wavechannel, config=laser_cfg)

        self.saver = None
        self.scanner = Scanner(self.laser, self.wavemeter)

        self.running = False
        self.events_processed = 0
        self.event_timestamps = deque(maxlen=1000)

        self.daq_thread = None

        self.pending_events_count = 0
        self.pending_bunches_count = 0
        self.rate_lock = threading.Lock()

        # Decoupled rate-window state. The integration time governs how
        # often a (t, rate) sample is emitted into `rate_samples`. It is
        # independent of the GUI refresh interval — the GUI just reads
        # whatever is in `rate_samples` on every repaint. See
        # `_maybe_flush_rate`, `set_integration_time`, `get_rate_history`.
        gui_cfg = self.config.get("gui_settings", {})
        self.integration_time_s = float(gui_cfg.get("integration_time_s", 0.1))
        self.rate_samples = deque(maxlen=2000)
        self._rate_t0 = None
        self._rate_window_start = None

        self.cached_wavenumber = 0.0
        self.sensor_lock = threading.Lock()

        self.last_scan_filename = None
        self.tof_online_mode = False

    def start(self):
        if self.running: return
        print("[DAQ] Starting system...")
        self.running = True
        self.tof_buffer = deque(maxlen=50000)
        with self.rate_lock:
            self.pending_events_count = 0
            self.pending_bunches_count = 0
            self.rate_samples.clear()
            self._rate_t0 = None
            self._rate_window_start = None

        self.tagger.start_reading()

        self.daq_thread = threading.Thread(target=self._daq_loop, daemon=True)
        self.daq_thread.start()

    def stop(self):
        self.running = False
        print("[DAQ] Stopping system...")

        if self.scanner.is_alive():
            self.scanner.stop()

        if hasattr(self.laser, 'stop'):
            self.laser.stop()

        if self.saver:
            self.saver.stop()
            self.saver = None

        self.tagger.stop()
        try:
            self.wavemeter.close()
        except Exception as e:
            print(f"[DAQ] wavemeter.close() failed: {e}")

    def start_scan(self, start_wn, end_wn, step, stop_mode, stop_value, loops=1):
        if not self.scanner.is_alive() and self.scanner.running == False:
            self.scanner = Scanner(self.laser, self.wavemeter)

        if self.scanner.is_alive():
             print("[DAQ] Scanner already running.")
             return

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filename_csv = f"data/scan_{timestamp}.csv"
        self.last_scan_filename = filename_csv
        filename_meta = f"data/scan_{timestamp}_meta.json"
        filename_final = f"data/final_scan_{timestamp}.csv"

        data_settings = self.config.get("data_settings", {})
        save_continuously = data_settings.get("save_continuously", True)

        self.saver = DataSaver(
            filename_csv,
            save_continuously=save_continuously,
            final_filename=filename_final
        )
        self.saver.start()
        print(f"[DAQ] Started logging to {filename_csv} (Continuous: {save_continuously})")

        metadata = {
            "timestamp": timestamp,
            "scan_parameters": {
                "start_wn": start_wn,
                "end_wn": end_wn,
                "step_size": step,
                "stop_mode": stop_mode,
                "stop_value": stop_value,
                "loops": loops,
                "loops_completed": 0
            },
            "laser_settings": self.config.get("control_settings", {}).get("laser_pid", {}),
            "wavemeter_server": self.config.get("wavemeter_server", {}),
            "hardware_settings": self.config.get("hardware_settings", {}),
            "simulation_settings": self.config.get("simulation_settings", {}),
            # Snapshot of the GUI display state at scan start. Without
            # this, a recorded scan can't be replayed with the same
            # integration window / rolling-average overlay that the
            # operator was watching.
            "gui_settings": self.config.get("gui_settings", {}),
            "simulation_mode": bool(self.simulation_mode),
        }

        if hasattr(self.laser, 'config'):
             metadata["laser_runtime_config"] = self.laser.config

        try:
            with open(filename_meta, 'w') as f:
                json.dump(metadata, f, indent=4)
            print(f"[DAQ] Saved metadata to {filename_meta}")
        except Exception as e:
            print(f"[DAQ] Failed to save metadata: {e}")

        self.scanner.configure(start_wn, end_wn, step, stop_mode, stop_value, loops, self._on_loop_complete)
        self.scanner.reset()
        self.tof_buffer = deque(maxlen=50000) # Clear buffer on new scan

        self.scanner.start()

    def _daq_loop(self):
        previous_bunch = -1
        previous_bunch2 = -1
        while self.running:
            self._maybe_flush_rate()

            if self.saver and not self.scanner.running:
                print("[DAQ] Scan finished. Stopping saver.")
                self.saver.stop()
                self.saver = None

            data = self.tagger.get_data()

            # Single cached wavenumber — no 4-channel array, no multimeter,
            # no spectrometer in the new experiment. The connectivity flag
            # is the live signal the GUI's StatusWidget surfaces; flipping
            # it here keeps the operator informed without scanning logs.
            with self.sensor_lock:
                try:
                    self.cached_wavenumber = self.wavemeter.get_wavenumber()
                    self.wavemeter_connected = True
                except Exception:
                    self.cached_wavenumber = 0.0
                    self.wavemeter_connected = False
                current_wn = self.cached_wavenumber

            for entry in data:
                channel = entry[2]
                # `entry[0]` is the tagger's global packet ID — used here as
                # the timestamp surrogate, same as the original DBD code. It
                # is also separately recorded as `bunch_id` below so offline
                # analysis can recover bunch counts. Not wall time; treat as
                # a monotonic ordinal.
                timestamp = entry[0]

                if channel == -1:  # Empty Bunch
                    with self.rate_lock:
                         self.pending_bunches_count += 1

                    if self.scanner.is_accumulating:
                         self.scanner.report_event(is_bunch=True)

                         if self.saver:
                             record = {
                                'timestamp': timestamp,
                                'channel': channel,
                                'tof': entry[3],
                                'wavemeter_wn': current_wn,
                                'laser_target_wn': self.scanner.current_wavenumber,
                                'scan_bin_index': self.scanner.current_bin_index,
                                'bunch_id': entry[0]
                            }
                             self.saver.add_event(record)

                if channel == 2:
                    self.events_processed += 1
                    self.event_timestamps.append(timestamp)

                    with self.rate_lock:
                         self.pending_events_count += 1
                         if entry[0] != previous_bunch:
                            self.pending_bunches_count += 1
                            previous_bunch = entry[0]

                    record = {
                        'timestamp': timestamp,
                        'channel': channel,
                        'tof': entry[3],
                        'wavemeter_wn': current_wn,
                        'laser_target_wn': self.scanner.current_wavenumber,
                        'scan_bin_index': self.scanner.current_bin_index,
                        'bunch_id': entry[0]
                    }

                    if self.scanner.is_accumulating and self.saver:
                        self.saver.add_event(record)
                        self.tof_buffer.append(entry[3])
                        self.scanner.report_event(is_bunch=False)
                        if entry[0] != previous_bunch2:
                            self.scanner.report_event(is_bunch=True)
                            previous_bunch2 = entry[0]
                    elif self.tof_online_mode:
                        self.tof_buffer.append(entry[3])

            time.sleep(self.config["gui_settings"]["refresh_rate_ms"]/1000)

    def update_laser_settings(self, new_config: dict):
        """
        Updates laser control settings at runtime. The dict can mix:
          - lock-judgment params (tolerance_wn, poll_interval, ...)
          - PID params (kp, ki, kd, vLow, vHigh, gain, offset)
          - channel (rerouting the underlying client)
        """
        if hasattr(self.laser, 'update_config'):
             self.laser.update_config(new_config)
        if hasattr(self.laser, 'update_pid_config'):
             self.laser.update_pid_config(new_config)

        if "channel" in new_config:
            new_ch = int(new_config["channel"])
            self.wavechannel = new_ch
            self.wavemeter.channel = new_ch
            self.laser.channel = new_ch
            # Auto-engage READ on the new channel so the GUI doesn't have to
            # think about it. If the server is unreachable we log and move on.
            try:
                self.wavemeter.enable_read(new_ch)
            except Exception as e:
                print(f"[DAQ] enable_read({new_ch}) failed: {e}")
            print(f"[DAQ] Wavemeter Channel updated to {self.wavechannel}")

        print("[DAQ] Laser settings updated.")


    def _maybe_flush_rate(self):
        """Emit one (t, rate) sample whenever the current integration window
        has elapsed. Called from `_daq_loop`. The first iteration just
        anchors `_rate_t0` / `_rate_window_start` and returns — that
        discards the multi-second startup backlog between
        `tagger.start_reading()` and the first loop tick."""
        now = time.monotonic()
        with self.rate_lock:
            if self._rate_t0 is None:
                self._rate_t0 = now
                self._rate_window_start = now
                # Discard whatever accumulated before the window anchor.
                self.pending_events_count = 0
                self.pending_bunches_count = 0
                return
            if now - self._rate_window_start < self.integration_time_s:
                return
            events = self.pending_events_count
            bunches = self.pending_bunches_count
            self.pending_events_count = 0
            self.pending_bunches_count = 0
            self._rate_window_start = now
            rate = (events / bunches) if bunches > 0 else 0.0
            self.rate_samples.append((now - self._rate_t0, rate))

    def get_rate_history(self):
        """Snapshot of the windowed rate history. Returns ([t...], [rate...])
        — two parallel lists of the same length, copied under the lock so
        the caller can safely iterate without holding it."""
        with self.rate_lock:
            if not self.rate_samples:
                return [], []
            times, rates = zip(*self.rate_samples)
            return list(times), list(rates)

    def set_integration_time(self, seconds: float):
        """Change the rate-emission cadence live. The current half-filled
        bin is discarded so the next emitted sample reflects exactly one
        full new-sized window (no partial-window artifact)."""
        seconds = float(seconds)
        if seconds < 0.005:
            seconds = 0.005
        elif seconds > 600.0:
            seconds = 600.0
        with self.rate_lock:
            self.integration_time_s = seconds
            self.pending_events_count = 0
            self.pending_bunches_count = 0
            # If the loop hasn't anchored yet, leave _rate_window_start as
            # None so the first iteration still acts as the anchor.
            if self._rate_window_start is not None:
                self._rate_window_start = time.monotonic()

    def clear_rate_history(self):
        """Drop all accumulated rate samples and reset the windowing clock.
        Used by the GUI's Reset button so a new scan starts at t=0 with no
        stale tail."""
        with self.rate_lock:
            self.rate_samples.clear()
            self._rate_t0 = None
            self._rate_window_start = None
            self.pending_events_count = 0
            self.pending_bunches_count = 0

    def get_latest_wavenumber(self):
        with self.sensor_lock:
            return self.cached_wavenumber

    def get_wavemeter_status(self) -> dict:
        """One-shot snapshot of the wavemeter link for the GUI's status row.
        `connected` is the result of the most recent poll attempt (or the
        initial `enable_read` for the very first frame). `simulation` is
        a fixed property of the DAQ instance."""
        wm = self.config.get("wavemeter_server", {})
        return {
            "simulation": bool(self.simulation_mode),
            "connected": bool(self.wavemeter_connected),
            "host": wm.get("host", ""),
            "port": wm.get("port", ""),
            "channel": int(self.wavechannel),
        }

    def _on_loop_complete(self, loop_number):
        """Callback from scanner when a loop finishes."""
        print(f"[DAQ] Loop {loop_number} complete. Saving snapshot.")
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filename = f"data/scan_snapshot_loop_{loop_number}_{timestamp}.csv"

        try:
            scan_data = self.scanner.scan_progress
            if not scan_data:
                return

            with open(filename, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(["Wavenumber_cm-1", "Rate_events_per_bunch", "Total_Events", "Total_Bunches"])
                writer.writerows(scan_data)
            print(f"[DAQ] Snapshot saved to {filename}")
        except Exception as e:
            print(f"[DAQ] Failed to save snapshot: {e}")
