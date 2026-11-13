"""Timestamp statistics, consistency verdicts, and triage classification."""

from .classify import classify
from .consistency import evaluate_consistency
from .fps_stats import describe, histogram
from .timestamps import compute_deltas, non_positive_indices, ticks_to_seconds

__all__ = [
    "classify",
    "compute_deltas",
    "describe",
    "evaluate_consistency",
    "histogram",
    "non_positive_indices",
    "ticks_to_seconds",
]
