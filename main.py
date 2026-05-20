import sys
import os
from PyQt5.QtWidgets import QApplication



from src.utils.settings_manager import SettingsManager
from src.control.daq_system import DAQSystem
from src.gui.main_window import MainWindow

if __name__ == "__main__":
    print("*"*50)
    app = QApplication(sys.argv)

    # Settings & DAQ Logic
    settings_manager = SettingsManager()
    settings = settings_manager.settings
    print(settings)

    daq = DAQSystem(config=settings)
    daq.start()

    # GUI
    window = MainWindow(daq)
    window.show()

    # Clean exit
    exit_code = app.exec_()
    daq.stop()
    sys.exit(exit_code)
