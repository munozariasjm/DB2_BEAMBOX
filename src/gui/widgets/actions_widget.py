from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QGroupBox, QPushButton,
                             QMessageBox, QFileDialog)
from PyQt5.QtCore import pyqtSignal

class ActionsWidget(QWidget):
    start_requested = pyqtSignal()
    pause_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    reset_requested = pyqtSignal()
    export_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        grp_actions = QGroupBox("Actions")
        layout_actions = QVBoxLayout()
        grp_actions.setLayout(layout_actions)

        self.btn_start = QPushButton("Start Scan")
        self.btn_start.clicked.connect(self.start_requested.emit)
        self.btn_start.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        layout_actions.addWidget(self.btn_start)

        self.btn_pause = QPushButton("Pause Scan")
        self.btn_pause.clicked.connect(self.pause_requested.emit)
        self.btn_pause.setEnabled(False)
        layout_actions.addWidget(self.btn_pause)

        self.btn_stop = QPushButton("Stop Scan")
        self.btn_stop.clicked.connect(self.stop_requested.emit)
        self.btn_stop.setEnabled(False)
        self.btn_stop.setStyleSheet("background-color: #9E9E9E; color: white;")
        layout_actions.addWidget(self.btn_stop)

        self.btn_reset = QPushButton("Reset Scan")
        self.btn_reset.clicked.connect(self.reset_requested.emit)
        self.btn_reset.setToolTip("Reset plots and scan history")
        layout_actions.addWidget(self.btn_reset)

        self.btn_export = QPushButton("Export Histogram CSV")
        self.btn_export.clicked.connect(self.export_requested.emit)
        layout_actions.addWidget(self.btn_export)

        layout.addWidget(grp_actions)

    def update_state(self, is_running, is_paused):
        if is_running:
            self.btn_start.setStyleSheet("background-color: #FF9800; color: white; font-weight: bold;")
            self.btn_start.setEnabled(True) # Keep enabled to show pop-up

            self.btn_stop.setEnabled(True)
            self.btn_stop.setStyleSheet("background-color: #FF0000; color: white; font-weight: bold;")

            self.btn_pause.setEnabled(True)

            if is_paused:
                self.btn_pause.setText("Resume Scan")
            else:
                self.btn_pause.setText("Pause Scan")
        else:
            self.btn_start.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
            self.btn_start.setEnabled(True)

            self.btn_stop.setEnabled(False)
            self.btn_stop.setStyleSheet("background-color: #9E9E9E; color: white;")

            self.btn_pause.setEnabled(False)
            self.btn_pause.setText("Pause Scan")
            self.btn_start.setEnabled(True)
            self.btn_stop.setEnabled(False)
            self.btn_pause.setEnabled(False)
            self.btn_pause.setText("Pause Scan")
