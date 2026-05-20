from PyQt5.QtWidgets import QWidget, QVBoxLayout, QSplitter, QSizePolicy
from PyQt5.QtCore import Qt
import pyqtgraph as pg
import numpy as np

class WavenumberAxis(pg.AxisItem):
    """Axis that shows wavenumber tick labels with extra decimal precision."""
    def tickStrings(self, values, scale, spacing):
        if spacing > 0.1:
            fmt = "{:.3f}"
        elif spacing > 0.01:
            fmt = "{:.4f}"
        elif spacing > 0.001:
            fmt = "{:.5f}"
        else:
            fmt = "{:.6f}"
        return [fmt.format(v * scale) for v in values]


class PlotWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.plot_items = {} # Map key -> PlotItem
        self.curves = {}     # Map key -> PlotDataItem (or list of them)
        self.active_options = ['rate', 'scan']
        self.auto_scale = True
        self.is_dark_mode = False
        self.tof_bins = 50
        # Rolling-average overlay on the rate plot. Toggled by the GUI's
        # Plot Options sidebar. Stays off until the operator opts in so a
        # fresh start shows only the raw curve.
        self.show_rate_avg = False

        self.set_theme(False, layout_rebuild=False) # Init theme first

        self.init_ui()

    def set_rate_avg_enabled(self, enabled: bool):
        """Show/hide the rolling-average overlay on the 'rate' plot. Safe to
        call before the plot has been built — the visibility flag is
        applied on the next `rebuild_plots`."""
        self.show_rate_avg = bool(enabled)
        avg = self.curves.get('rate_avg')
        if avg is not None:
            avg.setVisible(self.show_rate_avg)

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        self.splitter = QSplitter(Qt.Vertical)
        main_layout.addWidget(self.splitter)

        self.rebuild_plots()

    def set_active_plots(self, options):
        self.active_options = options
        self.rebuild_plots()

    def set_tof_bins(self, n):
        self.tof_bins = n

    def set_auto_scale(self, enabled):
        self.auto_scale = enabled
        for key, p in self.plot_items.items():
            if self.auto_scale:
                p.enableAutoRange(axis=pg.ViewBox.XYAxes)
            else:
                 p.disableAutoRange()

    def set_theme(self, is_dark, layout_rebuild=True):
        self.is_dark_mode = is_dark
        bg_color = 'k' if is_dark else 'w'
        fg_color = '#d5d5d5' if is_dark else 'k'

        pg.setConfigOption('background', bg_color)
        pg.setConfigOption('foreground', fg_color)
        pg.setConfigOptions(antialias=True)

        if layout_rebuild:
            self.rebuild_plots()

    def rebuild_plots(self):
        while self.splitter.count():
            w = self.splitter.widget(0)
            w.setParent(None)
            w.deleteLater()

        self.plot_items = {}
        self.curves = {}

        if isinstance(self.active_options, dict):
            active_list = []
            if self.active_options.get('rate'): active_list.append('rate')
            if self.active_options.get('scan'): active_list.append('scan')
            if self.active_options.get('laser'): active_list.append('laser')
            if self.active_options.get('tof'): active_list.append('tof')
            self.active_options = active_list

        if not self.active_options:
            return

        pen_color = 'g' if self.is_dark_mode else 'g'

        for key in self.active_options:
            if key == 'scan':
                wn_axis = WavenumberAxis(orientation='bottom')
                pw = pg.PlotWidget(axisItems={'bottom': wn_axis})
            else:
                pw = pg.PlotWidget()
            pw.setMinimumHeight(30)

            self.splitter.addWidget(pw)
            p = pw.getPlotItem()

            self.plot_items[key] = p

            if key == 'rate':
                p.setTitle("Total Event Rate")
                p.setLabel('left', "Events/Bunch")
                p.setLabel('bottom', "Time", units='s')
                p.getAxis('left').enableAutoSIPrefix(False)
                p.showGrid(x=True, y=True)
                p.addLegend()
                curve = p.plot(pen=pg.mkPen(pen_color, width=2), name="Raw")
                self.curves['rate'] = curve
                # Overlay curve: solid red, thicker, drawn on top of raw.
                # Visibility tracks `self.show_rate_avg` so toggling the
                # checkbox in Plot Options is instant — no rebuild needed.
                avg_pen = pg.mkPen('r', width=2.5, style=pg.QtCore.Qt.SolidLine)
                curve_avg = p.plot(pen=avg_pen, name="Rolling avg")
                curve_avg.setVisible(self.show_rate_avg)
                self.curves['rate_avg'] = curve_avg

            elif key == 'scan':
                p.setTitle("Scan Results: Events/Bin")
                p.setLabel('bottom', "Wavenumber", units='cm^-1')
                p.setLabel('left', "Rate", units='Events/Bunch')
                p.getAxis('left').enableAutoSIPrefix(False)
                p.getAxis('bottom').enableAutoSIPrefix(False)
                p.showGrid(x=True, y=True)

                color_scan = 'b' if self.is_dark_mode else 'b'
                curve = p.plot(pen=pg.mkPen(color_scan, width=2), symbol='o', symbolSize=5, symbolBrush=color_scan, symbolPen=None)

                cursor = pg.InfiniteLine(pos=0, angle=90, pen=pg.mkPen('#FFA500', width=3, style=pg.QtCore.Qt.DashLine), label='Target')
                p.addItem(cursor)

                self.curves['scan'] = curve
                self.curves['scan_cursor'] = cursor

            elif key == 'laser':
                p.setTitle("Wavenumber vs Time")
                p.setLabel('left', "Wavenumber", units='cm^-1')
                p.setLabel('bottom', "Time", units='s')
                p.getAxis('left').enableAutoSIPrefix(False)
                p.showGrid(x=True, y=True)
                p.addLegend()

                curve_curr = p.plot(pen=pg.mkPen('r', width=2), name='Measured')
                curve_target = p.plot(pen=pg.mkPen('#FFA500', width=3, style=pg.QtCore.Qt.DashLine), name='Target')

                self.curves['laser_curr'] = curve_curr
                self.curves['laser_target'] = curve_target

            elif key == 'tof':
                p.setTitle("ToF Histogram")
                p.setLabel('bottom', "ToF")
                p.setLabel('left', "Density")
                p.showGrid(x=True, y=True)
                hist_pen = 'w' if self.is_dark_mode else 'k'
                brush_color = (255, 255, 255, 50) if self.is_dark_mode else (0, 0, 0, 50)
                curve = p.plot(stepMode=True, fillLevel=0, fillOutline=True, brush=brush_color, pen=hist_pen)
                self.curves['tof'] = curve

        self.set_auto_scale(self.auto_scale)

    def update_plots(self, history):
        times = history.get('times', [])
        if len(times) == 0: return

        if 'rate' in self.curves:
            # The rate plot has its own X axis (samples come at the DAQ's
            # integration cadence, not the GUI repaint cadence). Use the
            # caller-supplied `rate_times` if present; otherwise fall back
            # to the shared `times` axis (legacy single-axis behaviour).
            rate_times = history.get('rate_times', times)
            self.curves['rate'].setData(rate_times, history['rate'])
            # Overlay: only draw when (a) the user has it on AND (b) the
            # caller supplied a same-length array. An empty/missing array
            # clears the curve cleanly without distorting auto-scale.
            avg = history.get('rate_avg')
            if self.show_rate_avg and avg is not None and len(avg) == len(rate_times):
                self.curves['rate_avg'].setData(rate_times, avg)
            else:
                self.curves['rate_avg'].setData([], [])

        if 'scan' in self.curves:
            scan_data = history.get('scan_data')
            if scan_data is not None:
                if scan_data:
                    wls, rates, _, _ = zip(*scan_data)
                    self.curves['scan'].setData(wls, rates)
                else:
                    self.curves['scan'].setData([], [])

            target_wn_list = history.get('target_wn', [])
            current_target = target_wn_list[-1] if len(target_wn_list) > 0 else 0

            self.curves['scan_cursor'].setValue(current_target)

        if 'laser_curr' in self.curves:
            self.curves['laser_curr'].setData(times, history['wn'])
            self.curves['laser_target'].setData(times, history['target_wn'])

        if 'tof' in self.curves:
            tof_data = history.get('tof_buffer')
            if tof_data is not None: # Only update if provided
                if len(tof_data) > 0:
                    counts, bin_edges = np.histogram(tof_data, bins=self.tof_bins, density=True)
                    self.curves['tof'].setData(bin_edges, counts)
                    self.plot_items['tof'].setTitle(f"ToF Histogram ({len(tof_data)} events)")
                else:
                    self.curves['tof'].setData([], [])
                    self.plot_items['tof'].setTitle("ToF Histogram (0 events)")
