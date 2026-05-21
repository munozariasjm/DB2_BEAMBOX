# Discrete Beamline DAQ (DBD)

Laser-spectroscopy DAQ for a beamline experiment. Steps a wavelength-locked
laser across a wavenumber range while time-tagging detector events
synchronously with the beam's bunch trigger. Saves event-per-bunch (EPB) vs
wavenumber.

This build is the **new-experiment refit**:
- Wavemeter access goes through a custom socket server
  (`LASERLABCOMPUTER/new_wmServer.py`) on TCP port 5000. The wire format
  is JSON — one object per request, one newline-terminated JSON object per
  reply (except SET, which is fire-and-forget). The previous line-based
  protocol lives on as `LASERLABCOMPUTER/old_wmServer.py` for reference.
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

A fresh checkout boots in **simulation mode** (`settings.json →
simulation_mode: true`) so the GUI runs end-to-end with no wavemeter
server, no TimeTagger card, no network. A loud terminal + yellow GUI
banner make synthetic data impossible to mistake for a real scan. For an
actual run, flip `simulation_mode` to `false` and start the wavemeter
server on the lab PC first (see below).

### 1. Start the wavemeter server

On the lab machine with the Bristol wavemeter + Measurement Computing
analog-out card:

```powershell
pip install -r LASERLABCOMPUTER/requirements.txt
# Bristol SDK isn't on PyPI — install from Bristol's distribution.
python LASERLABCOMPUTER/new_wmServer.py
```

The server prints `Server listening on 0.0.0.0:5000` on startup and
`Wavemeter thread started` once the multiplexer is alive. Verbs:

| Request | Reply |
|---|---|
| `{"cmd":"GET"}` | `{"type":"total","data":{"telemetry":{...}, "config":{...}}}\n` |
| `{"cmd":"CONFIG"}` | `{"type":"config","data":...}\n` |
| `{"cmd":"SET","channel":<0-idx>,"change":{...}}` | *(no reply)* |

`SET` accepts any field on `WavePort` (`active_read`, `active_pid`,
`vLow`, `vHigh`, `gain`, `offset`) or its nested PID (`kp`, `ki`, `kd`,
`setpoint`). `set_pid_param` / `enable_read` / `enable_pid` / etc. on
the client all fold down to a SET. The client batches multi-key updates
into a single SET — the server parses one JSON object per `recv()`, so
back-to-back tiny messages can otherwise coalesce and crash its loop.

The server's `wavePorts` keys are 0-indexed. We keep
`wavemeter_server.channel` 1-indexed in settings (matching the legacy
convention and existing logbooks) and translate at the client boundary,
so the same `channel: 1` keeps pointing at the same physical port across
the protocol swap.

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

`hardware_settings.tagger.detector_channel` (default `2`) picks which
TimeTagger4 input is treated as the detector. Hits on other inputs are
ignored. Move the detector cable → bump this number; no code change. The
trigger always arrives as `channel == -1` regardless.

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

`wavemeter_server.channel` (1-indexed externally) selects which port of
the server's WavePort table the DAQ locks against. Default is `1`, which
the client translates to wavePort 0 on the wire. To target wavePort 2
(say, the laser on port index 2 in the server's defaults dict), set
`channel: 3`.

`gui_settings.wavemeter_display_unit` flips the status panel's Measured /
Target readout between `"wn"` (cm⁻¹, default) and `"nm"` (nm vacuum).
Display-only — scan settings, lock tolerance, and all internal math stay
in cm⁻¹. Reload the app to switch.

#### Running tagger-only (no wavemeter server)

For bringing up the tagger side of the rig before the wavemeter server
is ready, set `wavemeter_server.enabled: false` in `settings.json`. The
DAQ installs a stub wavemeter (readings return 0.0, PID commands are
silently dropped) and the GUI's wavemeter row shows an orange
**DISABLED** badge instead of red **DISCONNECTED**. The tagger, plots,
and scan logic all keep working — only the `wavemeter_wn` column in any
recorded CSV will be 0.

If `enabled` is left at `true` but the server doesn't answer the
startup probe, the DAQ logs a loud terminal banner and falls back to
the same stub automatically — so the GUI never gets stuck spinning on a
dead socket. Restart the app after the server is up to recover real
readings.

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
| `channel` | -1 for empty bunch; otherwise the TimeTagger4 input that fired (the detector channel, default 2) |
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
