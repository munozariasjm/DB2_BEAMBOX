from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QGroupBox, QListWidget, QListWidgetItem,
                              QAbstractItemView, QCheckBox, QLabel, QSpinBox, QDoubleSpinBox, QHBoxLayout)
from PyQt5.QtCore import pyqtSignal, Qt


class PlotOptionsWidget(QWidget):
    options_changed = pyqtSignal(list)
    auto_scale_toggled = pyqtSignal(bool)
    theme_toggled = pyqtSignal(bool)  # True = Dark, False = Light
    tof_online_toggled = pyqtSignal(bool)
    tof_bins_changed = pyqtSignal(int)
    # Rate-plot signals. The MainWindow forwards these to DAQSystem
    # (integration_time_changed) and PlotWidget (rate_avg_toggled) plus
    # keeps the rolling-window value locally for the avg computation.
    integration_time_changed = pyqtSignal(float)
    rate_avg_toggled = pyqtSignal(bool)
    rate_avg_window_changed = pyqtSignal(float)

    def __init__(self, parent=None, initial_rate_settings: dict = None):
        super().__init__(parent)
        self.item_map = {}
        self._initial_rate = dict(initial_rate_settings or {})
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        grp_opts = QGroupBox("Plot Options")
        layout_opts = QVBoxLayout()
        grp_opts.setLayout(layout_opts)

        self.chk_auto_scale = QCheckBox("Lock/Auto-Scale Axes")
        self.chk_auto_scale.setChecked(True)
        self.chk_auto_scale.toggled.connect(self.auto_scale_toggled.emit)
        layout_opts.addWidget(self.chk_auto_scale)
        self.chk_theme = QCheckBox("Dark Mode")
        self.chk_theme.setChecked(False)
        self.chk_theme.toggled.connect(self.theme_toggled.emit)
        layout_opts.addWidget(self.chk_theme)

        layout_opts.addWidget(QLabel("Drag to Reorder:"))
        self.list_widget = QListWidget()
        self.list_widget.setDragDropMode(QAbstractItemView.InternalMove)
        self.list_widget.model().rowsMoved.connect(self.emit_options)
        self.list_widget.itemChanged.connect(self.emit_options)

        self.add_item('rate', "Event Rate vs Time", checked=True)
        self.add_item('scan', "Scan Results (Rate vs WN)", checked=True)
        self.add_item('laser', "Measured & Target WN vs Time", checked=False)
        self.add_item('tof', "ToF Histogram", checked=False)

        layout_opts.addWidget(self.list_widget)
        layout.addWidget(grp_opts)

        # ----- Rate Plot group -----
        grp_rate = QGroupBox("Rate Plot")
        layout_rate = QVBoxLayout()
        grp_rate.setLayout(layout_rate)

        int_row = QHBoxLayout()
        int_row.addWidget(QLabel("Integration time (s):"))
        self.spin_integration = QDoubleSpinBox()
        self.spin_integration.setRange(0.01, 60.0)
        self.spin_integration.setDecimals(3)
        self.spin_integration.setSingleStep(0.01)
        self.spin_integration.setValue(float(self._initial_rate.get("integration_time_s", 0.1)))
        self.spin_integration.setKeyboardTracking(False)
        self.spin_integration.valueChanged.connect(self.integration_time_changed.emit)
        int_row.addWidget(self.spin_integration)
        layout_rate.addLayout(int_row)

        self.chk_rate_avg = QCheckBox("Show rolling average")
        self.chk_rate_avg.setChecked(bool(self._initial_rate.get("rate_avg_enabled", False)))
        self.chk_rate_avg.toggled.connect(self._on_rate_avg_toggled)
        layout_rate.addWidget(self.chk_rate_avg)

        win_row = QHBoxLayout()
        win_row.addWidget(QLabel("Rolling window (s):"))
        self.spin_avg_window = QDoubleSpinBox()
        self.spin_avg_window.setRange(0.1, 600.0)
        self.spin_avg_window.setDecimals(2)
        self.spin_avg_window.setSingleStep(0.5)
        self.spin_avg_window.setValue(float(self._initial_rate.get("rate_avg_window_s", 0.5)))
        self.spin_avg_window.setKeyboardTracking(False)
        self.spin_avg_window.setEnabled(self.chk_rate_avg.isChecked())
        self.spin_avg_window.valueChanged.connect(self.rate_avg_window_changed.emit)
        win_row.addWidget(self.spin_avg_window)
        layout_rate.addLayout(win_row)

        layout.addWidget(grp_rate)

        # ----- ToF Settings -----
        grp_tof = QGroupBox("ToF Settings")
        layout_tof = QVBoxLayout()
        grp_tof.setLayout(layout_tof)

        self.chk_tof_online = QCheckBox("ToF Online Mode")
        self.chk_tof_online.setChecked(False)
        self.chk_tof_online.toggled.connect(self.tof_online_toggled.emit)
        layout_tof.addWidget(self.chk_tof_online)

        bins_row = QHBoxLayout()
        bins_row.addWidget(QLabel("Bins:"))
        self.spin_tof_bins = QSpinBox()
        self.spin_tof_bins.setRange(10, 1000)
        self.spin_tof_bins.setValue(50)
        self.spin_tof_bins.valueChanged.connect(self.tof_bins_changed.emit)
        bins_row.addWidget(self.spin_tof_bins)
        layout_tof.addLayout(bins_row)

        layout.addWidget(grp_tof)

    def _on_rate_avg_toggled(self, enabled):
        # Gray out the window field when the overlay is off so it's obvious
        # that the value is moot until the checkbox is on.
        self.spin_avg_window.setEnabled(enabled)
        self.rate_avg_toggled.emit(enabled)

    def add_item(self, key, text, checked=False):
        item = QListWidgetItem(text)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        item.setData(Qt.UserRole, key)
        self.list_widget.addItem(item)

    def emit_options(self):
        active_plots = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.checkState() == Qt.Checked:
                active_plots.append(item.data(Qt.UserRole))
        self.options_changed.emit(active_plots)

    def get_options(self):
        active_plots = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.checkState() == Qt.Checked:
                active_plots.append(item.data(Qt.UserRole))
        return active_plots
