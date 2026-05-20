"""Laser control settings dialog for the wavemeter-server PID stack.

The wavemeter server runs the actual PID and drives the laser via an analog
voltage. From here the operator can:

  - push PID parameters (kp / ki / kd) to the server
  - set the voltage clamp + gain/offset that turn PID output into output volts
  - tune client-side lock judgment (tolerance in cm⁻¹, poll cadence, sample count)
  - flip server-side PID on/off
  - sync the dialog fields from the server's current STATUS

Returned `get_settings()` is a single flat dict — DAQSystem.update_laser_settings
splits it into the lock-judgment vs. PID buckets and forwards each.
"""

from PyQt5.QtCore import QPoint
from PyQt5.QtGui import QCursor
from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)


PARAM_HELP = {
    "kp": "Proportional gain pushed to the server's PID.",
    "ki": "Integral gain pushed to the server's PID.",
    "kd": "Derivative gain pushed to the server's PID.",
    "vLow": "Lower voltage clamp on the server's analog out (V).",
    "vHigh": "Upper voltage clamp on the server's analog out (V).",
    "gain": "PID-output → voltage scale (V per unit pid_output).",
    "offset": "PID-output → voltage offset (V at zero pid_output).",
    "tolerance_wn": "Max |wavemeter − target| (cm⁻¹) that counts as on-target.",
    "poll_interval": "Seconds between client-side wavemeter polls.",
    "required_stable_samples": "Consecutive in-tolerance reads required to call the laser locked.",
    "wm_averaging_samples": "Rolling-average window on wavemeter reads.",
    "channel": "Wavemeter port (1-indexed) used as the lock reference.",
    "continuous": "Keep the lock active after the first success.",
    "pid_enable": "Toggle the server's PID on/off for this channel (READ_ON is implied).",
    "read_enable": "Toggle the server's per-channel wavemeter polling on/off (READ_ON / READ_OFF).",
}


def _help_button(text: str) -> QToolButton:
    btn = QToolButton()
    btn.setText("?")
    btn.setAutoRaise(True)
    btn.setToolTip(text)
    btn.setStyleSheet(
        "QToolButton { color: #4a6fa5; font-weight: bold; padding: 0 4px; }"
    )

    def show_help():
        QToolTip.showText(QCursor.pos() + QPoint(8, 8), text, btn)

    btn.clicked.connect(show_help)
    return btn


def _labeled(text: str, key: str) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    lay.addWidget(QLabel(text))
    lay.addWidget(_help_button(PARAM_HELP[key]))
    lay.addStretch(1)
    return w


