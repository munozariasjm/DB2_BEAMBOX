"""Wavemeter socket server with built-in per-channel PID.

This file is a copy of `new_manuals/laser_server/wmServer.py`,
extended with a small command protocol so the DAQ can push
PID parameters and toggle PID/read state from the GUI. The hardware
interfaces (Bristol SCPI wavemeter, mcculw analog out) and the PID engine
itself are unchanged — only `SocketServer.handle_client`.

Wire protocol (newline-terminated ASCII, one command per line):

    GET                         (existing) → "time:t,1:wn,...\\n"
    SET <ch> <key>=<value>      → "OK\\n" or "ERR <msg>\\n"
    PID_ON <ch>                 → "OK\\n" / "ERR ..."
    PID_OFF <ch>                → "OK\\n"
    READ_ON <ch>                → "OK\\n"
    READ_OFF <ch>               → "OK\\n"
    STATUS <ch>                 → "kp:..,ki:..,kd:..,setpoint:..,active_pid:..,active_read:..,latest_reading:..,latest_error:..,latest_output:..\\n"

`SET` accepts keys handled by WavePort.updateParams: kp, ki, kd, setpoint,
vLow, vHigh, gain, offset. All numeric (float). Channels are 1-indexed
externally — `SET 1 ...` targets the same port that appears as `1:<wn>` in
GET (i.e. internal `wavePorts[0]`). Done because the GET reply was already
1-indexed; we make the rest of the protocol consistent rather than have
SET/STATUS run a different numbering scheme than GET.
"""

from dataclasses import dataclass, field
try:
  from mcculw import ul
  from mcculw.enums import DigitalIODirection, ULRange, BoardInfo, DigitalIODirection, DigitalPortType
except ImportError:
  ul = None
  DigitalIODirection = ULRange = BoardInfo = DigitalPortType = None
try:
  from Bristol.pyBristolSCPI import pyBristolSCPI
  from Bristol.digital import DigitalProps, PortInfo
except ImportError:
  pyBristolSCPI = None
  DigitalProps = PortInfo = None
import time, socket, threading, json, os

@dataclass
class PIDState:
  kp      : float = 1
  ki      : float = 0
  kd      : float = 0
  setpoint: float = 0
  integral: float = 0
  previous_error: float = 0
  previous_time: float = field(default_factory=time.perf_counter)
  def update(self, measurement):
    now = time.perf_counter()
    error=self.setpoint-measurement
    if self.previous_time:
      dt = now-self.previous_time
      if dt<=0: dt=1e-6
      derivative = (error-self.previous_error)/dt
    else:
      dt=0
      derivative=0

    self.integral += error*dt
    output=self.kp*error + self.ki*self.integral + self.kd*derivative
    self.previous_error=error
    self.previous_time = now
    return(error, output)

  def reset(self, measurement, current_voltage, gain, offset):
    now=time.perf_counter()
    error = self.setpoint-measurement
    self.previous_error=error
    self.previous_time=now
    if gain==0:desired_pid_output=0
    else: desired_pid_output=(current_voltage-offset)/gain
    if self.ki!=0: self.integral=(desired_pid_output-self.kp*error)/self.ki
    else:          self.integral=0

@dataclass
class WavePort:
  channel         :int
  active_read     :bool     = False
  active_pid      :bool     = False
  pid             :PIDState = field(default_factory=PIDState)
  vLow            :float    = -5
  vHigh           :float    =  5
  gain            :float    =  10
  offset          :float    =  0
  latest_reading  :float    =  0
  latest_error    :float    =  0
  latest_output   :float    =  0
  def updateParams(self, **kwargs):
    for key, value in kwargs.items():
      if hasattr(self.pid, key): setattr(self.pid, key, value)
      elif hasattr(self, key): setattr(self, key, value)
      else: raise AttributeError(f"Unknown WavePort parameter: {key}")

  def getParam(self, key):
    if hasattr(self.pid, key): return(getattr(self.pid, key))
    elif hasattr(self, key): return(getattr(self, key))
    else: raise AttributeError(f"Unknown WavePort parameter: {key}")
  def enablePID(self):
    if not self.active_read:
      print('must enable channel read before activating channel PID'); return(False)
    self.pid.reset(measurement=self.latest_reading, current_voltage=self.latest_output, gain=self.gain, offset=self.offset); self.active_pid=True
    return(True)
  def disablePID(self): self.active_pid=False
  def clamp(self, value):    return max(self.vLow, min(self.vHigh, value))
  def wavelength_to_voltage(self, pid_output): return(self.gain*pid_output+self.offset)
  def update_pid(self, measurement):
    error, pid_output = self.pid.update(measurement)
    voltage=self.clamp(self.wavelength_to_voltage(pid_output))
    return(error, voltage)

