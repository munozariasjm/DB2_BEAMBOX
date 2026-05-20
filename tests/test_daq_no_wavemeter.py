"""DAQSystem real-tagger / no-wavemeter fallback path.

Two scenarios:

1. `wavemeter_server.enabled: false` — DAQ installs a NullWavemeterClient
   without trying to reach the server.
2. `enabled: true` but the startup probe (`enable_read`) raises — DAQ
   logs and silently swaps to NullWavemeterClient so the rest of the
   system stays alive.

Both paths must yield `wavemeter_disabled=True`, `wavemeter_connected=False`,
and a `get_wavemeter_status()` that the GUI status widget will render
as orange "DISABLED". The real `Tagger` is monkeypatched to a stub so
this test does not need a TimeTagger card.
"""

from __future__ import annotations

import pytest

import src.control.daq_system as daq_module
from src.devices.null_wavemeter import NullWavemeterClient


class _StubTagger:
    """Drop-in for src.devices.tagger.Tagger in this test."""

    def __init__(self, index=0, initialization_params=None):
        self.index = index
        self.initialization_params = initialization_params or {}
        self._started = False

    def start_reading(self):
        self._started = True

    def get_data(self):
        return []

    def stop(self):
        self._started = False

    def close(self):
        pass


def _base_config():
    return {
        "simulation_mode": False,
        "gui_settings": {"refresh_rate_ms": 50, "integration_time_s": 0.1},
        "scan_settings": {"start_wn": 12624.9, "end_wn": 12624.9, "step_size": 1e-6,
                          "stop_mode": "time", "stop_val": 1.0, "loops": 1},
        "data_settings": {"default_save_dir": "data", "auto_save": False},
        "hardware_settings": {"tagger": {"input_mode": "TTL"}},
        # The host:port intentionally points nowhere — if anything tries to
        # actually dial it the test will hang or fail.
        "wavemeter_server": {"enabled": True, "host": "127.0.0.1", "port": 1,
                             "channel": 1, "tolerance_wn": 1e-5,
                             "poll_interval": 0.1,
                             "required_stable_samples": 4,
                             "wm_averaging_samples": 5},
        "simulation_settings": {"tagger": {}, "wavemeter": {}},
        "control_settings": {"laser_pid": {}},
    }


@pytest.fixture(autouse=True)
def stub_tagger(monkeypatch):
    monkeypatch.setattr(daq_module, "Tagger", _StubTagger)
    yield


def test_wavemeter_explicitly_disabled_installs_null_client():
    cfg = _base_config()
    cfg["wavemeter_server"]["enabled"] = False

    daq = daq_module.DAQSystem(config=cfg)

    assert isinstance(daq.wavemeter, NullWavemeterClient)
    assert daq.wavemeter_disabled is True
    assert daq.wavemeter_connected is False
    status = daq.get_wavemeter_status()
    assert status["mode"] == "null"
    assert status["simulation"] is False
    assert status["connected"] is False
    # Sanity: the no-op surface returns 0.0 without raising.
    assert daq.wavemeter.get_wavenumber() == 0.0


def test_unreachable_server_falls_back_to_null_client(monkeypatch):
    """Probe (`enable_read`) raises → DAQ swaps to NullWavemeterClient."""

    class _RaisingClient:
        def __init__(self, host, port, channel=1, timeout=2.0):
            self.host = host
            self.port = port
            self.channel = channel

        def enable_read(self, channel=None):
            raise RuntimeError("wmServer unreachable (synthetic)")

        def close(self):
            pass

    monkeypatch.setattr(daq_module, "WavemeterClient", _RaisingClient)

    daq = daq_module.DAQSystem(config=_base_config())

    assert isinstance(daq.wavemeter, NullWavemeterClient)
    assert daq.wavemeter_disabled is True
    assert daq.wavemeter_connected is False
    status = daq.get_wavemeter_status()
    assert status["mode"] == "null"


def test_real_client_kept_when_probe_succeeds(monkeypatch):
    """Sanity-check the happy path: probe succeeds → real client retained.

    We stub both the fast TCP smoke test (`socket.create_connection`) and
    the WavemeterClient itself so the test doesn't need a live wmServer.
    """

    class _OkClient:
        def __init__(self, host, port, channel=1, timeout=2.0):
            self.host = host
            self.port = port
            self.channel = channel

        def enable_read(self, channel=None):
            return None

        def get_wavenumber(self):
            return 12624.9

        def close(self):
            pass

    class _FakeSock:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def close(self): pass

    monkeypatch.setattr(daq_module.socket, "create_connection",
                        lambda *a, **k: _FakeSock())
    monkeypatch.setattr(daq_module, "WavemeterClient", _OkClient)

    daq = daq_module.DAQSystem(config=_base_config())

    assert isinstance(daq.wavemeter, _OkClient)
    assert daq.wavemeter_disabled is False
    assert daq.wavemeter_connected is True
    assert daq.get_wavemeter_status()["mode"] == "real"
