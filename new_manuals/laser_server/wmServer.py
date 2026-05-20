from dataclasses import dataclass, field
from mcculw import ul
from mcculw.enums import DigitalIODirection, ULRange, BoardInfo, DigitalIODirection, DigitalPortType
from Bristol.pyBristolSCPI import pyBristolSCPI
from Bristol.digital import DigitalProps, PortInfo
# from Bristol.PID.daq import DAQ as PID_DAQ
import time, socket, threading, json

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
    # now = time.perf_counter(); if now-self.pid.previous_time>5: self.pid.reset()
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
  def __init__(self, allChannels = [*range(8)], activeChannels = [0]):#,1,2,3,5]):
    self.lock = threading.Lock()
    self.running=False
    # self.activeChannels=activeChannels
    self.wavePorts={}
    for ch in allChannels: self.wavePorts[ch]=WavePort(channel=ch)
    for ch in activeChannels: self.wavePorts[ch].active_read=True
    # self.pid_daq = PID_DAQ()
    defaults={0:398.91112672,#398.91111876,
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
  def addChannel(self, ch):pass#TODO
  def removeChannel(self, ch):pass#TODO
  def set_setPoint(self, ch):pass#TODO: WavePorts objects currently don't do anything to pid_engine

  def get_snapshot(self):
    with self.lock:
      output={"time":time.time()}
      for ch, wp in self.wavePorts.items():
        if wp.active_read: output[ch+1] = self.wavePorts[ch].latest_reading
      return output

class WavemeterMultiplexer:
  def __init__(self, state:AppState):#, fos_ports:list):
    self.state=state
    self.wavemeter = pyBristolSCPI()
    self.fos_board_num = 0
    digital_props=DigitalProps(self.fos_board_num)
    # Find the first port that supports output, defaulting to None if one is not found.
    self.port = next(
            (port for port in digital_props.port_info
             if port.supports_output), None)
    if self.port == None:
      print("unsupported board")
      raise RuntimeError("No digital output port found")
    # print(port.type)
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
    except ULError as e: print(f"Error: {e}")

  def run(self):
    print("Wavemeter thread started")
    while self.state.running:
      try:
        with self.state.lock:
          active_channels=[ch for ch in list(self.state.wavePorts.keys()) if self.state.wavePorts[ch].active_read]
        for ch in active_channels:
            wp=self.state.wavePorts[ch]
            # print(f"switching to port {ch}")
            ul.d_out(self.fos_board_num, self.port.type, ch) #switching FOS port
            if (len(active_channels)>1) or (len(self.lastActiveChannels)>1):
              time.sleep(.025)#this is how I'm implementing a switching delay
            readout=self.wavemeter.readWL() #reading wavemeter
            if wp.active_pid:
              error, voltage = wp.update_pid(readout)
              self.set_output_voltage(voltage, ch)
              # print(f"channel {ch}; error:{error}; voltage:{voltage}")
            else:
              error = readout-wp.pid.setpoint
              voltage=wp.latest_output
            # if ch==5: print(ch, readout, error, voltage)
            with self.state.lock:
              wp.latest_reading=readout
              wp.latest_error=error
              wp.latest_output = voltage
        self.lastActiveChannels = active_channels
        # now=time.time(); print("dt=",now-then); then=now
        # print(laser_logs)
      except Exception as e:
        if not self.state.running: break #exceptions are fine if app is in process of shutting down
        print("Wavemeter thread crashed:", e)
        import traceback
        traceback.print_exc()
        time.sleep(0.1)
  def close(self):
    try: self.wavemeter.tn.close()
    except: pass

class SocketServer:
  def __init__(self, state:AppState, host="0.0.0.0", port=5000):
    self.state=state
    self.host=host
    self.port=port
    self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    self.server_socket.settimeout(1)

  def handle_client(self, conn, address):
    print(f"Client connected: {address}")
    try:
      while self.state.running:
        try:
          data = conn.recv(1024)
          if not data:
            break
          command = data.decode().strip()  # Eventually, actually cater to client's request
          # print("command:", command)
          if command == "GET":
            snapshot=self.state.get_snapshot()
            laser_logs=snapshot
            # message = json.dumps(snapshot)
            message=''.join([f'{key}:{laser_logs[key]},' for key in laser_logs.keys()]).rstrip(',') + '\n'
            conn.sendall(message.encode())
          else:
            conn.sendall(b'{"error":"unknown command"}')
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