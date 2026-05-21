from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QGroupBox, QLabel,
                             QHBoxLayout, QProgressBar)
from PyQt5.QtGui import QPainter, QColor, QBrush
from PyQt5.QtCore import Qt, QSize

from src.utils.units import wn_to_nm_vacuum

class LEDIndicator(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(24, 24)
        self.color = QColor("red")

    def set_color(self, color_str):
        self.color = QColor(color_str)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QBrush(self.color))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(2, 2, 20, 20)


class StatusWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        # Display unit for the "Measured/Target" readout. Internal math
        # stays in cm⁻¹ regardless — only this label flips. Set via
        # `set_display_unit("wn"|"nm")` from MainWindow at startup; toggled
        # by the gui_settings.wavemeter_display_unit setting.
        self._display_unit = "wn"
        self.init_ui()

    def set_display_unit(self, unit: str):
        unit = (unit or "wn").lower()
        if unit not in ("wn", "nm"):
            unit = "wn"
        self._display_unit = unit
        if hasattr(self, "lbl_status_wn"):
            label = "nm" if unit == "nm" else "cm^-1"
            self.lbl_status_wn.setText(
                f"Measured: -- {label}\nTarget: -- {label}"
            )

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        grp_status = QGroupBox("Status")
        layout_status = QVBoxLayout()
        grp_status.setLayout(layout_status)

        self.lbl_progress = QLabel("Progress: Idle")
        layout_status.addWidget(self.lbl_progress)

        # LED Row
        row_led = QHBoxLayout()
        row_led.addWidget(QLabel("State:"))
        self.led = LEDIndicator()
        row_led.addWidget(self.led)
        row_led.addStretch()
        layout_status.addLayout(row_led)

        self.lbl_eta = QLabel("ETA: --")
        layout_status.addWidget(self.lbl_eta)

        # Wavemeter link row. The LED here is independent of the scan-state
        # LED above: green = poll succeeded on the last loop iteration, red
        # = the last poll raised (server unreachable / channel inactive),
        # blue = simulation mode (no real link to keep track of).
        row_wm = QHBoxLayout()
        row_wm.addWidget(QLabel("Wavemeter:"))
        self.led_wm = LEDIndicator()
        self.led_wm.set_color("gray")
        row_wm.addWidget(self.led_wm)
        self.lbl_wm = QLabel("--")
        row_wm.addWidget(self.lbl_wm)
        row_wm.addStretch()
        layout_status.addLayout(row_wm)

        # Info Icon Row
        row_info = QHBoxLayout()
        self.lbl_status_wn = QLabel("Measured: -- cm^-1\nTarget: -- cm^-1")
        font = self.lbl_status_wn.font()
        font.setBold(True)
        self.lbl_status_wn.setFont(font)
        row_info.addWidget(self.lbl_status_wn)

        row_info.addStretch()
        self.lbl_scan_info = QLabel("ⓘ")
        self.lbl_scan_info.setFixedSize(20, 20)
        self.lbl_scan_info.setAlignment(Qt.AlignCenter)
        self.lbl_scan_info.setStyleSheet("border: 1px solid gray; border-radius: 10px; color: #2196F3; font-weight: bold;")
        self.lbl_scan_info.setToolTip("No active scan")
        row_info.addWidget(self.lbl_scan_info)
        layout_status.addLayout(row_info)

        layout_status.addWidget(QLabel("Scan Progress:"))
        self.progress_bar = QProgressBar()
        layout_status.addWidget(self.progress_bar)

        layout_status.addWidget(QLabel("Bin Accumulation:"))
        self.bin_progress = QProgressBar()
        layout_status.addWidget(self.bin_progress)

        layout.addWidget(grp_status)

    def update_wavemeter_status(self, wm_status: dict):
        """Refresh the wavemeter link row from `DAQSystem.get_wavemeter_status()`.

        Four colours, mapped from the `mode`/`connected` fields:
          - blue   "SIMULATION"  — sim mode (synthetic readings).
          - orange "DISABLED"    — real run with NullWavemeterClient (server
                                   off by config or unreachable at startup).
          - green  "host:port"   — real client, last poll succeeded.
          - red    "DISCONNECTED"— real client, last poll failed.
        """
        mode = wm_status.get("mode")
        if mode is None:
            mode = "sim" if wm_status.get("simulation") else "real"

        if mode == "sim":
            self.led_wm.set_color("#4a8fff")
            self.lbl_wm.setText("SIMULATION")
            self.lbl_wm.setStyleSheet("color: #4a8fff; font-weight: bold;")
            return
        if mode == "null":
            self.led_wm.set_color("#e68a00")
            self.lbl_wm.setText("DISABLED (tagger-only)")
            self.lbl_wm.setStyleSheet("color: #e68a00; font-weight: bold;")
            return
        host = wm_status.get("host", "?")
        port = wm_status.get("port", "?")
        ch = wm_status.get("channel", "?")
        if wm_status.get("connected"):
            self.led_wm.set_color("#1faa1f")
            self.lbl_wm.setText(f"{host}:{port}  ch{ch}")
            self.lbl_wm.setStyleSheet("color: #1faa1f;")
        else:
            self.led_wm.set_color("#d11414")
            self.lbl_wm.setText(f"DISCONNECTED  {host}:{port}")
            self.lbl_wm.setStyleSheet("color: #d11414; font-weight: bold;")

    def update_status(self, daq_status, active_params_text=None):
        target_wn = daq_status['target_wn']
        measured_wn = daq_status['measured_wn']

        if self._display_unit == "nm":
            # Convert only for display. `wn_to_nm_vacuum(0)` would blow up
            # so guard the zero/idle case (target_wn=0 when no scan).
            meas_disp = wn_to_nm_vacuum(measured_wn) if measured_wn > 0 else 0.0
            tgt_disp = wn_to_nm_vacuum(target_wn) if target_wn > 0 else 0.0
            self.lbl_status_wn.setText(
                f"Measured: {meas_disp:.6f} nm\nTarget: {tgt_disp:.6f} nm"
            )
        else:
            self.lbl_status_wn.setText(
                f"Measured: {measured_wn:.6f} cm^-1\nTarget: {target_wn:.6f} cm^-1"
            )

        if active_params_text:
             self.lbl_scan_info.setToolTip(active_params_text)

        if daq_status['is_running']:
            if daq_status['is_paused']:
                self.lbl_progress.setText("Status: Paused")
            elif daq_status.get('is_stopping', False):
                self.lbl_progress.setText("Status: Stopping...")
            else:
                self.lbl_progress.setText(f"Status: Scanning Bin {daq_status['bin_index']}/{daq_status['total_bins']}")

            is_accumulating = daq_status.get('is_accumulating', False)
            if is_accumulating:
                 self.led.set_color("green") # Ingesting
                 self.lbl_progress.setText(f"Status: Ingesting (Bin {daq_status['bin_index']})")
            else:
                 self.led.set_color("yellow") # Converging / Moving
                 self.lbl_progress.setText(f"Status: Converging (Bin {daq_status['bin_index']})")

            if daq_status['eta_seconds'] > 0:
                mins = int(daq_status['eta_seconds'] // 60)
                secs = int(daq_status['eta_seconds'] % 60)
                self.lbl_eta.setText(f"ETA: {mins}m {secs}s")
            else:
                self.lbl_eta.setText("ETA: Calculating...")

            if daq_status['total_bins'] > 0:
                pct = int((daq_status['bins_completed'] / daq_status['total_bins']) * 100)
                self.progress_bar.setValue(pct)

            if daq_status['stop_value'] > 0:
                if daq_status['stop_mode'] == 'events':
                     bin_pct = (daq_status['accumulated'] / daq_status['stop_value']) * 100
                     self.bin_progress.setValue(int(min(bin_pct, 100)))
                elif daq_status['stop_mode'] == 'bunches':
                     bin_pct = (daq_status['accumulated_bunches'] / daq_status['stop_value']) * 100
                     self.bin_progress.setValue(int(min(bin_pct, 100)))
                else:
                     self.bin_progress.setValue(0)
        else:
            self.lbl_progress.setText("Status: Idle")
            self.led.set_color("red") # Off/Idle
            self.lbl_eta.setText("ETA: --")
            self.progress_bar.setValue(0)
            self.bin_progress.setValue(0)
            self.lbl_scan_info.setToolTip("No active scan")
