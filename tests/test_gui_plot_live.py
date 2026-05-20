import sys
import numpy as np
from PyQt5.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget, QPushButton, QLabel
from PyQt5.QtCore import QTimer

# Adjust path to find src
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from src.gui.widgets.plot_widget import PlotWidget
from src.gui.widgets.collapsible_box import CollapsibleBox

class TestWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.resize(800, 600)
        self.central = QWidget()
        self.setCentralWidget(self.central)
        self.layout = QVBoxLayout(self.central)

        # Test Collapsible Box
        self.box = CollapsibleBox("Test Collapsible")
        content = QLabel("This is hidden content.\nLine 2\nLine 3")
        self.box.set_content_widget(content)
        self.layout.addWidget(self.box)

        self.plot_widget = PlotWidget()
        self.layout.addWidget(self.plot_widget)

        self.btn_toggle = QPushButton("Toggle Plots")
        self.btn_toggle.clicked.connect(self.toggle_plots)
        self.layout.addWidget(self.btn_toggle)

        self.btn_theme = QPushButton("Toggle Theme")
        self.btn_theme.clicked.connect(self.toggle_theme)
        self.layout.addWidget(self.btn_theme)

        self.btn_avg = QPushButton("Toggle Rolling Avg Overlay")
        self.btn_avg.clicked.connect(self.toggle_avg)
        self.layout.addWidget(self.btn_avg)
        self.avg_on = False

        self.timer = QTimer()
        self.timer.timeout.connect(self.update_data)
        self.timer.start(100) # 10Hz

        self.t = 0
        self.toggle_state = 0
        self.is_dark = True

        # Auto-expand for test
        QTimer.singleShot(1000, self.box.on_pressed)

    def toggle_plots(self):
        self.toggle_state = (self.toggle_state + 1) % 2
        if self.toggle_state == 0:
            self.plot_widget.set_active_plots(['rate', 'scan', 'laser', 'tof'])
        else:
            self.plot_widget.set_active_plots(['scan', 'tof'])

    def toggle_theme(self):
        self.is_dark = not self.is_dark
        self.plot_widget.set_theme(self.is_dark)

    def toggle_avg(self):
        self.avg_on = not self.avg_on
        self.plot_widget.set_rate_avg_enabled(self.avg_on)

    def update_data(self):
        self.t += 0.1
        times = np.linspace(max(0, self.t - 10), self.t, 100)

        from src.utils.rate_math import compute_trailing_average
        rate_raw = np.sin(times) + 2 + np.random.normal(0, 0.4, size=len(times))
        rate_avg = compute_trailing_average(list(times), list(rate_raw), 2.0)

        history = {
            'times': times,
            'rate_times': times,
            'rate': rate_raw,
            'rate_avg': rate_avg,
            'wn': np.cos(times) * 10 + 1000,
            'target_wn': np.ones_like(times) * 1000,
            # scan_data: list of (wl, rate, total_events, total_bunches)
            'scan_data': list(zip(np.linspace(990, 1010, 20), np.random.random(20)*5, np.zeros(20), np.zeros(20))),
            'tof_buffer': np.random.normal(loc=50, scale=10, size=100)
        }

        self.plot_widget.update_plots(history)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = TestWindow()
    win.show()
    # Auto-close after 5 seconds for automated testing check
    QTimer.singleShot(5000, app.quit)
    sys.exit(app.exec_())