class LaserControlDialog(QDialog):
    def __init__(self, current_settings: dict, parent=None, wavemeter_client=None):
        """current_settings is the flat dict; wavemeter_client (optional) is
        passed in so the Sync button can call client.get_status() — the
        dialog reaches across to read live PID values from the server."""
        super().__init__(parent)
        self.setWindowTitle("Laser Control (Server-PID)")
        self.resize(440, 560)
        self.settings = dict(current_settings)
        self._client = wavemeter_client

        layout = QVBoxLayout(self)

        # --- PID parameters (pushed to server) ---
        pid_group = QGroupBox("Server PID parameters")
        pid_form = QFormLayout()

        self.kp_spin = self._make_double(self.settings.get("kp", 1.0), decimals=6, step=0.01, lo=-1e6, hi=1e6)
        pid_form.addRow(_labeled("kp", "kp"), self.kp_spin)

        self.ki_spin = self._make_double(self.settings.get("ki", 0.0), decimals=6, step=0.01, lo=-1e6, hi=1e6)
        pid_form.addRow(_labeled("ki", "ki"), self.ki_spin)

        self.kd_spin = self._make_double(self.settings.get("kd", 0.0), decimals=6, step=0.001, lo=-1e6, hi=1e6)
        pid_form.addRow(_labeled("kd", "kd"), self.kd_spin)

        self.vLow_spin = self._make_double(self.settings.get("vLow", -5.0), decimals=3, step=0.1, lo=-10.0, hi=10.0)
        pid_form.addRow(_labeled("vLow (V)", "vLow"), self.vLow_spin)

        self.vHigh_spin = self._make_double(self.settings.get("vHigh", 5.0), decimals=3, step=0.1, lo=-10.0, hi=10.0)
        pid_form.addRow(_labeled("vHigh (V)", "vHigh"), self.vHigh_spin)

        self.gain_spin = self._make_double(self.settings.get("gain", 10.0), decimals=6, step=0.01, lo=-1e6, hi=1e6)
        pid_form.addRow(_labeled("gain (V/unit)", "gain"), self.gain_spin)

        self.offset_spin = self._make_double(self.settings.get("offset", 0.0), decimals=3, step=0.01, lo=-10.0, hi=10.0)
        pid_form.addRow(_labeled("offset (V)", "offset"), self.offset_spin)

        pid_group.setLayout(pid_form)
        layout.addWidget(pid_group)

        # --- Lock judgment (client-side) ---
        lock_group = QGroupBox("Lock judgment (client-side)")
        lock_form = QFormLayout()

        self.tol_spin = self._make_double(self.settings.get("tolerance_wn", 1e-5), decimals=7, step=1e-6, lo=1e-7, hi=1.0)
        lock_form.addRow(_labeled("Tolerance (cm⁻¹)", "tolerance_wn"), self.tol_spin)

        self.poll_spin = self._make_double(self.settings.get("poll_interval", 0.1), decimals=3, step=0.01, lo=0.001, hi=5.0)
        lock_form.addRow(_labeled("Poll interval (s)", "poll_interval"), self.poll_spin)

        self.stable_spin = QSpinBox()
        self.stable_spin.setRange(1, 50)
        self.stable_spin.setValue(int(self.settings.get("required_stable_samples", 4)))
        lock_form.addRow(_labeled("Stable samples", "required_stable_samples"), self.stable_spin)

        self.wm_avg_spin = QSpinBox()
        self.wm_avg_spin.setRange(1, 200)
        self.wm_avg_spin.setValue(int(self.settings.get("wm_averaging_samples", 5)))
        lock_form.addRow(_labeled("WM averaging samples", "wm_averaging_samples"), self.wm_avg_spin)

        lock_group.setLayout(lock_form)
        layout.addWidget(lock_group)

        # --- Wavemeter address/channel ---
        wm_group = QGroupBox("Wavemeter")
        wm_form = QFormLayout()

        self.channel_spin = QSpinBox()
        self.channel_spin.setRange(1, 8)
        self.channel_spin.setValue(int(self.settings.get("channel", 1)))
        wm_form.addRow(_labeled("Channel (1-indexed)", "channel"), self.channel_spin)

        host_display = QLineEdit(str(self.settings.get("host", "")))
        host_display.setReadOnly(True)
        wm_form.addRow("Host (settings.json)", host_display)
        port_display = QLineEdit(str(self.settings.get("port", "")))
        port_display.setReadOnly(True)
        wm_form.addRow("Port (settings.json)", port_display)

        wm_group.setLayout(wm_form)
        layout.addWidget(wm_group)

        # --- Mode + sync ---
        toggles = QHBoxLayout()
        # READ_ON is a prerequisite for PID_ON, so the dialog defaults this
        # checked. Unchecking it sends READ_OFF which also disables PID
        # server-side.
        self.read_enable_check = QCheckBox("Enable read")
        self.read_enable_check.setChecked(bool(self.settings.get("read_enable", True)))
        self.read_enable_check.setToolTip(PARAM_HELP["read_enable"])
        toggles.addWidget(self.read_enable_check)

        self.pid_enable_check = QCheckBox("Enable server PID")
        self.pid_enable_check.setChecked(bool(self.settings.get("pid_enable", False)))
        self.pid_enable_check.setToolTip(PARAM_HELP["pid_enable"])
        toggles.addWidget(self.pid_enable_check)

        self.continuous_check = QCheckBox("Continuous hold")
        self.continuous_check.setChecked(bool(self.settings.get("continuous", False)))
        self.continuous_check.setToolTip(PARAM_HELP["continuous"])
        toggles.addWidget(self.continuous_check)

        toggles.addStretch(1)
        self.sync_button = QPushButton("Sync from server")
        self.sync_button.setToolTip(
            "Read kp/ki/kd/setpoint/etc from the server's STATUS reply\n"
            "and replace the fields above. Requires a live wavemeter client."
        )
        self.sync_button.clicked.connect(self._sync_from_server)
        toggles.addWidget(self.sync_button)
        layout.addLayout(toggles)

        # OK / Cancel
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _make_double(value, decimals=3, step=0.01, lo=-1e6, hi=1e6) -> QDoubleSpinBox:
        sb = QDoubleSpinBox()
        sb.setDecimals(decimals)
        sb.setSingleStep(step)
        sb.setRange(lo, hi)
        sb.setValue(float(value))
        return sb

    def _sync_from_server(self):
        if self._client is None:
            QToolTip.showText(QCursor.pos(), "No client wired into this dialog.", self.sync_button)
            return
        try:
            status = self._client.get_status()
        except Exception as e:
            QToolTip.showText(QCursor.pos(), f"STATUS failed: {e}", self.sync_button)
            return
        for key, spin in (
            ("kp", self.kp_spin), ("ki", self.ki_spin), ("kd", self.kd_spin),
            ("vLow", self.vLow_spin), ("vHigh", self.vHigh_spin),
            ("gain", self.gain_spin), ("offset", self.offset_spin),
        ):
            if key in status:
                spin.setValue(float(status[key]))
        if "active_pid" in status:
            self.pid_enable_check.setChecked(bool(float(status["active_pid"])))

    def get_settings(self) -> dict:
        return {
            # PID (sent to server)
            "kp": self.kp_spin.value(),
            "ki": self.ki_spin.value(),
            "kd": self.kd_spin.value(),
            "vLow": self.vLow_spin.value(),
            "vHigh": self.vHigh_spin.value(),
            "gain": self.gain_spin.value(),
            "offset": self.offset_spin.value(),
            # Lock judgment (client-side)
            "tolerance_wn": self.tol_spin.value(),
            "poll_interval": self.poll_spin.value(),
            "required_stable_samples": self.stable_spin.value(),
            "wm_averaging_samples": self.wm_avg_spin.value(),
            # Mode
            "continuous": self.continuous_check.isChecked(),
            "pid_enable": self.pid_enable_check.isChecked(),
            "read_enable": self.read_enable_check.isChecked(),
            # Channel
            "channel": self.channel_spin.value(),
        }