class AppState:
  mlc_map = {0:(0,4),
             1:(1,4),
             2:(0,2),
             3:(1,2),
             4:(0,1),
             5:(1,1),
             6:(0,3),
             7:(1,3)}
  def __init__(self, allChannels = [*range(8)], activeChannels = [0]):
    self.lock = threading.Lock()
    self.running=False
    self.wavePorts={}
    for ch in allChannels: self.wavePorts[ch]=WavePort(channel=ch)
    for ch in activeChannels: self.wavePorts[ch].active_read=True
    defaults={0:398.91112672,
              1:760,
              2:935,
              3:780,
              4:0,
              5:787.62484,
              6:0,
              7:0}
    for ch, sp in defaults.items():
      if ch in self.wavePorts:
        self.wavePorts[ch].pid.setpoint=sp

  def get_snapshot(self):
    with self.lock:
      output={"time":time.time()}
      for ch, wp in self.wavePorts.items():
        if wp.active_read: output[ch+1] = self.wavePorts[ch].latest_reading
      return output

  def get_status(self, ch):
    """Return a flat dict snapshot of a WavePort + its PID for STATUS replies."""
    with self.lock:
      if ch not in self.wavePorts:
        raise KeyError(f"channel {ch} not configured")
      wp = self.wavePorts[ch]
      return {
        "kp": wp.pid.kp,
        "ki": wp.pid.ki,
        "kd": wp.pid.kd,
        "setpoint": wp.pid.setpoint,
        "vLow": wp.vLow,
        "vHigh": wp.vHigh,
        "gain": wp.gain,
        "offset": wp.offset,
        "active_pid": int(wp.active_pid),
        "active_read": int(wp.active_read),
        "latest_reading": wp.latest_reading,
        "latest_error": wp.latest_error,
        "latest_output": wp.latest_output,
      }

class WavemeterMultiplexer:
  def __init__(self, state:AppState):
    self.state=state
    if pyBristolSCPI is None:
      raise RuntimeError("pyBristolSCPI not available; cannot run real multiplexer")
    self.wavemeter = pyBristolSCPI()
    self.fos_board_num = 0
    digital_props=DigitalProps(self.fos_board_num)
    self.port = next(
            (port for port in digital_props.port_info
             if port.supports_output), None)
    if self.port == None:
      print("unsupported board")
      raise RuntimeError("No digital output port found")
    if self.port.is_port_configurable: ul.d_config_port(self.fos_board_num,
                                                   self.port.type,
                                                   DigitalIODirection.OUT)
    self.lastActiveChannels = [ch for ch in list(self.state.wavePorts.keys()) if self.state.wavePorts[ch].active_read]

  def set_output_voltage(self, voltage, channel):
    '''applies PID voltage to appropriate output port'''
    meas_comp_board_ch, meas_comp_board_num = self.state.mlc_map[channel]
    try:
      output_value = ul.from_eng_units(meas_comp_board_num, ULRange.BIP10VOLTS, voltage)
      ul.a_out(meas_comp_board_num, meas_comp_board_ch, ULRange.BIP10VOLTS, output_value)
    except Exception as e: print(f"Error: {e}")

  def run(self):
    print("Wavemeter thread started")
    while self.state.running:
      try:
        with self.state.lock:
          active_channels=[ch for ch in list(self.state.wavePorts.keys()) if self.state.wavePorts[ch].active_read]
        for ch in active_channels:
            wp=self.state.wavePorts[ch]
            ul.d_out(self.fos_board_num, self.port.type, ch)
            if (len(active_channels)>1) or (len(self.lastActiveChannels)>1):
              time.sleep(.025)
            readout=self.wavemeter.readWL()
            if wp.active_pid:
              error, voltage = wp.update_pid(readout)
              self.set_output_voltage(voltage, ch)
            else:
              error = readout-wp.pid.setpoint
              voltage=wp.latest_output
            with self.state.lock:
              wp.latest_reading=readout
              wp.latest_error=error
              wp.latest_output = voltage
        self.lastActiveChannels = active_channels
      except Exception as e:
        if not self.state.running: break
        print("Wavemeter thread crashed:", e)
        import traceback
        traceback.print_exc()
        time.sleep(0.1)
  def close(self):
    try: self.wavemeter.tn.close()
    except: pass


# --- Valid keys for SET. Restricted to numeric WavePort/PID params; readonly
# fields (latest_*, active_*) and the channel index are NOT settable here.
_SET_KEYS = {"kp", "ki", "kd", "setpoint", "vLow", "vHigh", "gain", "offset"}


