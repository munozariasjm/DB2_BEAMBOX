"""Socket client for LASERLABCOMPUTER/wmServer.py.

The DAQ uses cm^-1 internally; the wavemeter server speaks nm vacuum (the
Bristol wavemeter's native unit). All boundary conversion happens here:
`get_wavenumber()` returns cm^-1; `set_setpoint_wn()` accepts cm^-1 and
sends nm to the server. PID parameters are passed through raw (kp/ki/kd
have no units; vLow/vHigh/gain/offset are server-side voltage settings).

A single persistent TCP socket is kept open for the client's lifetime.
Each command is sent newline-terminated; the reply is read until the next
newline. On a transport error the socket is rebuilt once and the command
retried. Higher-level callers (LaserController, GUI) treat reconnects as
transparent and only see hard failures.
"""

from __future__ import annotations

import socket
import threading
from typing import Optional

from src.utils.units import nm_vacuum_to_wn, wn_to_nm_vacuum


class WavemeterClientError(RuntimeError):
    """Raised when the server returns an `ERR ...` reply or the socket fails."""


class WavemeterClient:
    def __init__(self, host: str, port: int, channel: int = 1, timeout: float = 2.0):
        self.host = host
        self.port = int(port)
        self.channel = int(channel)
        self.timeout = float(timeout)
        self._sock: Optional[socket.socket] = None
        self._buf = b""
        self._lock = threading.Lock()

    # ---- Public surface used by LaserController / DAQSystem ----

    def get_wavenumber(self) -> float:
        """Return the configured channel's reading in cm^-1, or 0.0 if absent."""
        data = self._parse_get(self._ask("GET"))
        nm = float(data.get(self.channel, 0.0))
        if nm <= 0:
            return 0.0
        return nm_vacuum_to_wn(nm)

    def get_reading_nm(self, channel: Optional[int] = None) -> float:
        """Raw wavelength reading (nm) for a specific channel."""
        ch = self.channel if channel is None else int(channel)
        data = self._parse_get(self._ask("GET"))
        return float(data.get(ch, 0.0))

    def set_setpoint_wn(self, target_wn: float) -> None:
        """Push a wavenumber setpoint (cm^-1) by converting to nm first."""
        nm = wn_to_nm_vacuum(float(target_wn))
        self.set_pid_param("setpoint", nm)

    def set_pid_param(self, key: str, value: float, channel: Optional[int] = None) -> None:
        ch = self.channel if channel is None else int(channel)
        reply = self._ask(f"SET {ch} {key}={float(value)}")
        if reply != "OK":
            raise WavemeterClientError(f"SET {key}={value} failed: {reply}")

    def enable_pid(self, channel: Optional[int] = None) -> None:
        ch = self.channel if channel is None else int(channel)
        reply = self._ask(f"PID_ON {ch}")
        if reply != "OK":
            raise WavemeterClientError(f"PID_ON {ch} failed: {reply}")

    def disable_pid(self, channel: Optional[int] = None) -> None:
        ch = self.channel if channel is None else int(channel)
        reply = self._ask(f"PID_OFF {ch}")
        if reply != "OK":
            raise WavemeterClientError(f"PID_OFF {ch} failed: {reply}")

    def enable_read(self, channel: Optional[int] = None) -> None:
        ch = self.channel if channel is None else int(channel)
        reply = self._ask(f"READ_ON {ch}")
        if reply != "OK":
            raise WavemeterClientError(f"READ_ON {ch} failed: {reply}")

    def disable_read(self, channel: Optional[int] = None) -> None:
        ch = self.channel if channel is None else int(channel)
        reply = self._ask(f"READ_OFF {ch}")
        if reply != "OK":
            raise WavemeterClientError(f"READ_OFF {ch} failed: {reply}")

    def get_status(self, channel: Optional[int] = None) -> dict:
        ch = self.channel if channel is None else int(channel)
        reply = self._ask(f"STATUS {ch}")
        if reply.startswith("ERR"):
            raise WavemeterClientError(f"STATUS {ch} failed: {reply}")
        out = {}
        for kv in reply.split(","):
            if ":" not in kv:
                continue
            k, v = kv.split(":", 1)
            try:
                out[k] = float(v)
            except ValueError:
                out[k] = v
        return out

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    # ---- Internals ----

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

    def _ask(self, command: str) -> str:
        """Send one newline-terminated command, return one line of reply.
        Reconnects and retries once on socket-level errors."""
        wire = command.encode("ascii") + b"\n"
        with self._lock:
            for attempt in (0, 1):
                try:
                    self._ensure_connected()
                    self._sock.sendall(wire)
                    return self._recv_line_locked()
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

    @staticmethod
    def _parse_get(reply: str) -> dict:
        """Parse a `time:t,1:wn,2:wn,...` reply into {int_channel: float, "time": float}."""
        out = {}
        for kv in reply.split(","):
            if ":" not in kv:
                continue
            k, v = kv.split(":", 1)
            try:
                vf = float(v)
            except ValueError:
                continue
            if k == "time":
                out["time"] = vf
            else:
                try:
                    out[int(k)] = vf
                except ValueError:
                    continue
        return out
