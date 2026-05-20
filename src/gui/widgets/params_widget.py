from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QGridLayout, QGroupBox,
                             QLabel, QDoubleSpinBox, QComboBox, QPushButton)
from PyQt5.QtCore import pyqtSignal

class ParamsWidget(QWidget):
    settings_requested = pyqtSignal()

    def __init__(self, parent=None, settings_config=None):
        super().__init__(parent)
        self.settings_config = settings_config or {}
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # --- Parameters ---
        grp_params = QGroupBox("Scan Parameters")
        layout_params = QGridLayout()
        grp_params.setLayout(layout_params)

        defaults = self.settings_config

        # Start WN
        layout_params.addWidget(QLabel("Start WN (cm^-1):"), 0, 0)
        self.spin_start_wn = QDoubleSpinBox()
        self.spin_start_wn.setRange(0, 50000)
        self.spin_start_wn.setValue(defaults.get("start_wn", defaults.get("min_wn", 16666.0)))
        self.spin_start_wn.setDecimals(6)
        self.spin_start_wn.setSingleStep(0.000001)
        layout_params.addWidget(self.spin_start_wn, 0, 1)

        # End WN
        layout_params.addWidget(QLabel("End WN (cm^-1):"), 1, 0)
        self.spin_end_wn = QDoubleSpinBox()
        self.spin_end_wn.setRange(0, 50000)
        self.spin_end_wn.setValue(defaults.get("end_wn", defaults.get("max_wn", 16680.0)))
        self.spin_end_wn.setDecimals(6)
        self.spin_end_wn.setSingleStep(0.000001)
        layout_params.addWidget(self.spin_end_wn, 1, 1)

        # Step
        layout_params.addWidget(QLabel("Step Size (cm^-1):"), 2, 0)
        self.spin_step = QDoubleSpinBox()
        self.spin_step.setRange(0.000001, 1000)
        self.spin_step.setValue(defaults.get("step_size", 0.5))
        self.spin_step.setDecimals(6)
        self.spin_step.setSingleStep(0.000001)
        layout_params.addWidget(self.spin_step, 2, 1)

        # Stop Mode
        layout_params.addWidget(QLabel("Stop Condition:"), 3, 0)
        self.combo_mode = QComboBox()
        self.combo_mode.addItems(["Target Bunches", "Fixed Time (s)"])
        # Set default mode
        mode = defaults.get("stop_mode", "bunches")
        self.combo_mode.setCurrentIndex(0 if mode == "bunches" else 1)
        layout_params.addWidget(self.combo_mode, 3, 1)

        # Stop Value
        layout_params.addWidget(QLabel("Target Value:"), 4, 0)
        self.spin_stop_val = QDoubleSpinBox()
        self.spin_stop_val.setRange(0.1, 1000000)
        self.spin_stop_val.setValue(defaults.get("stop_val", 100))
        self.spin_stop_val.setDecimals(1)
        layout_params.addWidget(self.spin_stop_val, 4, 1)

        # Loops
        layout_params.addWidget(QLabel("Loops:"), 5, 0)
        self.spin_loops = QDoubleSpinBox() # Using Double to allow integer but re-using style if easier, or QSpinBox
        from PyQt5.QtWidgets import QSpinBox
        self.spin_loops = QSpinBox()
        self.spin_loops.setRange(1, 100)
        self.spin_loops.setValue(defaults.get("loops", 1))
        layout_params.addWidget(self.spin_loops, 5, 1)

        # Settings Button
        self.btn_settings = QPushButton("Laser Settings...")
        self.btn_settings.clicked.connect(self.settings_requested.emit)
        layout_params.addWidget(self.btn_settings, 6, 0, 1, 2) # Span 2 columns

        layout.addWidget(grp_params)

        self.param_widgets = [
            self.spin_start_wn, self.spin_end_wn, self.spin_step,
            self.combo_mode, self.spin_stop_val, self.spin_loops, self.btn_settings
        ]

    def set_enabled(self, enabled):
        for w in self.param_widgets:
            if w == self.btn_settings:
                w.setEnabled(True) # Always enabled
            else:
                w.setEnabled(enabled)

    def get_params(self):
        start_wn = self.spin_start_wn.value()
        end_wn = self.spin_end_wn.value()
        step_size = self.spin_step.value()
        stop_val = self.spin_stop_val.value()
        loops = self.spin_loops.value()

        mode_idx = self.combo_mode.currentIndex()
        stop_mode = 'bunches' if mode_idx == 0 else 'time'

        return {
            'start_wn': start_wn,
            'end_wn': end_wn,
            'step_size': step_size,
            'stop_mode': stop_mode,
            'stop_val': stop_val,
            'loops': loops,
            # For display/tooltip
            'display': {
                "Start WN": f"{start_wn:.6f} cm^-1",
                "End WN": f"{end_wn:.6f} cm^-1",
                "Step": f"{step_size:.6f} cm^-1",
                "Mode": stop_mode,
                "Value": f"{stop_val}",
                "Loops": f"{loops}"
            }
        }
