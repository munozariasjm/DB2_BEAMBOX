import json
import csv
import os
import numpy as np

class DataLoader:
    def __init__(self):
        pass

    def load_scan(self, json_path):
        """
        Loads scan metadata from a JSON file and attempts to load the corresponding CSV data.
        Returns a tuple (metadata, processed_data).
        """
        if not os.path.exists(json_path):
            raise FileNotFoundError(f"Metadata file not found: {json_path}")

        with open(json_path, 'r') as f:
            metadata = json.load(f)

        # Infer CSV path from JSON path pattern
        # Pattern: scan_TIMESTAMP_meta.json -> scan_TIMESTAMP.csv
        base_dir = os.path.dirname(json_path)
        filename = os.path.basename(json_path)

        if filename.endswith("_meta.json"):
            csv_filename = filename.replace("_meta.json", ".csv")
        else:
             raise ValueError("Invalid metadata filename format. Expected *_meta.json")

        csv_path = os.path.join(base_dir, csv_filename)

        if not os.path.exists(csv_path):
             raise FileNotFoundError(f"Associated data file not found: {csv_path}")

        data = self.process_data(csv_path)
        return metadata, data

    def process_data(self, csv_path):
        """
        Parses the CSV file and reconstructs history arrays for plotting.

        Expected columns (per the new-experiment schema):
          timestamp, channel, tof, wavemeter_wn, laser_target_wn,
          scan_bin_index, bunch_id
        """
        times = []
        wn_history = []
        target_wn_history = []
        rate_history = []
        tof_buffer = []

        # bin_index -> {'events', 'bunches', 'wn'}
        scan_bins = {}

        current_bunch_id = -1
        events_in_current_bunch = 0
        last_rel_time = 0.0
        last_wn = 0.0
        last_target_wn = 0.0
        last_bin_idx = -1

        with open(csv_path, 'r') as f:
            reader = csv.DictReader(f)
            start_time = None

            for row in reader:
                try:
                    ts = float(row['timestamp'])
                except (ValueError, KeyError):
                    continue

                if start_time is None:
                    start_time = ts
                rel_time = ts - start_time

                try:
                    bunch_id = int(row['bunch_id'])
                    channel = int(row['channel'])
                    bin_idx = int(row['scan_bin_index'])
                    wn_target = float(row['laser_target_wn'])
                except (ValueError, KeyError):
                    continue

                if bin_idx not in scan_bins:
                    scan_bins[bin_idx] = {'events': 0, 'bunches': 0, 'wn': wn_target}

                # Bunch transition: flush the previous bunch's accumulated
                # counts before starting a new one.
                if bunch_id != current_bunch_id:
                    if current_bunch_id != -1:
                        rate_history.append(events_in_current_bunch)
                        times.append(last_rel_time)
                        wn_history.append(last_wn)
                        target_wn_history.append(last_target_wn)

                        if last_bin_idx in scan_bins:
                            scan_bins[last_bin_idx]['bunches'] += 1
                            scan_bins[last_bin_idx]['events'] += events_in_current_bunch

                    current_bunch_id = bunch_id
                    events_in_current_bunch = 0

                last_rel_time = rel_time
                try:
                    last_wn = float(row['wavemeter_wn'])
                except (ValueError, KeyError):
                    last_wn = 0.0
                last_target_wn = wn_target
                last_bin_idx = bin_idx

                if channel == 2:
                    events_in_current_bunch += 1
                    try:
                        tof_buffer.append(float(row['tof']))
                    except (ValueError, KeyError):
                        pass

            # Flush the trailing bunch.
            if current_bunch_id != -1:
                rate_history.append(events_in_current_bunch)
                times.append(last_rel_time)
                wn_history.append(last_wn)
                target_wn_history.append(last_target_wn)
                if last_bin_idx in scan_bins:
                    scan_bins[last_bin_idx]['bunches'] += 1
                    scan_bins[last_bin_idx]['events'] += events_in_current_bunch

        # Format scan_data: [(wavenumber, rate, total_events, total_bunches), ...]
        final_scan_data = []
        for idx in sorted(scan_bins.keys()):
            b = scan_bins[idx]
            rate = b['events'] / b['bunches'] if b['bunches'] > 0 else 0
            final_scan_data.append((b['wn'], rate, b['events'], b['bunches']))

        return {
            'times': times,
            'rate': rate_history,
            'wn': wn_history,
            'target_wn': target_wn_history,
            'scan_data': final_scan_data,
            'tof_buffer': tof_buffer,
        }
