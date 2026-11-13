"""Explainable CFR/VFR/timestamp consistency verdicts."""

from __future__ import annotations

from typing import Any, Sequence

from ..models import Thresholds
from .fps_stats import describe, p95_p50_ratio
from .timestamps import duplicate_indices, reversal_indices


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _base_result(
    stats: dict,
    declared_fps: float | None,
    measured_fps: float | None,
    threshold_ratio: float | None,
    stable: bool,
    max_deviation: float | None,
    non_positive: list[int],
) -> dict[str, Any]:
    delta = None
    relative = None
    if declared_fps is not None and measured_fps is not None and declared_fps > 0:
        delta = measured_fps - declared_fps
        relative = abs(delta) / declared_fps
    return {
        "verdict": None,
        "reason_codes": [],
        "reason_text": "",
        "declared_fps": declared_fps,
        "measured_fps": measured_fps,
        "fps_delta": delta,
        "fps_rel_delta": relative,
        "stable": stable,
        "max_delta_rel_deviation": max_deviation,
        "jitter": stats.get("jitter"),
        "p95_p50": threshold_ratio,
        "non_positive_count": len(non_positive),
        "non_positive_indices": non_positive,
    }


def evaluate_consistency(
    declared_fps: float | None,
    deltas_ticks: Sequence[int],
    timescale: int,
    thresholds: Thresholds,
) -> dict[str, Any]:
    """Classify one interval sequence into one of the four verdicts.

    All decision thresholds come from the supplied configuration object.
    """
    if timescale <= 0:
        raise ValueError("timescale must be positive")
    stats = describe(deltas_ticks)
    non_positive = [
        index for index, delta in enumerate(deltas_ticks) if int(delta) <= 0
    ]
    measured_fps = None
    mean = stats.get("mean")
    if mean is not None and mean > 0:
        measured_fps = timescale / float(mean)
    ratio = p95_p50_ratio(stats)
    max_deviation = None
    median = stats.get("median")
    if median is not None and median > 0:
        max_deviation = max(
            abs(float(delta) - float(median)) / float(median)
            for delta in deltas_ticks
        )
    stable = bool(
        deltas_ticks
        and non_positive == []
        and max_deviation is not None
        and max_deviation <= thresholds.cfr_delta_rel_tolerance
    )
    result = _base_result(
        stats,
        declared_fps,
        measured_fps,
        ratio,
        stable,
        max_deviation,
        non_positive,
    )

    if not deltas_ticks:
        result["verdict"] = "CFR_OK"
        result["reason_codes"] = ["NO_FRAME_INTERVALS"]
        result["reason_text"] = "Fewer than two presentation timestamps; no interval can be judged."
        return result

    if non_positive:
        codes: list[str] = []
        reversals = reversal_indices(deltas_ticks)
        duplicates = duplicate_indices(deltas_ticks)
        if reversals:
            codes.append("TIMESTAMP_REVERSAL")
        if duplicates:
            codes.append("DUPLICATE_TIMESTAMP")
        codes.append("NON_POSITIVE_DELTA")
        result["verdict"] = "TIMESTAMP_BROKEN"
        result["reason_codes"] = codes
        result["reason_text"] = (
            f"{len(non_positive)} non-positive interval(s) found at indices "
            f"{non_positive[:12]}{'...' if len(non_positive) > 12 else ''}; "
            f"{len(reversals)} reversal(s), {len(duplicates)} duplicate(s)."
        )
        return result

    jitter = stats.get("jitter")
    high_jitter = bool(
        jitter is not None and float(jitter) > thresholds.vfr_jitter_threshold
    )
    high_ratio = bool(
        ratio is not None and float(ratio) > thresholds.vfr_p95_p50_threshold
    )
    if high_jitter or high_ratio:
        codes = []
        if high_jitter:
            codes.append("HIGH_JITTER")
        if high_ratio:
            codes.append("HIGH_P95_P50_RATIO")
        codes.append("UNSTABLE_INTERVALS")
        result["verdict"] = "VFR_SUSPECT"
        result["reason_codes"] = codes
        result["reason_text"] = (
            f"Interval dispersion is high (jitter={_fmt(jitter)}, "
            f"p95/p50={_fmt(ratio)}); variable frame rate is likely."
        )
        return result

    if not stable:
        result["verdict"] = "VFR_SUSPECT"
        result["reason_codes"] = ["IRREGULAR_INTERVALS"]
        result["reason_text"] = (
            f"Interval deviation {_fmt(max_deviation)} exceeds the configured "
            f"CFR tolerance {thresholds.cfr_delta_rel_tolerance:.4f}."
        )
        return result

    if declared_fps is None or declared_fps <= 0:
        result["verdict"] = "CFR_MISMATCH"
        result["reason_codes"] = ["DECLARED_FPS_MISSING"]
        result["reason_text"] = (
            f"Intervals are stable at {_fmt(measured_fps)} fps, but the container "
            "does not expose a usable declared frame rate."
        )
        return result

    assert result["fps_delta"] is not None
    assert result["fps_rel_delta"] is not None
    abs_delta = abs(float(result["fps_delta"]))
    rel_delta = float(result["fps_rel_delta"])
    if (
        abs_delta > thresholds.fps_abs_tolerance
        and rel_delta > thresholds.fps_rel_tolerance
    ):
        result["verdict"] = "CFR_MISMATCH"
        result["reason_codes"] = ["DECLARED_FPS_MISMATCH"]
        result["reason_text"] = (
            f"Stable intervals measured {_fmt(measured_fps)} fps, but the container "
            f"declares {_fmt(declared_fps)} fps (delta {_fmt(abs_delta)})."
        )
        return result

    result["verdict"] = "CFR_OK"
    result["reason_codes"] = ["FPS_CONSISTENT"]
    result["reason_text"] = (
        f"Constant-like intervals ({_fmt(measured_fps)} fps) agree with the declared "
        f"{_fmt(declared_fps)} fps within configured tolerances."
    )
    return result
