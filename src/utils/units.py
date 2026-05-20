"""Unit conversion between wavenumber (cm⁻¹) and vacuum wavelength (nm).

The DAQ uses cm⁻¹ internally; the wavemeter server speaks nm vacuum (the
Bristol wavemeter's native unit). The cm⁻¹ ↔ nm conversion happens at the
WavemeterClient boundary — `set_setpoint_wn()` and `get_wavenumber()` are
the only places these helpers should be called from.
"""


def wn_to_nm_vacuum(wn_cm: float) -> float:
    return 1e7 / wn_cm


def nm_vacuum_to_wn(nm: float) -> float:
    return 1e7 / nm
