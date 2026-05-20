import time
import sys
import numpy as np
import pandas as pd
import os

this_path = os.path.abspath(__file__)
father_path = "C:\\Users\\EMALAB\\Desktop\\TW_DAQ"
sys.path.append(father_path)
try:
    from TimeTaggerDriver_isolde.timetagger4 import TimeTagger as tg
except ImportError:
    print("[HW] Warning: TimeTaggerDriver_isolde not found. Tagger will not work in real mode.")
    tg = None

def convert_to_stoptime(t):
    # 30000 -> ~15 us
    convertion = 30_000 / (15e-6)  # p / s
    return convertion * t


def time_to_flops(t):
    quantization = 100e-12  # seconds / flops
    return t / quantization


def flops_to_time(ft):
    quantization = 100e-12  # seconds / flops
    return ft * quantization


def compute_tof_from_data(data: pd.DataFrame):
    latest_trigger_time = 0
    tofs = []
    for index, d in data.iterrows():
        is_trigger = d.channels == -1
        if is_trigger:
            latest_trigger_time = d.timestamp
        else:
            tof = d["timestamp"] - latest_trigger_time
            tofs.append(flops_to_time(tof))
    return np.array(tofs)

INIT_TIME = 1e-6
STOP_TIME_WINDOW = 10e-6


# Front-end presets per signaling standard. Matches the constants documented
# in the TimeTagger4 User Guide pp. 26-27 (TIMETAGGER4_DC_OFFSET_N_NIM = -0.35 V,
# TIMETAGGER4_DC_OFFSET_P_TTL = +1.13 V) with trigger edge polarity flipped
# accordingly. Trigger level mirrors the same DC offset by default.
_INPUT_MODE_PRESETS = {
    "NIM": {
        "trigger_level": -0.35,
        "trigger_rising": False,   # falling edge for negative-going pulses
        "channel_level":  -0.35,
        "channel_rising":  False,
    },
    "TTL": {
        "trigger_level":  1.13,
        "trigger_rising": True,    # rising edge for positive-going pulses
        "channel_level":  1.13,
        "channel_rising":  True,
    },
}


def _resolve_tagger_config(init_params: dict) -> dict:
    """Merge the preset for `input_mode` with explicit per-field overrides.
    Returns a flat dict the constructor can apply directly. Explicit fields
    win over the preset so the operator can tweak one channel without
    flipping the whole front-end."""
    mode = str(init_params.get("input_mode", "NIM")).upper()
    if mode not in _INPUT_MODE_PRESETS:
        raise ValueError(
            f"unknown tagger input_mode {mode!r}; expected one of {sorted(_INPUT_MODE_PRESETS)}"
        )
    preset = _INPUT_MODE_PRESETS[mode]

    # Channel-wide defaults from the preset.
    channel_levels = [preset["channel_level"]] * 4
    channel_rising = [preset["channel_rising"]] * 4

    # Explicit list overrides land entry-by-entry.
    explicit_levels = init_params.get("channel_levels")
    if explicit_levels is not None:
        for i, v in enumerate(explicit_levels[:4]):
            channel_levels[i] = float(v)
    explicit_rising = init_params.get("channel_rising")
    if explicit_rising is not None:
        for i, v in enumerate(explicit_rising[:4]):
            channel_rising[i] = bool(v)

    return {
        "input_mode": mode,
        "trigger_level":   float(init_params.get("trigger_level",   preset["trigger_level"])),
        "trigger_rising":  bool(init_params.get("trigger_rising",   preset["trigger_rising"])),
        "channel_levels":  channel_levels,
        "channel_rising":  channel_rising,
        "channel_starts_us": float(init_params.get("channel_starts_us", INIT_TIME * 1e6)),
        "channel_stops_us":  float(init_params.get("channel_stops_us",  STOP_TIME_WINDOW * 1e6)),
    }


