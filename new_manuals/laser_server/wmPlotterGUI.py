import pyqtgraph as pg
import numpy as np
import PyQt6 as qt
from PyQt6 import QtWidgets, QtCore
from PyQt6.QtGui import QIcon
import sys
from urllib.request import urlopen
import time
import os
from client_class import wavemeterClient, dummyWavemeter
from SinglePortViewer import SinglePortViewer
import threading

class MainGUI(QtWidgets.QMainWindow):
  def __init__(self, wavemeter, watch_list=[], colorList=[], maxLength=20000):
    super().__init__()
    self.cw=QtWidgets.QWidget(); self.setCentralWidget(self.cw) #create and set central widget
    self.verticalLayout = QtWidgets.QVBoxLayout(); self.cw.setLayout(self.verticalLayout) #create horizontal layout, add to central widget
    self.gridLayout=QtWidgets.QGridLayout(); self.verticalLayout.addLayout(self.gridLayout)
    if type(wavemeter) == list: self.wavemeter_list=wavemeter
    else: self.wavemeter_list=[wavemeter]
    self.fos_port_list=list(wavemeter.data.keys())[1:]#[i for i in range(1,len(wavemeter.data.keys()))] #update for case of multiple wavemeters
    self.data={port:{"Times":[],"Wavelengths":[]} for port in self.fos_port_list}
    self.watching={port:False for port in self.fos_port_list}
    for port in watch_list:
      self.watching[port]=True
    self.colorList=colorList #TODO: make this a cmap?
    self.maxLength=maxLength
    self.makePortViewers()
    self.timer = QtCore.QTimer()
    self.timer.timeout.connect(self.update)
    self.timer.start(1)

  def makePortViewers(self):
    self.portViewers={}
    self.unviewedPorts=[]
    i=0
    for fos_port in self.fos_port_list:
      if not self.watching[fos_port]: self.unviewedPorts+=[fos_port];continue
      self.addPortViewer(fos_port)
    if len(self.unviewedPorts)>0: self.addAddButton()

  def addAddButton(self):
    i=len(self.portViewers.keys())
    if len(self.unviewedPorts)>0:
      row=i%2
      col=i//2
      self.addViewerLayout=QtWidgets.QVBoxLayout()
      self.addComboBox=QtWidgets.QComboBox(); self.addViewerLayout.addWidget(self.addComboBox)
      for port in self.unviewedPorts:
            self.addComboBox.addItem(f"Port {port}")
      self.addViewerButton=QtWidgets.QPushButton("Add Port")
      self.addViewerButton.clicked.connect(lambda: self.addViewerButtonAction())
      self.addViewerLayout.addWidget(self.addViewerButton)
      self.gridLayout.addLayout(self.addViewerLayout, row, col)

  def addPortViewer(self, fos_port):
    i=len(self.portViewers.keys())
    row=i%2
    col=i//2
    self.portViewers[fos_port]=SinglePortViewer(fos_port=fos_port, label=f'Port {fos_port}', color=self.colorList[fos_port-1]
      , data=[self.data[fos_port][key] for key in ["Times", "Wavelengths"]], maxLength=self.maxLength)
    self.gridLayout.addLayout(self.portViewers[fos_port].layout, row, col)
    # self.portViewers[fos_port].closeButton.clicked.connect((lambda x: lambda: self.closePortViewer(x))(fos_port))#stupid syntax to avoid binding to reference
    self.portViewers[fos_port].closeButton.clicked.connect(lambda: self.closePortViewer(fos_port))

  def addViewerButtonAction(self):
    fos_port = self.unviewedPorts[self.addComboBox.currentIndex()]
    try: toPop=self.unviewedPorts.index(fos_port)
    except: print("trying to add an already viewed port"); return
    self.unviewedPorts.pop(toPop)
    self.watching[fos_port] = True
    print('port:', fos_port)
    self.addPortViewer(fos_port)
    if len(self.unviewedPorts)>0: self.addAddButton()

  def update(self):
    self.readout={}
    for wavemeter in self.wavemeter_list:
      self.readout.update(wavemeter.data)
    for fos_port in self.fos_port_list:
      if fos_port not in self.readout.keys(): continue
      if self.readout[fos_port]==0: continue#print("huh?", self.readout);continue #0 indicates a failed reading, but blows up as a frequency
      if len(self.data[fos_port]['Times'])>0 and self.readout["time"]==self.data[fos_port]['Times'][-1]:continue #ignore duplicate reading
      self.data[fos_port]['Times']+=[self.readout["time"]]
      self.data[fos_port]['Wavelengths']+=[self.readout[fos_port ]]
      if len(self.data[fos_port]['Times'])>self.maxLength:
        self.data[fos_port]['Times'].pop(0)
        self.data[fos_port]['Wavelengths'].pop(0)
      if self.watching[fos_port]:
        self.portViewers[fos_port].addData(self.readout["time"], self.readout[fos_port])

  def close_thing(self,thing):
    # print(thing)
    if not isinstance(thing, QtWidgets.QLayout): thing.widget().deleteLater();return
    while thing.count():
      child=thing.takeAt(0)
      self.close_thing(child)
      thing.removeItem(child)

    thing.deleteLater();

  def closePortViewer(self, port):
    print('port:',port);
    # self.close_thing(self.portViewers[port].layout)
    # del self.portViewers[port]
    # self.watching[port]=False
    for fos_port in self.portViewers.keys():
      self.close_thing(self.portViewers[fos_port].layout)
      #del self.portViewers[fos_port]
    # self.addViewerButton.deleteLater()
    if len(self.unviewedPorts)>0:self.close_thing(self.addViewerLayout)
    self.watching[port]=False
    self.unviewedPorts+=[port]
    self.makePortViewers()
    return

  def safeExit(self):
    for wm in self.wavemeter_list:
      wm.stop()

def main_old():
  print("Starting GUI")
  try:
    # wmc=wavemeterClient("10.54.6.173", 5000)
    wmc=wavemeterClient("10.54.6.156", 5000)
    wmc.start(); print("client running")
  except:
    wmc=dummyWavemeter(num_ports=8)
  print(wmc.data)
  app = QtWidgets.QApplication(sys.argv)
  window = MainGUI(wmc, watch_list=[*range(1,9)], colorList=4*['blue','orange','red','magenta'])
  app.aboutToQuit.connect(window.safeExit)
  window.show()
  sys.exit(app.exec())
  wmc.stop()

def main():
    print("GUI main() entered")

    try:
        wmc = wavemeterClient("10.54.6.173", 5000)
        wmc.start()
        print("client running")
    except Exception as e:
        print("Client failed:", e)
        wmc = dummyWavemeter(num_ports=8)

    print("Creating QApplication")
    app = QtWidgets.QApplication(sys.argv)

    print("Creating window")
    window = MainGUI(
        wmc,
        watch_list=[*range(1,9)],
        colorList=4*['blue','orange','red','magenta']
    )

    print("Showing window")
    window.show()

    print("Entering event loop")
    sys.exit(app.exec())

if __name__ == '__main__':
  main_old()