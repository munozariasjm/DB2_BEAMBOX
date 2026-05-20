"""Rolling-average computation for the live count-rate plot.

The rate samples come from `DAQSystem.rate_samples` at a configurable cadence
(`integration_time_s`). The cadence can change mid-run, and the scanner can
pause/resume, so the timestamps are not on a uniform grid. A two-pointer
trailing-window scan handles that cleanly in O(N).
"""

from __future__ import annotations

from typing import List, Sequence


def compute_trailing_average(
    times: Sequence[float],
    values: Sequence[float],
    window_s: float,
) -> List[float]:
    """For each (t_i, v_i), return mean of v_j where (t_i - window_s) < t_j <= t_i.

    `times` must be non-decreasing. Returns a list the same length as the
    inputs. Empty inputs return []. `window_s` <= 0 is treated as "every
    sample averages with itself" — i.e. returns the values unchanged.
    """
    n = len(times)
    if n == 0:
        return []
    if len(values) != n:
        raise ValueError("times and values must have the same length")

    if window_s <= 0:
        return [float(v) for v in values]

    out: List[float] = [0.0] * n
    left = 0
    running = 0.0
    for right in range(n):
        running += values[right]
        # Pop samples that fell out of the trailing window.
        while times[right] - times[left] > window_s:
            running -= values[left]
            left += 1
        count = right - left + 1
        out[right] = running / count
    return out
