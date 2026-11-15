"""Timestamp statistics, consistency verdicts, and triage classification."""

from .classify import classify
from .consistency import evaluate_consistency
from .fps_stats import describe, histogram
from .sampling import (
    TimelineSample,
    downsample_deltas,
    downsample_pts,
    select_sample_indices,
)
from .timestamps import compute_deltas, non_positive_indices, ticks_to_seconds

__all__ = [
    "TimelineSample",
    "classify",
    "compute_deltas",
    "describe",
    "downsample_deltas",
    "downsample_pts",
    "evaluate_consistency",
    "histogram",
    "non_positive_indices",
    "select_sample_indices",
    "ticks_to_seconds",
]
