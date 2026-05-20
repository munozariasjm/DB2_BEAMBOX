
import json
import os
from typing import Dict, Any

class SettingsManager:
    DEFAULT_SETTINGS = {
        "scan_settings": {
            "start_wn": 16666.0,
            "end_wn": 16680.0,
            "step_size": 0.5,
            "stop_mode": "bunches",
            "stop_val": 100,
            "loops": 1
        },
        "gui_settings": {
            "window_width": 1200,
            "window_height": 800,
            "refresh_rate_ms": 500,
            # Rate-plot knobs. integration_time_s is the physics window over
            # which events/bunches are aggregated into one plotted point;
            # decoupled from refresh_rate_ms (which is purely UI repaint).
            "integration_time_s": 0.1,
            "rate_avg_enabled": False,
            "rate_avg_window_s": 0.5
        },
        "data_settings": {
            "default_save_dir": "data",
            "auto_save": True
        },
        # Tagger hardware front-end config. `input_mode` flips between the
        # TTL and NIM presets (see src/devices/tagger.py:_INPUT_MODE_PRESETS).
        # Explicit fields override the preset entry-by-entry.
        "hardware_settings": {
            "tagger": {
                "input_mode": "TTL",
                "channel_starts_us": 1.0,
                "channel_stops_us": 10.0
            }
        },
        # Network endpoint for LASERLABCOMPUTER/wmServer.py plus the lock-
        # judgment knobs used by the LaserController polling loop.
        "wavemeter_server": {
            "host": "10.54.6.156",
            "port": 5000,
            "channel": 1,
            "tolerance_wn": 1e-5,
            "poll_interval": 0.1,
            "required_stable_samples": 4,
            "wm_averaging_samples": 5
        },
        "simulation_settings": {
            "tagger": {
                "repetition_rate": 50.0,
                "mean_events_per_bunch": 200.0
            },
            "wavemeter": {
                "slew_rate_nm_per_s": 0.05,
                "noise_nm": 1e-7,
                "initial_nm": 791.96
            }
        },
        # PID parameters pushed to the wavemeter server's WavePort. These
        # are server-side knobs (kp/ki/kd, voltage clamp, gain/offset) —
        # the client does no PID of its own.
        "control_settings": {
            "laser_pid": {
                "kp": 1.0,
                "ki": 0.0,
                "kd": 0.0,
                "vLow": -5.0,
                "vHigh": 5.0,
                "gain": 10.0,
                "offset": 0.0,
                "continuous": False
            }
        },
        # Sim-by-default. A fresh checkout boots without needing the
        # wavemeter server or the TimeTagger card present — flip this to
        # false in settings.json on the actual DAQ host. The loud terminal
        # + GUI banners make it impossible to mistake simulated data for
        # real data, so the conservative-real-default rationale no longer
        # applies.
        "simulation_mode": True
    }

    def __init__(self, config_path: str = "settings.json"):
        self.config_path = config_path
        self.settings = self.load_settings()

    def load_settings(self) -> Dict[str, Any]:
        """Loads settings from JSON file. Creates file with defaults if not exists."""
        if not os.path.exists(self.config_path):
            self.save_settings(self.DEFAULT_SETTINGS)
            return self.DEFAULT_SETTINGS.copy()

        try:
            with open(self.config_path, 'r') as f:
                user_settings = json.load(f)

            # Shallow merge with defaults. Top-level values are usually dicts
            # (the section blocks), but a few are scalars (e.g. simulation_mode).
            # Only attempt dict.update() when BOTH sides are dicts; otherwise
            # the user's value overrides outright.
            merged = self.DEFAULT_SETTINGS.copy()
            for section, values in user_settings.items():
                if (
                    section in merged
                    and isinstance(merged[section], dict)
                    and isinstance(values, dict)
                ):
                    merged[section].update(values)
                else:
                    merged[section] = values
            return merged
        except Exception as e:
            print(f"Error loading settings: {e}. Using defaults.")
            return self.DEFAULT_SETTINGS.copy()

    def save_settings(self, settings: Dict[str, Any] = None):
        """Saves current settings to JSON file."""
        if settings is None:
            settings = self.settings

        try:
            with open(self.config_path, 'w') as f:
                json.dump(settings, f, indent=4)
        except Exception as e:
            print(f"Error saving settings: {e}")

    def get_section(self, section: str) -> Dict[str, Any]:
        return self.settings.get(section, {})
