"""Socket client for LASERLABCOMPUTER/new_wmServer.py.

The wavemeter server speaks a JSON-over-TCP protocol (one JSON object per
request, one newline-terminated JSON reply per response — except for SET,
which the server intentionally does not reply to). The DAQ uses cm^-1
internally; the wavemeter still speaks nm vacuum (the Bristol wavemeter's
native unit). All boundary conversion happens here.

Wire protocol (see new_wmServer.py:handle_client):

    Request                                                  Reply
    -------                                                  -----
    {"cmd":"GET"}                                            {"type":"total","data":{
                                                                "telemetry":{"<ch>":{latest_time,
                                                                                      latest_reading,
                                                                                      latest_error,
                                                                                      latest_output}, ...},
                                                                "config":{"<ch>":{channel, active_read,
                                                                                  active_pid, pid:{kp,ki,kd,
                                                                                                    setpoint,integral},
                                                                                  vLow, vHigh, gain, offset,
                                                                                  last_config}, ...}}}
    {"cmd":"CONFIG"}                                         {"type":"config","data":<config block>}
    {"cmd":"SET","channel":<ch>,"change":{<k>:<v>, ...}}     (no reply — server's sendall is commented out)

Channel indexing: the new server uses 0-indexed `wavePorts` keys directly
on the wire. We keep `wavemeter_server.channel` 1-indexed in settings (the
existing operator-facing convention) and translate at this boundary, so
existing settings.json files do not need to be renumbered.

Caveat re. enable_pid: the server has an `enablePID()` routine that seeds
the PID integral via `pid.reset(...)` so the loop doesn't kick on engage.
The JSON protocol exposes no verb that calls it — `SET active_pid=true`
flips the flag but skips the seeding. Live with it for now; the laser
team would need to add an `ENABLE_PID` verb to recover the reset path.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from typing import Any, Optional

from src.utils.units import nm_vacuum_to_wn, wn_to_nm_vacuum


# The new server's `handle_client` parses one JSON object per `recv()`. Two
# SETs sent within microseconds can land in a single recv buffer, which
# json.loads then rejects — the outer try/except in the server catches
# this and CLOSES the connection. We spread back-to-back sends out by this
# many seconds so independent SETs end up in independent recv() calls.
# 1 ms ≈ 1000 SETs/sec ceiling, far above any human-driven workflow.
_SEND_SPACER_S = 0.001


class WavemeterClientError(RuntimeError):
    """Raised when the server reply can't be parsed or the socket fails."""


