import time
from collections import deque
from PyQt5.QtWidgets import (QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QSplitter, QLabel)
from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtWidgets import QMessageBox
from PyQt5.QtWidgets import QFileDialog, QMessageBox
import csv
from src.utils.settings_manager import SettingsManager
from src.utils.rate_math import compute_trailing_average
from src.gui.widgets.params_widget import ParamsWidget
from src.gui.widgets.actions_widget import ActionsWidget
from src.gui.widgets.status_widget import StatusWidget
from src.gui.widgets.plot_widget import PlotWidget
from src.gui.widgets.plot_options_widget import PlotOptionsWidget
from src.gui.widgets.laser_control_dialog import LaserControlDialog
from src.gui.widgets.collapsible_box import CollapsibleBox


def _make_simulation_banner() -> QLabel:
    """Loud GUI banner shown across the top of the window in simulation mode.
    Mirrors the terminal banner emitted by DAQSystem. The colour scheme is
    deliberately garish so it cannot be confused with the rest of the UI."""
    label = QLabel(
        "⚠   SIMULATION MODE — NO REAL HARDWARE — DATA IS SYNTHETIC   ⚠"
    )
    label.setAlignment(Qt.AlignCenter)
    label.setStyleSheet(
        "QLabel {"
        " background-color: #ffcc00;"
        " color: #4a0000;"
        " font-weight: bold;"
        " font-size: 14pt;"
        " padding: 6px;"
        " border: 2px solid #b30000;"
        "}"
    )
    return label