class Tagger:
    """
    Interface for the real Time Tagger device.

    `initialization_params` accepts:
      input_mode          : "TTL" or "NIM" (default "NIM"). Sets the preset.
      trigger_level       : explicit override (Volts).
      trigger_rising      : explicit override (bool).
      channel_levels      : optional 4-element list of per-channel levels.
      channel_rising      : optional 4-element list of per-channel edge types.
      channel_starts_us   : TOF window open  (microseconds, default 1.0).
      channel_stops_us    : TOF window close (microseconds, default 10.0).
    """
    def __init__(self, index=0, initialization_params: dict = {}):
        print(f"[HW] Tagger initialized (Index: {index})")
        cfg = _resolve_tagger_config(initialization_params)
        print(f"[HW] Tagger input_mode={cfg['input_mode']} "
              f"(trigger_level={cfg['trigger_level']}, "
              f"trigger_rising={cfg['trigger_rising']})")

        self.index = index
        self.input_mode = cfg["input_mode"]
        self.trigger_level = cfg["trigger_level"]
        self.trigger_type = cfg["trigger_rising"]
        self.channels = [True, True, True, True]
        self.levels = list(cfg["channel_levels"])
        self.type = list(cfg["channel_rising"])
        starts_t = cfg["channel_starts_us"] * 1e-6
        stops_t  = cfg["channel_stops_us"]  * 1e-6
        self.starts = [int(time_to_flops(starts_t)) for _ in range(4)]
        self.stops  = [int(time_to_flops(stops_t))  for _ in range(4)]
        self.flops_to_time = flops_to_time
        self.card = None
        self.started = False
        self.init_card()
        print('card initialized')

    def start_reading(self):
        self.started = True
        self.card.startReading()
        print('started reading')

    def stop(self):
        if self.card is not None:
            if self.started:
                self.card.stopReading()
                self.started = False
            self.card.stop()
            self.card = None

    def get_data(self, timeout=5, return_splitted=False):
        """
        IF THERE IS NO EVENT IN THE BUNCH, IT GIVES THE TRIGGER
        IF THERE ARE EVENTS, IT WONT GIVE YOU THE TRIGGER.
        """
        # start = time.time()
        last_inp_data = 0
        # while time.time() - start < timeout:
        status, data = self.card.getPackets()
        if status == 0:  # trigger detected, so there is data
            if data == []:
                print('no data')
                if return_splitted:
                    return [], [], []
                return []
            else:
                new_data = []
                empty_bunches = []
                new_events = []
                for d in data:
                    # _t = time.time() # -> Gives the time in seconds since the epoch as a floating point number
                    # # d has:  [packet_number, events, channel, flops since last trigger]
                    if d[2] == -1: # If the d is a trigger signal we update the last trigger time
                        d[-1] = 0
                        # d.append(_t)
                        empty_bunches.append(d)
                    else:
                        d[-1] = flops_to_time(d[-1])
                        # d.append(_t + d[-1])
                        new_events.append(d)
                    new_data.append(d)
                    # last_trigger_time = _t
                if return_splitted:
                    return new_data, empty_bunches, new_events
                return new_data # [packet_number, events, channel, time_offset since last trigger]
        elif status == 1:  # no trigger seen yet, go to sleep for a bit and try again
            print('status is 1. Better luck next time')
            if return_splitted:
                return [], [], []
            return []
        else:
            raise ValueError

    def set_trigger_level(self, level):
        self.trigger_level = level

    def set_trigger_rising(self):
        self.set_trigger_type(type='rising')

    def set_trigger_falling(self):
        self.set_trigger_type(type='falling')

    def set_trigger_type(self, type='falling'):
        self.trigger_type = type == 'rising'

    def enable_channel(self, channel):
        self.channels[channel] = True

    def disable_channel(self, channel):
        self.channels[channel] = False

    def set_channel_level(self, channel, level):
        self.levels[channel] = level

    def set_channel_rising(self, channel):
        self.set_type(channel, type='rising')

    def set_channel_falling(self, channel):
        self.set_type(channel, type='falling')

    def set_type(self, channel, type='falling'):
        self.type[channel] = type == 'rising'

    def set_channel_window(self, channel, start=0, stop=600000):
        self.starts[channel] = start
        self.stops[channel] = stop


    def init_card(self):
        kwargs = {}
        kwargs['trigger_level'] = self.trigger_level
        # print('initcard',self.trigger_level)
        kwargs['trigger_rising'] = self.trigger_type
        for i, info in enumerate(zip(self.channels, self.levels, self.type, self.starts, self.stops)):
            ch, l, t, st, sp = info
            kwargs['channel_{}_used'.format(i)] = ch
            kwargs['channel_{}_level'.format(i)] = l
            kwargs['channel_{}_rising'.format(i)] = t
            kwargs['channel_{}_start'.format(i)] = st
            kwargs['channel_{}_stop'.format(i)] = sp
        kwargs['index'] = self.index
        if self.card is not None:
            self.stop()
        self.card = tg(**kwargs)
        print("*"*50)
        print(kwargs)
        print("*"*50)