class WavemeterClient:
    def __init__(self, host: str, port: int, channel: int = 1, timeout: float = 2.0):
        self.host = host
        self.port = int(port)
        # 1-indexed externally to match the legacy convention and existing
        # settings.json files; converted to the server's 0-indexed wavePort
        # key inside _zero_indexed().
        self.channel = int(channel)
        self.timeout = float(timeout)
        self._sock: Optional[socket.socket] = None
        self._buf = b""
        self._lock = threading.Lock()

    # ---- Public surface used by LaserController / DAQSystem / GUI ----

    def get_wavenumber(self) -> float:
        """Return the configured channel's reading in cm^-1, or 0.0 if absent."""
        nm = self.get_reading_nm()
        if nm <= 0:
            return 0.0
        return nm_vacuum_to_wn(nm)

    def get_reading_nm(self, channel: Optional[int] = None) -> float:
        """Raw wavelength reading (nm) for a specific channel."""
        ch = self._zero_indexed(channel)
        data = self._fetch_total()
        telemetry = data.get("telemetry", {})
        port = telemetry.get(str(ch)) or telemetry.get(ch) or {}
        return float(port.get("latest_reading", 0.0) or 0.0)

    def set_setpoint_wn(self, target_wn: float) -> None:
        """Push a wavenumber setpoint (cm^-1) by converting to nm first."""
        self.set_pid_param("setpoint", wn_to_nm_vacuum(float(target_wn)))

    def set_pid_param(self, key: str, value: Any, channel: Optional[int] = None) -> None:
        """Fire-and-forget SET. The new server intentionally does not reply,
        so a hardware-side error will not surface here — only transport
        failures (socket closed / unreachable host) raise."""
        self.set_params({key: value}, channel=channel)

    def set_params(self, changes: dict, channel: Optional[int] = None) -> None:
        """Push multiple parameter changes to one channel in a single SET
        message. Preferred over a loop of `set_pid_param` calls because the
        server parses one JSON object per recv — sending N changes as one
        SET sidesteps both the recv-coalescing risk and the latency of N
        round trips."""
        if not changes:
            return
        ch = self._zero_indexed(channel)
        self._send_json({"cmd": "SET", "channel": ch, "change": dict(changes)})

    def enable_pid(self, channel: Optional[int] = None) -> None:
        # NOTE: the server's `enablePID()` routine (which calls pid.reset
        # to seed the integral so the loop doesn't kick on engage) is not
        # reachable from JSON — only the `active_pid` flag is. If the lab
        # adds a dedicated ENABLE_PID verb, switch to it here.
        self.set_pid_param("active_pid", True, channel=channel)

    def disable_pid(self, channel: Optional[int] = None) -> None:
        self.set_pid_param("active_pid", False, channel=channel)

    def enable_read(self, channel: Optional[int] = None) -> None:
        self.set_pid_param("active_read", True, channel=channel)

    def disable_read(self, channel: Optional[int] = None) -> None:
        self.set_pid_param("active_read", False, channel=channel)

    def get_status(self, channel: Optional[int] = None) -> dict:
        """Flat status dict for one channel, in the shape LaserControlDialog
        expects (kp, ki, kd, setpoint, vLow, vHigh, gain, offset,
        active_pid, active_read, latest_reading, latest_error,
        latest_output)."""
        ch = self._zero_indexed(channel)
        data = self._fetch_total()
        config = (data.get("config") or {})
        telemetry = (data.get("telemetry") or {})
        cfg = config.get(str(ch)) or config.get(ch) or {}
        tel = telemetry.get(str(ch)) or telemetry.get(ch) or {}
        pid = cfg.get("pid") or {}
        out = {
            "kp": float(pid.get("kp", 0.0) or 0.0),
            "ki": float(pid.get("ki", 0.0) or 0.0),
            "kd": float(pid.get("kd", 0.0) or 0.0),
            "setpoint": float(pid.get("setpoint", 0.0) or 0.0),
            "vLow": float(cfg.get("vLow", 0.0) or 0.0),
            "vHigh": float(cfg.get("vHigh", 0.0) or 0.0),
            "gain": float(cfg.get("gain", 0.0) or 0.0),
            "offset": float(cfg.get("offset", 0.0) or 0.0),
            "active_pid": float(bool(cfg.get("active_pid"))),
            "active_read": float(bool(cfg.get("active_read"))),
            "latest_reading": float(tel.get("latest_reading", 0.0) or 0.0),
            "latest_error": float(tel.get("latest_error", 0.0) or 0.0),
            "latest_output": float(tel.get("latest_output", 0.0) or 0.0),
        }
        return out

    def ping(self) -> None:
        """Round-trip GET used as a smoke test. SET is fire-and-forget under
        the new protocol so it cannot be used to verify the server is alive
        — only GET (which does reply) can. Raises WavemeterClientError on
        any transport or parse failure."""
        self._fetch_total()

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    # ---- Internals ----

    def _zero_indexed(self, channel: Optional[int]) -> int:
        """settings.json channel is 1-indexed; new_wmServer is 0-indexed."""
        ch = self.channel if channel is None else int(channel)
        zero = ch - 1
        if zero < 0:
            raise WavemeterClientError(
                f"channel {ch} is < 1; settings.json channel is 1-indexed"
            )
        return zero

    def _fetch_total(self) -> dict:
        reply = self._ask_json({"cmd": "GET"})
        if reply.get("type") not in ("total", "config", "telemetry"):
            raise WavemeterClientError(
                f"unexpected reply from wmServer: {reply!r}"
            )
        data = reply.get("data") or {}
        # The server wraps config_dict() in {"config": ...} inside total_dict,
        # so `data` already has top-level "telemetry" and/or "config" keys.
        return data

    def _ensure_connected(self) -> None:
        if self._sock is not None:
            return
        s = socket.create_connection((self.host, self.port), timeout=self.timeout)
        s.settimeout(self.timeout)
        self._sock = s
        self._buf = b""

    def _close_locked(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
        self._sock = None
        self._buf = b""

    def _send_json(self, payload: dict) -> None:
        """Send one JSON-encoded request and do NOT wait for a reply. Used
        for SET, which the server explicitly does not respond to. Reconnects
        once on transport failure. See `_SEND_SPACER_S` for why we briefly
        pause after the send."""
        wire = json.dumps(payload).encode("ascii")
        with self._lock:
            for attempt in (0, 1):
                try:
                    self._ensure_connected()
                    self._sock.sendall(wire)
                    if _SEND_SPACER_S > 0:
                        time.sleep(_SEND_SPACER_S)
                    return
                except (OSError, ConnectionError) as e:
                    self._close_locked()
                    if attempt == 1:
                        raise WavemeterClientError(
                            f"wmServer at {self.host}:{self.port} unreachable: {e}"
                        )

    def _ask_json(self, payload: dict) -> dict:
        """Send one JSON-encoded request and read one newline-terminated
        JSON reply. Reconnects and retries once on transport errors."""
        wire = json.dumps(payload).encode("ascii")
        with self._lock:
            for attempt in (0, 1):
                try:
                    self._ensure_connected()
                    self._sock.sendall(wire)
                    line = self._recv_line_locked()
                    try:
                        return json.loads(line)
                    except json.JSONDecodeError as je:
                        raise WavemeterClientError(
                            f"wmServer returned non-JSON line: {line!r} ({je})"
                        )
                except (OSError, ConnectionError) as e:
                    self._close_locked()
                    if attempt == 1:
                        raise WavemeterClientError(
                            f"wmServer at {self.host}:{self.port} unreachable: {e}"
                        )

    def _recv_line_locked(self) -> str:
        while b"\n" not in self._buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("wmServer closed connection")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return line.decode(errors="replace").rstrip("\r")
