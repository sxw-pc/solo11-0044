from __future__ import annotations

import pytest

from fpsprobe.analysis.timestamps import (
    compute_deltas,
    duplicate_indices,
    non_positive_indices,
    reversal_indices,
    ticks_sequence_to_seconds,
    ticks_to_ms,
    ticks_to_seconds,
)


def test_compute_deltas_preserves_order_and_sign():
    assert compute_deltas([0, 1000, 2000]) == [1000, 1000]
    assert compute_deltas([0, 1000, 900, 900]) == [1000, -100, 0]
    assert compute_deltas([]) == []
    assert compute_deltas([42]) == []


def test_tick_conversions():
    assert ticks_to_seconds(30000, 30000) == 1.0
    assert ticks_to_ms(1500, 30000) == 50.0
    assert ticks_sequence_to_seconds([0, 1000, 2000], 1000) == [0.0, 1.0, 2.0]


def test_conversion_rejects_zero_timescale():
    with pytest.raises(ValueError):
        ticks_to_seconds(1, 0)
    with pytest.raises(ValueError):
        ticks_to_ms(1, -10)


def test_non_positive_positions_are_retained():
    deltas = [1000, 0, -50, 1000, 0]
    assert non_positive_indices(deltas) == [1, 2, 4]
    assert reversal_indices(deltas) == [2]
    assert duplicate_indices(deltas) == [1, 4]


def test_no_non_positive_values():
    assert non_positive_indices([1, 2, 3]) == []
