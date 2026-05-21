import time
import threading
import numpy as np

from src.utils.units import nm_vacuum_to_wn

class Scanner(threading.Thread):
    def __init__(self, laser, wavemeter=None):
        super().__init__()
        self.laser = laser
        # `wavemeter` is the WavemeterClient (or mock) and exposes
        # `get_wavenumber()` returning a single cm⁻¹ scalar for the channel
        # the client was configured with. The Scanner doesn't pick channels.
        self.wavemeter = wavemeter

        self.running = False
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.pause_event.set() # Set means "Not Paused" (Running)

        # Scan Configuration
        self.start_wn = 16666.0
        self.end_wn = 16680.0
        self.step_size = 0.5 # cm^-1
        self.stop_mode = 'events' # 'events' or 'time'
        self.stop_value = 100 # count or seconds
        self.loops = 1
        self.loop_callback = None

        # State
        self.current_wavenumber = 0.0 # Target Wavenumber
        self.current_bin_index = 0
        self.accumulated_events = 0
        self.accumulated_bunches = 0
        self.is_accumulating = False # If True, we are in the "Measurement" phase

        # Aggregation
        self.histogram = {} # wn -> [accum_events, accum_bunches]

        # Results (for plotting)
        self.scan_progress = []

        # Timing for ETA
        self.start_timestamp = 0
        self.bins_completed = 0
        self.total_bins = 0
        self.bin_paused_duration = 0.0

    def set_wavemeter(self, wavemeter):
        self.wavemeter = wavemeter

    def wavenumber_to_wavelength(self, wn):
        """Converts cm^-1 to nm."""
        if wn == 0: return 0.0
        return 1e7 / wn

    def reset(self):
        """Clears scan progress and internal counters."""
        self.scan_progress = []
        self.histogram = {}
        self.bins_completed = 0
        self.start_timestamp = 0
        self.accumulated_events = 0
        self.accumulated_bunches = 0
        self.current_bin_index = 0
        print("[Scanner] Scan history reset.")

    def configure(self, start_wn, end_wn, step, stop_mode='events', stop_value=100, loops=1, loop_callback=None):
        self.start_wn = start_wn
        self.end_wn = end_wn
        self.step_size = step
        self.stop_mode = stop_mode
        self.stop_value = stop_value
        self.loops = loops
        self.loop_callback = loop_callback

    def run(self):
        self.running = True
        self.start_timestamp = time.time()

        try:
            # Initialize Histogram if not already
            if not self.histogram:
                 self.histogram = {}

            # Loop logic
            for loop_idx in range(self.loops):
                if self.stop_event.is_set(): break

                print(f"[Scanner] Starting Loop {loop_idx + 1}/{self.loops}...")

                # Determine direction for this loop
                forward = self.end_wn >= self.start_wn
                is_reversed = (loop_idx % 2 == 1)

                if is_reversed:
                    loop_start = self.end_wn
                    loop_end = self.start_wn
                    sign = -1 if forward else 1
                else:
                    loop_start = self.start_wn
                    loop_end = self.end_wn
                    sign = 1 if forward else -1

                # Buffer to include endpoint
                wavenumbers = np.arange(loop_start, loop_end + sign * self.step_size * 0.1, sign * self.step_size)

                # Initial estimate (only first time)
                if loop_idx == 0:
                     self.total_bins = len(wavenumbers) * self.loops

                print(f"[Scanner] Generating {len(wavenumbers)} bins. {loop_start} -> {loop_end}")

                for i, wn in enumerate(wavenumbers):
                    if self.stop_event.is_set(): break
                    self.wait_for_pause()

                    self.current_bin_index = i
                    self.current_wavenumber = wn

                    # Bin Loop (Retry logic for drift)
                    while True:
                        if self.stop_event.is_set(): break

                        # 1. Move Laser
                        if hasattr(self.laser, 'set_wavenumber'):
                            self.laser.set_wavenumber(wn)
                        else:
                            target_nm = self.wavenumber_to_wavelength(wn)
                            self.laser.set_wavelength(target_nm)

                        # 2. Wait for stable
                        while not self.laser.is_stable():
                            if self.stop_event.is_set(): return
                            self.wait_for_pause()
                            time.sleep(0.05)

                        # 3. Start Accumulating
                        self.accumulated_events = 0
                        self.accumulated_bunches = 0
                        self.bin_measured_wns = []
                        self.is_accumulating = True
                        self.bin_paused_duration = 0.0

                        start_time = time.time()
                        bin_complete = False

                        # Accumulation Loop
                        while True:
                            if self.stop_event.is_set(): return
                            self.wait_for_pause()

                            if not self.laser.is_stable():
                                print(f"[Scanner] Drift detected at {wn:.4f}. Resetting bin...")
                                self.is_accumulating = False
                                break

                            # Check Stop Condition
                            current_time = time.time()
                            current_duration = current_time - start_time - self.bin_paused_duration

                            if self.stop_mode == 'events':
                                if self.accumulated_events >= self.stop_value:
                                    bin_complete = True
                                    break
                            elif self.stop_mode == 'bunches':
                                if self.accumulated_bunches >= self.stop_value:
                                    bin_complete = True
                                    break
                            elif self.stop_mode == 'time':
                                if current_duration >= self.stop_value:
                                    bin_complete = True
                                    break

                            # Track Measured Wavenumber
                            if self.wavemeter:
                                wn_measured = self.wavemeter.get_wavenumber()
                                if wn_measured > 0:
                                    self.bin_measured_wns.append(wn_measured)

                            time.sleep(0.005)

                        if bin_complete:
                            break # Break Retry Loop -> Bin Done

                    # --- Post Bin Processing ---
                    self.is_accumulating = False

                    total_elapsed = time.time() - start_time
                    effective_duration = total_elapsed - self.bin_paused_duration

                    # Determine Tolerance (default to 0.01 if not found)
                    tolerance = 0.01
                    if hasattr(self.laser, 'tolerance'):
                        tolerance = self.laser.tolerance

                    # Fuzzy Bin Matching
                    wn_key = None
                    # sorted_keys = sorted(self.histogram.keys()) # Optimization: Could just check neighbours if sorted
                    # But straightforward iteration is safer for now.

                    for existing_key in self.histogram.keys():
                        if abs(wn - existing_key) <= tolerance:
                            wn_key = existing_key
                            break

                    if wn_key is None:
                        wn_key = round(wn, 6) # Fallback to new bin (rounded)

                    # Update Histogram
                    if wn_key not in self.histogram:
                        self.histogram[wn_key] = [0, 0]

                    self.histogram[wn_key][0] += self.accumulated_events
                    self.histogram[wn_key][1] += self.accumulated_bunches

                    # Recalculate Scan Progress (Sorted List) for GUI
                    sorted_wns = sorted(self.histogram.keys())
                    new_progress = []
                    for w in sorted_wns:
                        ev = self.histogram[w][0]
                        bu = self.histogram[w][1]
                        r = ev / bu if bu > 0 else 0
                        new_progress.append((w, r, ev, bu))

                    self.scan_progress = new_progress

                    rate_bin = self.accumulated_events / self.accumulated_bunches if self.accumulated_bunches > 0 else 0
                    print(f"[Scanner] Bin {wn:.6f} done. {self.accumulated_events} ev ({rate_bin:.4f} epb). Total: {self.histogram[wn_key][0]} ev.")

                    self.bins_completed += 1

                # End of Loop Iteration
                if self.loop_callback:
                    print(f"[Scanner] Loop {loop_idx+1} complete. Saving snapshot...")
                    try:
                        self.loop_callback(loop_idx + 1)
                    except Exception as e:
                        print(f"Callback error: {e}")

            print("[Scanner] Scan complete.")
        except Exception as e:
            print(f"[Scanner] Crashed: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self.running = False

    def wait_for_pause(self):
        """Blocks if pause_event is cleared."""
        if not self.pause_event.is_set():
            print("[Scanner] Waiting for resume...")
            t0 = time.time()
            self.pause_event.wait()
            self.bin_paused_duration += (time.time() - t0)

    def pause(self):
        self.pause_event.clear()
        print("[Scanner] Paused.")

    def resume(self):
        self.pause_event.set()
        print("[Scanner] Resumed.")

    def get_status(self):
        """Returns a dict with current status for GUI."""
        elapsed = time.time() - self.start_timestamp if self.start_timestamp > 0 else 0

        eta_seconds = 0
        if self.bins_completed > 0:
            avg_per_bin = elapsed / self.bins_completed
            remaining_bins = self.total_bins - self.bins_completed
            eta_seconds = remaining_bins * avg_per_bin

        # One GET round-trip pulls both the live reading and the server's
        # PID setpoint (in nm), so we can show a useful "Target" even when
        # no scan is active — falling back to the held setpoint instead of
        # 0.0 when scanner.current_wavenumber hasn't been driven yet.
        measured_wn = 0.0
        setpoint_wn = 0.0
        if self.wavemeter:
            try:
                status = self.wavemeter.get_status()
                nm_reading = float(status.get("latest_reading", 0.0) or 0.0)
                if nm_reading > 0:
                    measured_wn = nm_vacuum_to_wn(nm_reading)
                nm_setpoint = float(status.get("setpoint", 0.0) or 0.0)
                if nm_setpoint > 0:
                    setpoint_wn = nm_vacuum_to_wn(nm_setpoint)
            except Exception:
                # Server transient — keep the last-known zeros; the link
                # LED in the status widget will surface the disconnect.
                pass

        target_wn = (self.current_wavenumber if self.current_wavenumber > 0
                     else setpoint_wn)

        return {
            "target_wn": target_wn,
            "measured_wn": measured_wn,
            "stop_mode": self.stop_mode,
            "stop_value": self.stop_value,
            "accumulated": self.accumulated_events,
            "accumulated_bunches": self.accumulated_bunches,
            "bin_index": self.current_bin_index,
            "total_bins": self.total_bins,
            "bins_completed": self.bins_completed,
            "eta_seconds": eta_seconds,
            "is_paused": not self.pause_event.is_set(),
            "is_stopping": self.stop_event.is_set(),
            "is_running": self.running,
            "is_accumulating": self.is_accumulating
        }

    def stop(self, wait=True):
        self.stop_event.set()
        if hasattr(self.laser, 'stop'):
            self.laser.stop()

        if not self.pause_event.is_set():
            self.pause_event.set()

        if wait:
            self.join()

    def report_event(self, is_bunch=False):
        """Called by the data pipeline when an event is processed while accumulating."""
        if self.is_accumulating and self.pause_event.is_set():
            if is_bunch:
                self.accumulated_bunches += 1
            else:
                self.accumulated_events += 1
