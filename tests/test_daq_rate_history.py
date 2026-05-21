"""Headless tests for DAQSystem's windowed rate history.

Boots a real DAQSystem in simulation mode (MockTagger + MockWavemeterClient
— no PyQt5, no sockets, no hardware) and asserts the (t, rate) sample
stream produced by `_maybe_flush_rate` honours the configured integration
window.

The MockTagger emits triggers + Poisson events at `repetition_rate` Hz, so
the bunch counter ticks reliably even without a real beam. We pick a high
mean events/bunch so each window has nonzero rate (a window with zero
bunches would emit `rate=0.0`, which is correct but uninteresting for the
spacing-of-samples assertions).
"""

import time

import pytest

from src.control.daq_system import DAQSystem


def _sim_config(integration_time_s=0.05, repetition_rate=200.0, detector_channel=2):
    return {
        "simulation_mode": True,
        "gui_settings": {
            "refresh_rate_ms": 20,
            "integration_time_s": integration_time_s,
        },
        "scan_settings": {"start_wn": 12624.9, "end_wn": 12624.9, "step_size": 1e-6,
                          "stop_mode": "time", "stop_val": 1.0, "loops": 1},
        "data_settings": {"default_save_dir": "data", "auto_save": False},
        "hardware_settings": {"tagger": {"input_mode": "TTL",
                                          "detector_channel": detector_channel}},
        "wavemeter_server": {"host": "127.0.0.1", "port": 5000, "channel": 1,
                              "tolerance_wn": 1e-5, "poll_interval": 0.1,
                              "required_stable_samples": 4,
                              "wm_averaging_samples": 5},
        "simulation_settings": {
            "tagger": {
                "repetition_rate": repetition_rate,
                "mean_events_per_bunch": 10.0,
            },
            "wavemeter": {"slew_rate_nm_per_s": 0.05, "noise_nm": 1e-7,
                           "initial_nm": 791.96},
        },
        "control_settings": {"laser_pid": {}},
    }


@pytest.fixture
def daq_factory():
    """Yields a function returning a *started* DAQSystem; tears it down at end."""
    instances = []

    def _make(**kwargs):
        d = DAQSystem(config=_sim_config(**kwargs))
        d.start()
        instances.append(d)
        return d

    yield _make

    for d in instances:
        try:
            d.stop()
        except Exception:
            pass


def test_integration_time_governs_sample_cadence(daq_factory):
    """With integration_time=0.05 s, ~0.5 s of run should yield ~10 samples
    (allow ±5 jitter for thread scheduling + the first-sample anchor)."""
    daq = daq_factory(integration_time_s=0.05)
    time.sleep(0.6)
    times, rates = daq.get_rate_history()
    assert len(times) == len(rates)
    # The first iteration is consumed as the anchor; expect ~ (0.6 - eps) / 0.05.
    assert 6 <= len(times) <= 15, f"got {len(times)} samples"
    # Times are monotonically non-decreasing.
    assert all(times[i] <= times[i + 1] for i in range(len(times) - 1))
    # Sample spacing roughly matches the integration time.
    if len(times) >= 3:
        spacings = [times[i + 1] - times[i] for i in range(len(times) - 1)]
        median_spacing = sorted(spacings)[len(spacings) // 2]
        assert 0.03 < median_spacing < 0.10, f"median spacing {median_spacing}"


def test_set_integration_time_doubles_spacing_live(daq_factory):
    """Run at 0.05 s, then bump to 0.20 s; spacing of subsequently emitted
    samples should grow accordingly."""
    daq = daq_factory(integration_time_s=0.05)
    time.sleep(0.3)
    cut = len(daq.rate_samples)
    daq.set_integration_time(0.20)
    time.sleep(0.8)
    times, _ = daq.get_rate_history()
    # We should have new samples after the cut, spaced ~0.2 s apart.
    new_times = times[cut:]
    assert len(new_times) >= 2, f"only {len(new_times)} samples after change"
    spacings = [new_times[i + 1] - new_times[i] for i in range(len(new_times) - 1)]
    median_spacing = sorted(spacings)[len(spacings) // 2]
    assert 0.15 < median_spacing < 0.30, f"median spacing {median_spacing}"


def test_clear_rate_history_resets_clock(daq_factory):
    daq = daq_factory(integration_time_s=0.05)
    time.sleep(0.3)
    assert len(daq.rate_samples) > 0
    daq.clear_rate_history()
    times, rates = daq.get_rate_history()
    assert times == [] and rates == []
    assert daq._rate_t0 is None
    assert daq._rate_window_start is None
    # New samples after clear restart at t≈0, not at the old wall clock.
    time.sleep(0.3)
    times, _ = daq.get_rate_history()
    assert len(times) > 0
    assert times[0] < 0.5, f"first sample after clear is at t={times[0]}"


def test_rate_value_is_nonzero_on_configured_detector_channel(daq_factory):
    """Regression for the hardcoded channel filter: when the loop only
    accepted a hard-coded channel (3) but the sim emits on channel 2,
    every rate sample came out as 0.0 — broken plot, silent. The detector
    channel is now a config knob (default 2) that matches MockTagger, so
    the median rate must be clearly non-zero.

    Note on expected value: the loop counts each bunch twice (once via
    the channel==-1 trigger entry, once via the first detector hit's
    `entry[0] != previous_bunch` check), so for sim's mean_events_per_bunch
    of 10 the rate hovers near 5, not 10. Bounds are wide because the only
    thing this test guards against is "filter is broken → rate is 0"."""
    daq = daq_factory(integration_time_s=0.2, repetition_rate=500.0)
    time.sleep(1.5)
    _, rates = daq.get_rate_history()
    assert len(rates) >= 3, f"only {len(rates)} samples"
    median = sorted(rates)[len(rates) // 2]
    assert 2.0 < median < 10.0, f"median rate {median} — filter likely broken"


def test_detector_channel_knob_filters_other_channels(daq_factory):
    """Pointing detector_channel at an input the sim does not emit on (3)
    must produce all-zero rate samples — proves the config knob is wired
    end-to-end and that hits on other inputs are correctly ignored."""
    daq = daq_factory(integration_time_s=0.1, repetition_rate=500.0,
                      detector_channel=3)
    time.sleep(0.6)
    _, rates = daq.get_rate_history()
    assert len(rates) >= 2, f"only {len(rates)} samples"
    assert all(r == 0.0 for r in rates), \
        f"expected all-zero rates with detector_channel=3, got {rates}"


def test_first_sample_not_a_startup_spike(daq_factory):
    """The anchor iteration should consume the multi-second backlog between
    tagger.start_reading() and the first loop tick — so the *first* emitted
    sample reflects exactly one integration window, not the backlog."""
    daq = daq_factory(integration_time_s=0.1, repetition_rate=500.0)
    # Sleep long enough that the anchor + a few real windows have elapsed.
    time.sleep(0.5)
    times, rates = daq.get_rate_history()
    assert len(times) >= 1
    # Mean events/bunch is 10. A startup spike would be hundreds (it covered
    # the multi-second startup latency at 500 Hz bunches). A real window of
    # ~0.1 s at lambda=10 hovers near 10 ± shot noise.
    assert rates[0] < 50.0, f"first sample {rates[0]} looks like a startup spike"
