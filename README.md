# Discrete Beamline DAQ (DBD)

Laser-spectroscopy DAQ for a beamline experiment. Steps a wavelength-locked
laser across a wavenumber range while time-tagging detector events
synchronously with the beam's bunch trigger. Saves event-per-bunch (EPB) vs
wavenumber.

This build is the **new-experiment refit**:
- Wavemeter access goes through a custom socket server
  (`LASERLABCOMPUTER/wmServer.py`) on TCP port 5000.
- The server runs the **PID locally** (kp/ki/kd per channel) and drives the
  laser via an analog-out voltage.
- Only **one wavemeter channel** is recorded — no HeNe, no 4-channel array.
- The time-tagger card defaults to **TTL** front-end (was NIM previously);
  switchable via `hardware_settings.tagger.input_mode` in `settings.json`.
- The HP multimeter and EPICS spectrometer are gone.

## Quick start

```bash
pip install -r requirements.txt
python main.py
```

`settings.json → simulation_mode: true` runs without hardware. For real
scans, start the wavemeter server on the lab PC first (see below).

### 1. Start the wavemeter server

On the lab machine with the Bristol wavemeter + Measurement Computing
analog-out card:

```powershell
pip install -r LASERLABCOMPUTER/requirements.txt
# Bristol SDK isn't on PyPI — install from Bristol's distribution.
python LASERLABCOMPUTER/wmServer.py
```

The server prints `Server listening on 0.0.0.0:5000` on startup and
`Wavemeter thread started` once the multiplexer is alive. The DAQ talks
to it over TCP using a plain-text line protocol — see the docstring at
the top of `wmServer.py` for the verb list (`GET`, `SET`, `PID_ON`,
`PID_OFF`, `READ_ON`, `READ_OFF`, `STATUS`).

### 2. Time-tagger TTL vs NIM

The TimeTagger4 input variant is fixed at manufacture, but per-channel DC
offset and trigger polarity have to match the variant. `settings.json`
exposes a one-line switch:

```json
"hardware_settings": {
    "tagger": {
        "input_mode": "TTL"
    }
}
```

`"TTL"` applies +1.13 V threshold, rising-edge detection; `"NIM"` applies
−0.35 V, falling-edge. Explicit overrides under the same block
(`trigger_level`, `trigger_rising`, `channel_levels`, `channel_rising`,
`channel_starts_us`, `channel_stops_us`) win over the preset.

### 3. Rate plot

The "Total Event Rate" plot bins events into one (t, rate) sample per
**integration window** and overlays an optional rolling average. All three
controls live under *Plot Options → Rate Plot* and apply live (no restart):

- **Integration time (s)** — physics binning window. One point is emitted
  on the plot per window. Decoupled from `gui_settings.refresh_rate_ms`,
  which only controls UI repaint cadence. Persisted as
  `gui_settings.integration_time_s` (default `0.1`).
- **Show rolling average** — toggles a second red curve on the same plot.
  Persisted as `gui_settings.rate_avg_enabled`.
- **Rolling window (s)** — width of the trailing average. Each emitted
  sample averages over all prior samples within the last N seconds (causal
  / non-centered). Stays in physical time, so e.g. "0.5 s smoothing"
  remains 0.5 s even if the integration time is changed mid-run. Persisted
  as `gui_settings.rate_avg_window_s` (default `0.5`).

Reset (or starting a new scan) drops all accumulated rate samples and
restarts at t = 0.

### 4. Wavemeter channel

`wavemeter_server.channel` (1-indexed) selects which port of the server's
WavePort table the DAQ locks against. Default is `1` (the first port the
server reports in its `GET` reply).

### 5. PID parameters

PID lives on the server, not in the DAQ. The GUI's *Laser Control* dialog
exposes every server-side knob:

- **PID gains**: `kp`, `ki`, `kd`
- **Voltage clamp**: `vLow`, `vHigh`
- **PID-output → voltage**: `gain`, `offset`
- Toggle: **Enable server PID**

Changes pushed via OK fire `SET <ch> <key>=<val>` per field. The
**Sync from server** button reads back the live `STATUS` and repopulates
the dialog so a hand-tuned setpoint on the server can be brought back
into the GUI.

Lock judgment lives client-side:
- `tolerance_wn` (cm⁻¹) — how close the wavemeter must be to target.
- `poll_interval` (s) — polling cadence.
- `required_stable_samples` — consecutive in-tolerance reads before the
  controller declares `is_locked`.

## Layout

- `main.py` — GUI entry point.
- `settings.json` — scan params, tagger TTL/NIM, server endpoint, PID, sim toggle.
- `src/control/` — DAQ loop, scanner, laser controller (thin server-PID adapter).
- `src/devices/tagger.py` — real-hardware tagger driver wrapper with TTL/NIM presets.
- `src/devices/wavemeter_client.py` — socket client for `wmServer.py`.
- `src/simulation/sim_tagger.py` — Mock tagger for `simulation_mode: true`.
- `src/simulation/hardware_mocks.py` — `MockWavemeterClient` mirroring `WavemeterClient`.
- `src/gui/` — PyQt5 widgets.
- `LASERLABCOMPUTER/wmServer.py` — wavemeter server with extended PID protocol.
- `data/` — scan CSVs and per-scan metadata.

## CSV schema

Per-event records written to `data/scan_<ts>.csv`:

| column | meaning |
|---|---|
| `timestamp` | wall time (s) — absolute |
| `channel` | -1 for empty bunch, 2 for detector hit |
| `tof` | time of flight relative to bunch trigger (s) |
| `wavemeter_wn` | live wavemeter reading (cm⁻¹) |
| `laser_target_wn` | scanner target (cm⁻¹) |
| `scan_bin_index` | current bin within the scan |
| `bunch_id` | global bunch ID from the tagger |

Metadata JSON (`scan_<ts>_meta.json`) captures scan parameters, the PID
config, server endpoint, tagger config, and simulation params at scan
start so a recorded run is reproducible.

## Tests

```bash
pytest tests/ -k "not hardware"
```

Hardware-only tests live under `tests/hardware/` and require the actual
TimeTagger card.
