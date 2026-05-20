"""No-op wavemeter client used when the wmServer is unreachable or
explicitly disabled in `settings.json` (`wavemeter_server.enabled=false`).

Mirrors the public surface of `WavemeterClient` so `DAQSystem`, `Scanner`,
and the GUI can swap it in without code-path changes. Every read returns
0.0; every write is silently dropped. Useful when bringing up the tagger
side of the experiment ahead of the wavemeter server.

The status widget's wavemeter row turns orange ("DISABLED") rather than
red ("DISCONNECTED") when this stub is in use, so the operator can tell
"no server by design" from "server is down".
"""

from __future__ import annotations

from typing import Optional


class NullWavemeterClient:
    def __init__(self, host: str = "", port: int = 0, channel: int = 1, timeout: float = 0.0):
        self.host = host
        self.port = int(port)
        self.channel = int(channel)
        self.timeout = float(timeout)

    def get_wavenumber(self) -> float:
        return 0.0

    def get_reading_nm(self, channel: Optional[int] = None) -> float:
        return 0.0

    def set_setpoint_wn(self, target_wn: float) -> None:
        return

    def set_pid_param(self, key: str, value: float, channel: Optional[int] = None) -> None:
        return

    def enable_pid(self, channel: Optional[int] = None) -> None:
        return

    def disable_pid(self, channel: Optional[int] = None) -> None:
        return

    def enable_read(self, channel: Optional[int] = None) -> None:
        return

    def disable_read(self, channel: Optional[int] = None) -> None:
        return

    def get_status(self, channel: Optional[int] = None) -> dict:
        # Mirror the shape of WavemeterClient.get_status so the GUI's
        # "Sync from server" code path doesn't blow up. All zeros.
        return {
            "kp": 0.0, "ki": 0.0, "kd": 0.0,
            "setpoint": 0.0,
            "vLow": 0.0, "vHigh": 0.0,
            "gain": 0.0, "offset": 0.0,
            "active_pid": 0.0,
            "active_read": 0.0,
            "latest_reading": 0.0,
            "latest_error": 0.0,
            "latest_output": 0.0,
        }

    def close(self) -> None:
        return
