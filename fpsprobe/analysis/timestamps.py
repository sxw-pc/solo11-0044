"""Integer tick arithmetic and timestamp conversion helpers."""

from __future__ import annotations

from typing import Iterable, Sequence

from .fps_stats import describe, histogram

__all__ = [
    "compute_deltas",
    "describe",
    "duplicate_indices",
    "histogram",
    "non_positive_indices",
    "reversal_indices",
    "ticks_sequence_to_seconds",
    "ticks_to_ms",
    "ticks_to_seconds",
]


def compute_deltas(pts: Sequence[int]) -> list[int]:
    """Return consecutive PTS differences, preserving sign and duplicates."""
    return [int(right) - int(left) for left, right in zip(pts, pts[1:])]


def ticks_to_seconds(ticks: float, timescale: int) -> float:
    if timescale <= 0:
        raise ValueError("timescale must be positive")
    return float(ticks) / float(timescale)


def ticks_to_ms(ticks: float, timescale: int) -> float:
    return ticks_to_seconds(ticks, timescale) * 1000.0


def ticks_sequence_to_seconds(
    ticks: Iterable[float], timescale: int
) -> list[float]:
    return [ticks_to_seconds(value, timescale) for value in ticks]


def non_positive_indices(deltas: Sequence[int]) -> list[int]:
    """Indices of null or reversed intervals; indices are retained, not dropped."""
    return [index for index, delta in enumerate(deltas) if delta <= 0]


def reversal_indices(deltas: Sequence[int]) -> list[int]:
    return [index for index, delta in enumerate(deltas) if delta < 0]


def duplicate_indices(deltas: Sequence[int]) -> list[int]:
    return [index for index, delta in enumerate(deltas) if delta == 0]
