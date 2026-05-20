import socket
import time
import threading

class wavemeterClient():
    def __init__(self, host, port):
        self.host=host; self.port=port
        self.client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.client_socket.settimeout(1)
        self.client_socket.connect((self.host, self.port))
        self.leftover=''
        self.lastTimeStamp=-1
        self.reading=False
        self.data = {}

    def make_query(self):
        new_data={}
        dt=0
        t0=time.perf_counter()
        # client_socket.send(message.encode())  # Send the message
        request='GET'.encode()
        self.client_socket.send(request);
        self.message=self.leftover
        while "\n" not in self.message:
            packet=self.client_socket.recv(4).decode()
            self.message+=packet
        self.message,self.leftover = self.message.split('\n',1)
        self.message=self.message.split(",")
        for kvp in self.message:
            key,value = kvp.split(":")
            # self.data[key]=value
            if key=="time": new_data[key]=float(value)
            else: new_data[int(key)]=float(value)
            # if key=="time": self.data[key]=float(value)
            # else: self.data[int(key)]=float(value)
        self.lastTimeStamp=new_data["time"]
        # self.lastTimeStamp=self.data["time"]
        self.data = new_data
        # dt+=time.perf_counter()-t0
        # print("time elapsed:", dt)
        # return(self.data)

    def continuous_readout(self):
      while self.reading: self.make_query()
    def start(self):
      self.reading=True
      self.make_query()
      self.readout_thread=threading.Thread(target=self.continuous_readout)
      self.readout_thread.start()
    def stop(self):
      self.reading=False
      self.readout_thread.join()
      self.data={}

class dummyWavemeter():
  def __init__(self, num_ports=1):
    self.num_ports=num_ports
    self.start()

  def make_query(self):
    self.data={"time":time.time()}
    for key in range(1,self.num_ports+1):
      self.data[key] = -1

  def continuous_readout(self):
    while self.reading:
      self.make_query()
      time.sleep(.03)

  def start(self):
      self.reading=True
      self.make_query()
      self.readout_thread=threading.Thread(target=self.continuous_readout)
      self.readout_thread.start()

  def stop(self):
      self.reading=False
      self.readout_thread.join()
      self.data={}


if __name__ == '__main__':
    import numpy as np
    import matplotlib.pyplot as plt
    import pyqtgraph as pg
    from pyqtgraph.Qt import QtCore
    # wmc=wavemeterClient("10.54.6.173", 5000)
    wmc=wavemeterClient("10.54.6.156", 5000)
    wmc.start(); print("client running")
    for i in range(100):
        print(wmc.data)
        # print(wmc.data[2])
        time.sleep(0.01)
    # wmc.stop()
    # print("this", wmc.data)
    # quit()
    app = pg.mkQApp("Wavemeter Logger")
    win = pg.GraphicsLayoutWidget(show=True, title="Wavemeter Readouts")
    win.resize(1000,600)
    p1=win.addPlot(title="Port 2")
    data=[]
    curve = p1.plot(pen="y")
    def update():
        global data
        data+=[wmc.data[1]]
        if len(data)>1000:data.pop(0)
        curve.setData(data)

    timer = QtCore.QTimer()
    timer.timeout.connect(update)
    timer.start(1)
    pg.exec()
    quit()