class MainWindow(QMainWindow):
    def __init__(self, daq_system):
        super().__init__()
        self.daq = daq_system
        if hasattr(self.daq, 'config') and self.daq.config:
            self.settings_manager = SettingsManager() # We still need the manager to save
            self.settings_manager.settings = self.daq.config
        else:
            self.settings_manager = SettingsManager()

        self.scan_settings = self.settings_manager.get_section("scan_settings")

        self.setWindowTitle("DAQ Scanner Control (PyQt5) - Modular")
        self.resize(1200, 800)

        self._init_ui()
        self._init_logic()
        self.update_counter = 0

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        # Top-level vertical layout: optional simulation banner at the top,
        # then the existing horizontal splitter below. In real-hardware mode
        # the banner is simply not added — there is NO indicator at all,
        # because the absence of the warning is itself the signal.
        outer_layout = QVBoxLayout(central)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        if getattr(self.daq, "simulation_mode", False):
            self.sim_banner = _make_simulation_banner()
            outer_layout.addWidget(self.sim_banner)
            # Reflect the mode in the window title too — visible even when the
            # banner is scrolled off / window is minimised to the taskbar.
            self.setWindowTitle(self.windowTitle() + "  [SIMULATION]")

        body = QWidget()
        main_layout = QHBoxLayout(body)
        outer_layout.addWidget(body)

        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter)

        self.controls_widget = QWidget()
        self.controls_layout = QVBoxLayout(self.controls_widget)
        self.controls_layout.setContentsMargins(10, 10, 10, 10)
        self.controls_layout.setSpacing(15)

        self.params_widget = ParamsWidget(settings_config=self.scan_settings)
        self.actions_widget = ActionsWidget()
        gui_settings = self.settings_manager.get_section("gui_settings")
        self.plot_options_widget = PlotOptionsWidget(
            initial_rate_settings={
                "integration_time_s": gui_settings.get("integration_time_s", 0.1),
                "rate_avg_enabled": gui_settings.get("rate_avg_enabled", False),
                "rate_avg_window_s": gui_settings.get("rate_avg_window_s", 0.5),
            },
        )
        self.status_widget = StatusWidget()

        self.options_container = CollapsibleBox("Plot Options")
        self.options_container.set_content_widget(self.plot_options_widget)

        self.controls_layout.addWidget(self.params_widget)
        self.controls_layout.addWidget(self.actions_widget)
        self.controls_layout.addWidget(self.options_container)
        self.controls_layout.addWidget(self.status_widget)

        # Offline Mode Button
        from PyQt5.QtWidgets import QPushButton
        self.btn_offline = QPushButton("Open Offline Viewer")
        self.btn_offline.clicked.connect(self.open_offline_mode)
        self.controls_layout.addWidget(self.btn_offline)

        self.controls_layout.addStretch()

        splitter.addWidget(self.controls_widget)

        self.plot_widget = PlotWidget()
        splitter.addWidget(self.plot_widget)
        splitter.setStretchFactor(1, 4) # Plots take more space

    def _init_logic(self):
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_gui)

        gui_settings = self.settings_manager.get_section("gui_settings")
        refresh_interval = gui_settings.get("refresh_rate_ms", 100)
        self.timer.start(refresh_interval)

        self.start_time = time.time()
        self.time_history = deque(maxlen=200)
        # Rate samples live in DAQSystem.rate_samples now (decoupled from
        # the GUI refresh cadence). The GUI just reads + smooths them.
        self.wn_history = deque(maxlen=200)
        self.target_wn_history = deque(maxlen=200)
        # Voltage history dropped — multimeter removed for new-experiment refit.

        # Rate-plot live settings. Pushed into the subsystems before the
        # timer starts so the very first update_gui() honours them.
        self.rate_avg_window_s = float(gui_settings.get("rate_avg_window_s", 0.5))
        self.rate_avg_enabled = bool(gui_settings.get("rate_avg_enabled", False))
        self.daq.set_integration_time(float(gui_settings.get("integration_time_s", 0.1)))
        self.plot_widget.set_rate_avg_enabled(self.rate_avg_enabled)
        self.status_widget.set_display_unit(
            gui_settings.get("wavemeter_display_unit", "wn")
        )

        self.actions_widget.start_requested.connect(self.on_start)
        self.actions_widget.pause_requested.connect(self.on_pause)
        self.actions_widget.stop_requested.connect(self.on_stop)
        self.actions_widget.reset_requested.connect(self.on_reset)
        self.actions_widget.export_requested.connect(self.on_export)

        self.params_widget.settings_requested.connect(self.on_settings)

        self.plot_options_widget.options_changed.connect(self.plot_widget.set_active_plots)
        self.plot_options_widget.auto_scale_toggled.connect(self.plot_widget.set_auto_scale)
        self.plot_options_widget.theme_toggled.connect(self.plot_widget.set_theme)
        self.plot_options_widget.tof_online_toggled.connect(self._on_tof_online_toggled)
        self.plot_options_widget.tof_bins_changed.connect(self.plot_widget.set_tof_bins)
        self.plot_options_widget.integration_time_changed.connect(self._on_integration_time_changed)
        self.plot_options_widget.rate_avg_toggled.connect(self._on_rate_avg_toggled)
        self.plot_options_widget.rate_avg_window_changed.connect(self._on_rate_avg_window_changed)

        self.plot_widget.set_active_plots(self.plot_options_widget.get_options())
        self.plot_widget.set_auto_scale(self.plot_options_widget.chk_auto_scale.isChecked())

        self.was_running = False

    def _on_tof_online_toggled(self, enabled):
        self.daq.tof_online_mode = enabled
        self.daq.tof_buffer.clear()

    def _on_integration_time_changed(self, seconds: float):
        self.daq.set_integration_time(seconds)
        gui_settings = self.settings_manager.get_section("gui_settings")
        gui_settings["integration_time_s"] = float(seconds)
        self.settings_manager.settings["gui_settings"] = gui_settings
        self.settings_manager.save_settings()

    def _on_rate_avg_toggled(self, enabled: bool):
        self.rate_avg_enabled = bool(enabled)
        self.plot_widget.set_rate_avg_enabled(self.rate_avg_enabled)
        gui_settings = self.settings_manager.get_section("gui_settings")
        gui_settings["rate_avg_enabled"] = self.rate_avg_enabled
        self.settings_manager.settings["gui_settings"] = gui_settings
        self.settings_manager.save_settings()

    def _on_rate_avg_window_changed(self, seconds: float):
        self.rate_avg_window_s = float(seconds)
        gui_settings = self.settings_manager.get_section("gui_settings")
        gui_settings["rate_avg_window_s"] = self.rate_avg_window_s
        self.settings_manager.settings["gui_settings"] = gui_settings
        self.settings_manager.save_settings()

    def on_start(self):
        status = self.daq.scanner.get_status()
        if status['is_running']:
            QMessageBox.warning(self, "Scan Running",
                                "A scan is currently running, either pause it, or stop it.")
            return

        self.on_reset()

        params = self.params_widget.get_params()

        try:
            self.daq.start_scan(
                params['start_wn'],
                params['end_wn'],
                params['step_size'],
                params['stop_mode'],
                params['stop_val'],
                params['loops']
            )
            self.active_display_params = params['display']

            info_text = "<b>Current Scan Parameters:</b><br>"
            info_text += "<br>".join([f"• {k}: {v}" for k, v in self.active_display_params.items()])
            self.current_info_text = info_text

            self.scan_settings.update({
                'start_wn': params['start_wn'],
                'end_wn': params['end_wn'],
                'step_size': params['step_size'],
                'stop_mode': params['stop_mode'],
                'stop_val': params['stop_val'],
                'loops': params['loops']
            })
            self.settings_manager.save_settings()

        except Exception as e:
            print(f"Error starting scan: {e}")

    def on_stop(self):
        self.daq.scanner.stop(wait=True)

        if self.daq.saver:
            self.daq.saver.stop()
            self.daq.saver = None

    def on_pause(self):
        status = self.daq.scanner.get_status()
        if status['is_paused']:
            self.daq.scanner.resume()
        else:
            self.daq.scanner.pause()

    def on_reset(self):
        status = self.daq.scanner.get_status()

        if status['is_running']:
             self.daq.scanner.stop(wait=True)

        self.daq.scanner.reset()

        self.time_history.clear()
        self.daq.clear_rate_history()
        self.wn_history.clear()
        self.target_wn_history.clear()

        self.start_time = time.time()
        self.daq.event_timestamps.clear()

        self.plot_widget.rebuild_plots()

        if hasattr(self, 'current_info_text'):
            del self.current_info_text

    def on_export(self):


        scan_data = self.daq.scanner.scan_progress
        if not scan_data:
            QMessageBox.warning(self, "Warning", "No scan data.")
            return

        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", "", "CSV Files (*.csv)")
        if path:
            try:
                with open(path, 'w', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow(["Wavenumber_cm-1", "Rate_events_per_bunch", "Total_Events", "Total_Bunches"])
                    writer.writerows(scan_data)
                QMessageBox.information(self, "Success", f"Exported {len(scan_data)} points.")
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))

    def on_settings(self):
        # The new dialog blends two sources: PID parameters live under
        # control_settings.laser_pid, while channel/host/port + lock-judgment
        # tunables live under wavemeter_server. Flatten both for the dialog;
        # split them again when saving.
        control_section = self.settings_manager.get_section("control_settings")
        wm_section = self.settings_manager.get_section("wavemeter_server")
        laser_pid = control_section.get("laser_pid", {})

        flat = {}
        flat.update(laser_pid)
        flat.update({
            "tolerance_wn": wm_section.get("tolerance_wn", 1e-5),
            "poll_interval": wm_section.get("poll_interval", 0.1),
            "required_stable_samples": wm_section.get("required_stable_samples", 4),
            "wm_averaging_samples": wm_section.get("wm_averaging_samples", 5),
            "channel": wm_section.get("channel", 1),
            "host": wm_section.get("host", ""),
            "port": wm_section.get("port", ""),
        })

        dialog = LaserControlDialog(flat, self, wavemeter_client=self.daq.wavemeter)
        if dialog.exec_():
            new_settings = dialog.get_settings()
            self.daq.update_laser_settings(new_settings)

            # Persist back into the appropriate sub-sections.
            pid_keys = ("kp", "ki", "kd", "vLow", "vHigh", "gain", "offset", "continuous")
            laser_pid_new = {k: new_settings[k] for k in pid_keys if k in new_settings}
            control_section["laser_pid"] = laser_pid_new
            wm_section["channel"] = new_settings.get("channel", wm_section.get("channel", 1))
            wm_section["tolerance_wn"] = new_settings.get("tolerance_wn", wm_section.get("tolerance_wn"))
            wm_section["poll_interval"] = new_settings.get("poll_interval", wm_section.get("poll_interval"))
            wm_section["required_stable_samples"] = new_settings.get("required_stable_samples", wm_section.get("required_stable_samples"))
            wm_section["wm_averaging_samples"] = new_settings.get("wm_averaging_samples", wm_section.get("wm_averaging_samples"))
            self.settings_manager.settings["control_settings"] = control_section
            self.settings_manager.settings["wavemeter_server"] = wm_section
            self.settings_manager.save_settings()

            # Apply read_enable and pid_enable toggles to the running server.
            # Order matters: READ_OFF on the server also clears PID, so when
            # the operator wants both, enable read first; when disabling
            # everything, disable PID first to keep the transition clean.
            try:
                if new_settings.get("read_enable", True):
                    self.daq.wavemeter.enable_read()
                    if new_settings.get("pid_enable"):
                        self.daq.wavemeter.enable_pid()
                    else:
                        self.daq.wavemeter.disable_pid()
                else:
                    self.daq.wavemeter.disable_pid()
                    self.daq.wavemeter.disable_read()
            except Exception as e:
                print(f"[GUI] read/PID enable toggle failed: {e}")

            self.status_widget.update_status(self.daq.scanner.get_status(), "Laser Settings Updated.")

    def update_gui(self):
        current_time = time.time() - self.start_time

        status = self.daq.scanner.get_status()
        target_wn = status['target_wn']
        measured_wn = status['measured_wn']

        self.time_history.append(current_time)
        self.wn_history.append(measured_wn)
        self.target_wn_history.append(target_wn)

        # Pull the windowed rate samples from DAQSystem. These come at the
        # integration cadence (independent of the GUI repaint), so they
        # have their own X axis. Compute the rolling average only when the
        # overlay is enabled — otherwise pass an empty list so PlotWidget
        # clears the avg curve.
        rate_times, rate_values = self.daq.get_rate_history()
        if self.rate_avg_enabled and rate_times:
            rate_avg = compute_trailing_average(rate_times, rate_values, self.rate_avg_window_s)
        else:
            rate_avg = []

        if hasattr(self, 'current_info_text') and status['is_running']:
            info_text = self.current_info_text
        else:
            params = self.params_widget.get_params()
            display_params = params['display']
            info_text = "<b>Pending Scan Parameters:</b><br>"
            info_text += "<br>".join([f"• {k}: {v}" for k, v in display_params.items()])

        self.status_widget.update_status(status, info_text)
        self.status_widget.update_wavemeter_status(self.daq.get_wavemeter_status())

        self.actions_widget.update_state(status['is_running'], status['is_paused'])

        self.params_widget.set_enabled(not status['is_running'])

        # Throttling ToF Updates
        self.update_counter += 1
        tof_data = None

        if self.update_counter % 10 == 0:
             tof_data = self.daq.tof_buffer if hasattr(self.daq, 'tof_buffer') else []

        history = {
            'times': list(self.time_history),
            'rate_times': rate_times,
            'rate': rate_values,
            'rate_avg': rate_avg,
            'wn': list(self.wn_history),
            'target_wn': list(self.target_wn_history),
            'scan_data': self.daq.scanner.scan_progress,
            'tof_buffer': tof_data
        }
        self.plot_widget.update_plots(history)

        # Detect Scan Completion
        if self.was_running and not status['is_running']:
            if not status['is_stopping']: # Natural Completion
                msg = "Scan Complete Successfully!"
                if self.daq.last_scan_filename:
                   msg += f"\n\nData saved to:\n{self.daq.last_scan_filename}"

                QMessageBox.information(self, "Scan Finished", msg)

        self.was_running = status['is_running']


    def closeEvent(self, event):
        params = self.params_widget.get_params()
        self.scan_settings.update({
            'start_wn': params['start_wn'],
            'end_wn': params['end_wn'],
            'step_size': params['step_size'],
            'stop_mode': params['stop_mode'],
            'stop_val': params['stop_val'],
            'loops': params['loops']
        })
        self.settings_manager.save_settings()

        self.daq.stop()
        event.accept()

    def open_offline_mode(self):
        from src.gui.offline_window import OfflineWindow
        self.offline_window = OfflineWindow()
        self.offline_window.show()

