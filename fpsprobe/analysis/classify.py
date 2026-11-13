"""Map consistency verdicts to deterministic ok/review/reject triage buckets."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..models import Thresholds

VALID_CLASSES = {"ok", "review", "reject"}


def classify(
    verdict: str,
    reason_codes: Sequence[str],
    fps_delta: float | None,
    thresholds: Thresholds,
    mapping: Mapping[str, str],
) -> dict[str, Any]:
    """Return classification plus an auditable explanation of the mapping."""
    if verdict not in mapping:
        raise ValueError(f"no classification rule for verdict {verdict}")
    base = mapping[verdict]
    if base not in VALID_CLASSES:
        raise ValueError(f"classification for {verdict} must be ok/review/reject")
    codes = list(reason_codes)
    text = f"{verdict} maps to {base} by configuration."

    if verdict == "CFR_MISMATCH":
        delta = abs(float(fps_delta)) if fps_delta is not None else None
        if delta is None:
            text = (
                "CFR_MISMATCH with no usable FPS delta; configured mismatch mapping "
                f"applies ({base})."
            )
        elif delta >= thresholds.mismatch_review_abs_fps:
            codes.append("MISMATCH_ABOVE_REVIEW_THRESHOLD")
            text = (
                f"CFR_MISMATCH delta {delta:.3f} fps is at or above the review "
                f"threshold {thresholds.mismatch_review_abs_fps:.3f}; classified {base}."
            )
        else:
            codes.append("MISMATCH_BELOW_REVIEW_THRESHOLD")
            text = (
                f"CFR_MISMATCH delta {delta:.3f} fps is below the review threshold "
                f"{thresholds.mismatch_review_abs_fps:.3f}; classified ok."
            )
            base = "ok"
    return {
        "classification": base,
        "reason_codes": codes,
        "reason_text": text,
    }
