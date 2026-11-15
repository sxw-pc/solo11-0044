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


def _fps_from_interval(interval: float | None, timescale: int) -> float | None:
    if interval is None or interval <= 0:
        return None
    return timescale / float(interval)


def _relative_deviation(fps: float | None, declared_fps: float | None) -> float | None:
    if fps is None or declared_fps is None or declared_fps <= 0:
        return None
    return abs(float(fps) - float(declared_fps)) / float(declared_fps)


def estimate_measured_fps(
    stats: dict,
    timescale: int,
    declared_fps: float | None = None,
    *,
    stable: bool = False,
) -> dict:
    """Build the multi-estimator measured-fps block and its selection contract.

    Estimators:

    - ``mean``: legacy estimator, ``timescale / mean(interval)``.
    - ``median``: robust estimator, ``timescale / median(interval)``.
    - ``candidate_interval``: only computed when the interval sequence is
      stable; the median interval of the stable segment is quantised to whole
      ticks and converted back to fps. It is derived from the observed
      intervals, never from the declared fps, so a declared value cannot pull
      the candidate onto itself.

    Selection contract (median and candidate are never used to rescue an
    unstable sequence into a matching declared value):

    - stable segment present and declared fps usable -> ``candidate_interval``;
    - declared fps missing or stable segment empty -> ``median``;
    - ``mean`` is only a last resort when no median is available.
    """
    if timescale <= 0:
        raise ValueError("timescale must be positive")
    declared = None
    if declared_fps is not None and declared_fps > 0:
        declared = float(declared_fps)
    mean_fps = _fps_from_interval(stats.get("mean"), timescale)
    median_fps = _fps_from_interval(stats.get("median"), timescale)
    segment_count = int(stats.get("count") or 0) if stable else 0
    candidate_ticks = None
    candidate_fps = None
    if segment_count:
        median = stats.get("median")
        if median is not None and median > 0:
            candidate_ticks = max(1, int(round(float(median))))
            candidate_fps = _fps_from_interval(candidate_ticks, timescale)
    estimators = {
        "mean": {
            "fps": mean_fps,
            "rel_delta": _relative_deviation(mean_fps, declared),
        },
        "median": {
            "fps": median_fps,
            "rel_delta": _relative_deviation(median_fps, declared),
        },
        "candidate_interval": {
            "fps": candidate_fps,
            "interval_ticks": candidate_ticks,
            "rel_delta": _relative_deviation(candidate_fps, declared),
        },
    }
    selected_estimator = None
    selected_fps = None
    if candidate_fps is not None and declared is not None:
        selected_estimator = "candidate_interval"
        selected_fps = candidate_fps
    elif median_fps is not None:
        selected_estimator = "median"
        selected_fps = median_fps
    elif mean_fps is not None:
        selected_estimator = "mean"
        selected_fps = mean_fps
    return {
        "declared_fps": declared_fps,
        "stable_segment_count": segment_count,
        "estimators": estimators,
        "selected_estimator": selected_estimator,
        "selected_measured_fps": selected_fps,
    }
