from PyQt5.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QPushButton, QLabel, QFileDialog, QSplitter, QMessageBox)
from PyQt5.QtCore import Qt
from src.gui.widgets.plot_widget import PlotWidget
from src.gui.widgets.plot_options_widget import PlotOptionsWidget
from src.gui.widgets.collapsible_box import CollapsibleBox
from src.utils.data_loader import DataLoader
import os

class OfflineWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DAQ Scanner - Offline Mode")
        self.resize(1200, 800)

        self.loader = DataLoader()
        self.loaded_metadata = None
        self.loaded_data = None

        self._init_ui()

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        # --- Top Bar ---
        top_bar = QHBoxLayout()

        self.btn_load = QPushButton("Load Scan (JSON)")
        self.btn_load.clicked.connect(self.load_scan)
        self.btn_load.setStyleSheet("font-size: 14px; padding: 8px;")

        self.lbl_info = QLabel("No Scan Loaded")
        self.lbl_info.setStyleSheet("font-size: 14px; font-weight: bold; color: #555;")

        self.lbl_status = QLabel("[OFFLINE MODE]")
        self.lbl_status.setStyleSheet("font-size: 14px; font-weight: bold; color: red;")

        top_bar.addWidget(self.btn_load)
        top_bar.addWidget(self.lbl_info)
        top_bar.addStretch()
        top_bar.addWidget(self.lbl_status)

        main_layout.addLayout(top_bar)

        # --- Main Content Splitter ---
        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter)

        # --- Left Control Panel ---
        controls_widget = QWidget()
        controls_layout = QVBoxLayout(controls_widget)
        controls_layout.setContentsMargins(0,0,0,0)

        self.plot_options_widget = PlotOptionsWidget()
        options_box = CollapsibleBox("Plot Options")
        options_box.set_content_widget(self.plot_options_widget)

        controls_layout.addWidget(options_box)
        controls_layout.addStretch()

        splitter.addWidget(controls_widget)

        # --- Right/Main Plot Area ---
        self.plot_widget = PlotWidget()
        splitter.addWidget(self.plot_widget)

        splitter.setStretchFactor(1, 4)

        # Connect Signals
        self.plot_options_widget.options_changed.connect(self.plot_widget.set_active_plots)
        self.plot_options_widget.auto_scale_toggled.connect(self.plot_widget.set_auto_scale)
        self.plot_options_widget.theme_toggled.connect(self.plot_widget.set_theme)

        # Initialize Defaults
        self.plot_widget.set_active_plots(self.plot_options_widget.get_options())

    def load_scan(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open Scan Metadata", "data", "JSON Files (*_meta.json)")
        if not path:
            return

        try:
            self.loaded_metadata, self.loaded_data = self.loader.load_scan(path)
            self.update_ui_with_data()
            QMessageBox.information(self, "Success", "Scan loaded successfully.")
        except Exception as e:
            QMessageBox.critical(self, "Error Loading Scan", str(e))

    def update_ui_with_data(self):
        if not self.loaded_metadata or not self.loaded_data:
            return

        # Update Info Label
        ts = self.loaded_metadata.get('timestamp', 'Unknown Time')
        params = self.loaded_metadata.get('scan_parameters', {})
        loop_info = f"{params.get('loops_completed', '?')}/{params.get('loops', '?')} Loops"
        self.lbl_info.setText(f"Scan from: {ts} | {loop_info}")

        # Update Plots
        self.plot_widget.update_plots(self.loaded_data)
