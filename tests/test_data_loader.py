import unittest
import os
import json
import csv
import shutil
import tempfile
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.utils.data_loader import DataLoader


class TestDataLoader(unittest.TestCase):
    """DataLoader must parse the new-schema CSVs: no `voltage`,
    no `spectrum_peak`, no `wavemeter_wn1..4`. The single `wavemeter_wn`
    column is what gets recorded by the new-experiment DAQ."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.loader = DataLoader()

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_load_scan_new_schema(self):
        timestamp = "20250101_120000"
        json_path = os.path.join(self.test_dir, f"scan_{timestamp}_meta.json")
        csv_path = os.path.join(self.test_dir, f"scan_{timestamp}.csv")

        metadata = {
            "timestamp": timestamp,
            "scan_parameters": {"loops": 1}
        }
        with open(json_path, 'w') as f:
            json.dump(metadata, f)

        headers = ["timestamp", "channel", "tof",
                   "wavemeter_wn", "laser_target_wn", "scan_bin_index", "bunch_id"]

        data_rows = []
        # Bunch 1 (empty) — bin 0
        data_rows.append([100.0, -1, 0.0, 1000.0, 1000.0, 0, 1])
        # Bunch 101 (empty) — bin 10
        data_rows.append([100.1, -1, 0.0, 1500.0, 1500.0, 10, 101])
        # Bunch 102 (1 event) — bin 10
        data_rows.append([100.2, 2, 123.4, 1500.1, 1500.0, 10, 102])
        # Bunch 103 (2 events) — bin 10
        data_rows.append([100.3, 2, 200.0, 1500.2, 1500.0, 10, 103])
        data_rows.append([100.3, 2, 210.0, 1500.2, 1500.0, 10, 103])

        with open(csv_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerows(data_rows)

        loaded_meta, loaded_data = self.loader.load_scan(json_path)
        self.assertEqual(loaded_meta['timestamp'], timestamp)

        rates = loaded_data['rate']
        self.assertEqual(len(rates), 4)
        self.assertEqual(rates[0], 0)
        self.assertEqual(rates[1], 0)
        self.assertEqual(rates[2], 1)
        self.assertEqual(rates[3], 2)

        scan_data = loaded_data['scan_data']
        self.assertEqual(len(scan_data), 2)

        self.assertEqual(scan_data[0][0], 1000.0)
        self.assertEqual(scan_data[0][1], 0.0)
        self.assertEqual(scan_data[0][2], 0)
        self.assertEqual(scan_data[0][3], 1)

        self.assertEqual(scan_data[1][0], 1500.0)
        self.assertEqual(scan_data[1][1], 1.0)
        self.assertEqual(scan_data[1][2], 3)
        self.assertEqual(scan_data[1][3], 3)

        # `volt` is no longer in the loader output; only times/rate/wn/target_wn/scan_data/tof_buffer.
        self.assertNotIn('volt', loaded_data)
        self.assertIn('tof_buffer', loaded_data)
        self.assertEqual(loaded_data['tof_buffer'], [123.4, 200.0, 210.0])


if __name__ == '__main__':
    unittest.main()
