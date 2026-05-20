"""Tests for the TTL/NIM input-mode preset on Tagger.

The TimeTagger4 manual confirms TTL vs NIM is a hardware variant — but the
per-channel dc_offset (mapped to `level`) and the trigger edge polarity
must match. Our DAQ exposes a high-level `input_mode` switch that picks
sensible defaults for each variant. These tests verify the preset table
and the explicit-override path.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.devices.tagger import _resolve_tagger_config


def test_nim_preset():
    cfg = _resolve_tagger_config({"input_mode": "NIM"})
    assert cfg["input_mode"] == "NIM"
    assert cfg["trigger_level"] == pytest.approx(-0.35)
    assert cfg["trigger_rising"] is False
    assert cfg["channel_levels"] == [pytest.approx(-0.35)] * 4
    assert cfg["channel_rising"] == [False] * 4


def test_ttl_preset():
    cfg = _resolve_tagger_config({"input_mode": "TTL"})
    assert cfg["input_mode"] == "TTL"
    assert cfg["trigger_level"] == pytest.approx(1.13)
    assert cfg["trigger_rising"] is True
    assert cfg["channel_levels"] == [pytest.approx(1.13)] * 4
    assert cfg["channel_rising"] == [True] * 4


def test_default_is_nim():
    """No `input_mode` key → fall back to NIM (the variant currently
    deployed on the original DBD beamline)."""
    cfg = _resolve_tagger_config({})
    assert cfg["input_mode"] == "NIM"


def test_input_mode_is_case_insensitive():
    cfg_lower = _resolve_tagger_config({"input_mode": "ttl"})
    cfg_mixed = _resolve_tagger_config({"input_mode": "Ttl"})
    assert cfg_lower["input_mode"] == "TTL"
    assert cfg_mixed["input_mode"] == "TTL"
    assert cfg_lower["trigger_rising"] is True


def test_invalid_input_mode_raises():
    with pytest.raises(ValueError):
        _resolve_tagger_config({"input_mode": "ECL"})


def test_explicit_trigger_level_overrides_preset():
    cfg = _resolve_tagger_config({"input_mode": "TTL", "trigger_level": 2.5})
    assert cfg["trigger_level"] == pytest.approx(2.5)
    # Polarity from the preset stays put.
    assert cfg["trigger_rising"] is True


def test_explicit_trigger_rising_overrides_preset():
    cfg = _resolve_tagger_config({"input_mode": "NIM", "trigger_rising": True})
    assert cfg["trigger_rising"] is True
    # Level from the preset stays put.
    assert cfg["trigger_level"] == pytest.approx(-0.35)


def test_per_channel_level_overrides():
    cfg = _resolve_tagger_config({
        "input_mode": "TTL",
        "channel_levels": [1.5, 2.0, 0.8, 1.2],
    })
    assert cfg["channel_levels"] == [
        pytest.approx(1.5),
        pytest.approx(2.0),
        pytest.approx(0.8),
        pytest.approx(1.2),
    ]


def test_per_channel_rising_overrides():
    cfg = _resolve_tagger_config({
        "input_mode": "TTL",
        "channel_rising": [True, False, True, False],
    })
    assert cfg["channel_rising"] == [True, False, True, False]


def test_short_channel_list_keeps_preset_for_remainder():
    """If the operator only configures channel 0, channels 1-3 keep the
    preset default — don't crash on shorter lists."""
    cfg = _resolve_tagger_config({
        "input_mode": "NIM",
        "channel_levels": [-0.5],   # only channel 0 customised
    })
    assert cfg["channel_levels"][0] == pytest.approx(-0.5)
    assert cfg["channel_levels"][1] == pytest.approx(-0.35)
    assert cfg["channel_levels"][2] == pytest.approx(-0.35)
    assert cfg["channel_levels"][3] == pytest.approx(-0.35)


def test_tof_window_overrides():
    cfg = _resolve_tagger_config({
        "input_mode": "TTL",
        "channel_starts_us": 0.5,
        "channel_stops_us":  20.0,
    })
    assert cfg["channel_starts_us"] == pytest.approx(0.5)
    assert cfg["channel_stops_us"]  == pytest.approx(20.0)
