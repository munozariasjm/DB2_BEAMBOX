import pytest

from src.utils.rate_math import compute_trailing_average


def test_empty_inputs_return_empty():
    assert compute_trailing_average([], [], 5.0) == []


def test_length_mismatch_raises():
    with pytest.raises(ValueError):
        compute_trailing_average([0.0, 1.0], [1.0], 1.0)


def test_window_covers_only_self():
    # Spacing 1 s, window 0.5 s → each point averages with itself.
    times = [0.0, 1.0, 2.0, 3.0]
    vals = [1.0, 5.0, 9.0, 4.0]
    out = compute_trailing_average(times, vals, 0.5)
    assert out == pytest.approx(vals)


def test_window_larger_than_span_full_cumulative_average():
    # Window bigger than total span → every output is the cumulative mean
    # up to that index (causal/trailing semantics).
    times = [0.0, 1.0, 2.0, 3.0]
    vals = [1.0, 2.0, 3.0, 4.0]
    out = compute_trailing_average(times, vals, 100.0)
    expected = [1.0, 1.5, 2.0, 2.5]
    assert out == pytest.approx(expected)


def test_uniform_spacing_moving_average():
    # Spacing 1 s, window 2.5 s → each point averages itself + up to 2 prior.
    times = [0.0, 1.0, 2.0, 3.0, 4.0]
    vals = [10.0, 20.0, 30.0, 40.0, 50.0]
    out = compute_trailing_average(times, vals, 2.5)
    # i=0: [10]            -> 10
    # i=1: [10, 20]        -> 15
    # i=2: [10, 20, 30]    -> 20
    # i=3: [20, 30, 40]    -> 30 (t=0 fell out: 3 - 0 = 3 > 2.5)
    # i=4: [30, 40, 50]    -> 40
    assert out == pytest.approx([10.0, 15.0, 20.0, 30.0, 40.0])


def test_irregular_spacing_with_gap_isolates_sample():
    # Gap between t=1 and t=10 is bigger than the window → sample at t=10
    # averages only with itself.
    times = [0.0, 1.0, 10.0, 11.0]
    vals = [1.0, 2.0, 100.0, 200.0]
    out = compute_trailing_average(times, vals, 2.0)
    # i=0: [1]               -> 1
    # i=1: [1, 2]            -> 1.5
    # i=2: [100]             -> 100 (t=0,1 dropped: 10-0=10, 10-1=9 both > 2)
    # i=3: [100, 200]        -> 150
    assert out == pytest.approx([1.0, 1.5, 100.0, 150.0])


def test_zero_window_returns_inputs_unchanged():
    times = [0.0, 1.0, 2.0]
    vals = [3.0, 4.0, 5.0]
    out = compute_trailing_average(times, vals, 0.0)
    assert out == pytest.approx(vals)


def test_negative_window_treated_as_zero():
    times = [0.0, 1.0]
    vals = [7.0, 8.0]
    out = compute_trailing_average(times, vals, -1.0)
    assert out == pytest.approx(vals)