class SocketServer:
  def __init__(self, state:AppState, host="0.0.0.0", port=5000):
    self.state=state
    self.host=host
    self.port=port
    self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    self.server_socket.settimeout(1)

  def _format_get(self):
    snapshot = self.state.get_snapshot()
    return ''.join([f'{key}:{snapshot[key]},' for key in snapshot.keys()]).rstrip(',') + '\n'

  def _format_status(self, ch):
    status = self.state.get_status(ch)
    return ','.join(f'{k}:{v}' for k, v in status.items()) + '\n'

  def _parse_channel(self, raw: str) -> int:
    """External 1-indexed channel → internal wavePorts key (0-indexed)."""
    ext = int(raw)
    if ext < 1:
      raise ValueError(f"channel {ext} below 1 (channels are 1-indexed)")
    return ext - 1

  def _dispatch(self, command: str) -> str:
    """Parse one command line and return the reply (newline-terminated)."""
    cmd = command.strip()
    if not cmd:
      return "ERR empty command\n"
    parts = cmd.split()
    head = parts[0].upper()

    try:
      if head == "GET":
        return self._format_get()

      if head == "STATUS":
        if len(parts) < 2:
          return "ERR STATUS requires channel\n"
        return self._format_status(self._parse_channel(parts[1]))

      if head == "PID_ON":
        if len(parts) < 2: return "ERR PID_ON requires channel\n"
        ch = self._parse_channel(parts[1])
        with self.state.lock:
          wp = self.state.wavePorts.get(ch)
          if wp is None: return f"ERR channel {parts[1]} not configured\n"
          ok = wp.enablePID()
        return "OK\n" if ok else "ERR enablePID returned False (read disabled?)\n"

      if head == "PID_OFF":
        if len(parts) < 2: return "ERR PID_OFF requires channel\n"
        ch = self._parse_channel(parts[1])
        with self.state.lock:
          wp = self.state.wavePorts.get(ch)
          if wp is None: return f"ERR channel {parts[1]} not configured\n"
          wp.disablePID()
        return "OK\n"

      if head == "READ_ON":
        if len(parts) < 2: return "ERR READ_ON requires channel\n"
        ch = self._parse_channel(parts[1])
        with self.state.lock:
          wp = self.state.wavePorts.get(ch)
          if wp is None: return f"ERR channel {parts[1]} not configured\n"
          wp.active_read = True
        return "OK\n"

      if head == "READ_OFF":
        if len(parts) < 2: return "ERR READ_OFF requires channel\n"
        ch = self._parse_channel(parts[1])
        with self.state.lock:
          wp = self.state.wavePorts.get(ch)
          if wp is None: return f"ERR channel {parts[1]} not configured\n"
          wp.active_read = False
          wp.active_pid = False  # can't run PID without reads
        return "OK\n"

      if head == "SET":
        # Form: SET <ch> <key>=<value>
        if len(parts) < 3:
          return "ERR SET requires channel and key=value\n"
        ch = self._parse_channel(parts[1])
        kv = parts[2]
        if "=" not in kv:
          return "ERR SET arg must be key=value\n"
        key, value_str = kv.split("=", 1)
        if key not in _SET_KEYS:
          return f"ERR unknown key {key} (allowed: {sorted(_SET_KEYS)})\n"
        value = float(value_str)
        with self.state.lock:
          wp = self.state.wavePorts.get(ch)
          if wp is None: return f"ERR channel {parts[1]} not configured\n"
          wp.updateParams(**{key: value})
        return "OK\n"

      return f"ERR unknown command {head}\n"
    except (ValueError, KeyError, AttributeError) as e:
      return f"ERR {type(e).__name__}: {e}\n"

  def handle_client(self, conn, address):
    print(f"Client connected: {address}")
    buf = b""
    try:
      while self.state.running:
        try:
          data = conn.recv(1024)
          if not data:
            break
          buf += data
          while b"\n" in buf:
            line_bytes, buf = buf.split(b"\n", 1)
            command = line_bytes.decode(errors="replace").rstrip("\r")
            # Backward-compat: bare GET with no newline is still tolerated by
            # the legacy client. We split on newline above so the historic
            # buffered behaviour just works.
            reply = self._dispatch(command)
            conn.sendall(reply.encode())
          # Legacy: the original client sends `GET` without a
          # trailing newline and reads until it sees one. Support that by
          # treating a fully-buffered command without `\n` as a single line
          stripped = buf.strip()
          if stripped == b"GET":
            reply = self._dispatch(stripped.decode())
            conn.sendall(reply.encode())
            buf = b""
        except (ConnectionResetError, BrokenPipeError):
          print(f"Client disconnected abruptly: {address}"); break
    except Exception as e:
      print(f"Client {address} error: {e}")
    finally:
      conn.close()
      print(f"Connection closed: {address}")

  def run(self):
    self.server_socket.bind((self.host, self.port))
    self.server_socket.listen(5)
    print(f"Server listening on {self.host}:{self.port}")

    while self.state.running:
      try:
        conn, address = self.server_socket.accept()
        thread = threading.Thread(target=self.handle_client, args=(conn, address), daemon=True)
        thread.start()
      except socket.timeout: continue
      except Exception as e:
        if not self.state.running: break
        print(f"Server Exception: {e}")

  def close(self):
    try: self.server_socket.close()
    except: pass

if __name__ == '__main__':
  state = AppState()
  wm = WavemeterMultiplexer(state)
  wm_thread=threading.Thread(target=wm.run, daemon=True)
  server=SocketServer(state)
  server_thread=threading.Thread(target=server.run, daemon=True)
  state.running=True
  wm_thread.start()
  server_thread.start()
  for i in range(2):
    print(i)
    time.sleep(1)
  toStop = input('enter X to stop')
  state.running=False
  wm.close()
  server.close()
