"""Robust descriptive statistics and bounded histograms for PTS deltas."""

from __future__ import annotations

import statistics
from typing import Sequence


def percentile(sorted_values: Sequence[float], fraction: float) -> float:
    """Linear-interpolation percentile on already sorted values."""
    if not sorted_values:
        raise ValueError("cannot compute a percentile of an empty sequence")
    if not 0.0 <= fraction <= 1.0:
        raise ValueError("fraction must be between 0 and 1")
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = (len(sorted_values) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = position - lower
    return float(sorted_values[lower]) * (1.0 - weight) + float(
        sorted_values[upper]
    ) * weight


def _empty_stats() -> dict:
    return {
        "count": 0,
        "min": None,
        "max": None,
        "mean": None,
        "median": None,
        "p95": None,
        "stdev": None,
        "jitter": None,
    }


def describe(values: Sequence[int | float]) -> dict:
    """Describe a delta sequence without ever raising on empty input."""
    if not values:
        return _empty_stats()
    ordered = sorted(float(value) for value in values)
    mean = statistics.fmean(ordered)
    stdev = statistics.pstdev(ordered)
    jitter = None if mean == 0 else stdev / mean
    return {
        "count": len(ordered),
        "min": ordered[0],
        "max": ordered[-1],
        "mean": mean,
        "median": percentile(ordered, 0.5),
        "p95": percentile(ordered, 0.95),
        "stdev": stdev,
        "jitter": jitter,
    }


def histogram(values: Sequence[int | float], buckets: int) -> dict:
    """Aggregate values into at most ``buckets`` equal-width buckets."""
    if isinstance(buckets, bool) or not isinstance(buckets, int) or buckets < 1:
        raise ValueError("buckets must be a positive integer")
    if not values:
        return {
            "bucket_count": 0,
            "total": 0,
            "min": None,
            "max": None,
            "buckets": [],
        }
    numeric = [float(value) for value in values]
    low, high = min(numeric), max(numeric)
    if low == high:
        return {
            "bucket_count": 1,
            "total": len(numeric),
            "min": low,
            "max": high,
            "buckets": [{"lower": low, "upper": high, "count": len(numeric)}],
        }
    width = (high - low) / buckets
    counts = [0] * buckets
    for value in numeric:
        index = min(int((value - low) / width), buckets - 1)
        counts[index] += 1
    result_buckets = []
    for index, count in enumerate(counts):
        lower = low + index * width
        upper = high if index == buckets - 1 else low + (index + 1) * width
        result_buckets.append({"lower": lower, "upper": upper, "count": count})
    return {
        "bucket_count": buckets,
        "total": len(numeric),
        "min": low,
        "max": high,
        "buckets": result_buckets,
    }


def p95_p50_ratio(stats: dict) -> float | None:
    p95 = stats.get("p95")
    median = stats.get("median")
    if p95 is None or median is None or median <= 0:
        return None
    return float(p95) / float(median)
