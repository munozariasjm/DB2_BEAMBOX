import time
import random
import numpy as np

class MockTagger:
    """
    Simulates a Time Tagger device generating data at 50 Hz.
    Generates a Trigger (Ch -1) every 20ms, followed by Poisson-distributed
    photon events (Ch 1-4).
    """
    def __init__(self, index=0, initialization_params: dict = {}):
        self.index = index
        self.started = False
        self.start_time = 0.0

        # Physics / Simulation Parameters
        self.repetition_rate = initialization_params.get("repetition_rate", 50.0)  # Hz
        self.period = 1.0 / self.repetition_rate  # 0.02 seconds
        self.mean_events_per_bunch = initialization_params.get("mean_events_per_bunch", 200.0)  # Lambda for Poisson distribution

        # Tracks the theoretical time of the last generated trigger
        # to ensure perfect 50Hz periodicity without drift.
        # Tracks the theoretical time of the last generated trigger
        # to ensure perfect 50Hz periodicity without drift.
        self.last_trigger_time = 0.0

        # Global counter for bunches (packets)
        self.global_packet_counter = 0

        print(f"[SIM] MockTagger initialized: {self.repetition_rate}Hz, Poisson(lambda={self.mean_events_per_bunch})")

        # Define 3 Gaussian peaks: (mean, std, relative_weight)
        self.peaks = [
            {'mean': 0.003, 'std': 0.0002, 'weight': 0.3}, # 3ms
            {'mean': 0.008, 'std': 0.0005, 'weight': 0.5}, # 8ms (Main)
            {'mean': 0.015, 'std': 0.0010, 'weight': 0.2}, # 15ms
        ]

    def start_reading(self):
        self.started = True
        self.start_time = time.time()
        # Align the first trigger with the current time
        self.last_trigger_time = self.start_time
        print("[SIM] Tagger started reading.")

    def stop(self):
        self.started = False
        print("[SIM] Tagger stopped.")

    def get_data(self, timeout=5, return_splitted=False):
        """
        Returns data in the exact format of the real Tagger wrapper:
        List of [packet_num, events, channel, relative_time, absolute_time]
        """
        if not self.started:
            time.sleep(0.01)
            if return_splitted:
                return [], [], []
            return []

        current_time = time.time()

        # 1. Determine how many 50Hz cycles have passed since the last generation
        time_since_last = current_time - self.last_trigger_time

        # If we are polling faster than 50Hz, wait briefly and return nothing
        if time_since_last < self.period:
            time.sleep(0.001)
            if return_splitted:
                return [], [], []
            return []

        # 2. Generate all bunches that "happened" since the last call
        # (This logic handles cases where the GUI lags slightly)
        num_new_bunches = int(time_since_last / self.period)

        new_data = []
        new_triggers = []
        new_events = []

        for _ in range(num_new_bunches):
            # Advance the theoretical trigger time by exactly 20ms
            self.last_trigger_time += self.period
            trigger_ts = self.last_trigger_time

            # --- Generate Trigger Event (Channel -1) ---
            # Format: [packet_num, events, channel, relative_time, absolute_time]
            self.global_packet_counter += 1
            trigger_entry = [self.global_packet_counter, 0, -1, 0.0, trigger_ts]
            new_data.append(trigger_entry)
            new_triggers.append(trigger_entry)

            # --- Generate Photon Events (Poisson) ---
            num_events = np.random.poisson(self.mean_events_per_bunch)

            if num_events > 0:
                # Distribute events among peaks based on weights
                peak_weights = [p['weight'] for p in self.peaks]
                # Normalize weights to sum to 1.0
                total_w = sum(peak_weights)
                probs = [w / total_w for w in peak_weights]

                # Determine how many events go to each peak
                counts_per_peak = np.random.multinomial(num_events, probs)

                all_delays = []
                for i, count in enumerate(counts_per_peak):
                    if count > 0:
                        peak = self.peaks[i]
                        d = np.random.normal(peak['mean'], peak['std'], count)
                        all_delays.extend(d)

                delays = np.array(all_delays)

                # Filter to ensure they are within the 20ms window and positive
                valid_mask = (delays > 0) & (delays < 0.020)
                delays = np.sort(delays[valid_mask])

                for delay in delays:
                    # User requested only one channel (Channel 1)
                    channel = 2
                    absolute_ts = trigger_ts + delay

                    event_entry = [self.global_packet_counter, 0, channel, delay, absolute_ts]
                    new_data.append(event_entry)
                    new_events.append(event_entry)

        if return_splitted:
            return new_data, new_triggers, new_events
        return new_data

    # --- Dummy Methods to Satisfy Interface ---
    def set_trigger_level(self, level): pass
    def set_trigger_rising(self): pass
    def set_trigger_falling(self): pass
    def set_trigger_type(self, type='falling'): pass
    def enable_channel(self, channel): pass
    def disable_channel(self, channel): pass
    def set_channel_level(self, channel, level): pass
    def set_channel_rising(self, channel): pass
    def set_channel_falling(self, channel): pass
    def set_type(self, channel, type='falling'): pass
    def set_channel_window(self, channel, start=0, stop=600000): pass
    def init_card(self): pass