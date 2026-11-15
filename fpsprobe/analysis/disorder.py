"""Root-cause attribution for non-positive presentation-time deltas.

The consistency verdict (`TIMESTAMP_BROKEN`) only reports that some PTS
intervals failed to advance. This module explains *why*, per offending
sample, by combining the two timeline tables that produced the PTS sequence:

- ``stts`` (per-sample decode delta) and
- ``ctts`` (per-sample composition offset, signed for version 1 boxes).

For a delta index ``i`` (the interval between sample ``i`` and sample
``i + 1``) the reconstructed presentation interval is::

    pts_delta[i] = stts_delta[i] + (ctts[i + 1] - ctts[i])

so a non-positive interval can always be traced back to a stalled ``stts``
delta, a dropping ``ctts`` offset, or both.
"""

from __future__ import annotations

from typing import Any, Sequence

STTS_ZERO_DELTA = "STTS_ZERO_DELTA"
CTTS_DUPLICATE_OFFSET = "CTTS_DUPLICATE_OFFSET"
CTTS_NEGATIVE_OFFSET_REVERSAL = "CTTS_NEGATIVE_OFFSET_REVERSAL"
CTTS_COMBINATION_REVERSAL = "CTTS_COMBINATION_REVERSAL"
TIMESTAMP_DISORDER_UNSPECIFIED = "TIMESTAMP_DISORDER_UNSPECIFIED"

#: Human-readable meaning of each attribution code, reused in docs/tests.
CAUSE_DESCRIPTIONS = {
    STTS_ZERO_DELTA: (
        "stts sample_delta is zero; decode time did not advance, so the "
        "presentation timestamp repeats (or is exposed to any ctts drop)."
    ),
    CTTS_DUPLICATE_OFFSET: (
        "adjacent ctts composition offsets are identical while stts did not "
        "advance, producing an exact duplicate presentation timestamp."
    ),
    CTTS_NEGATIVE_OFFSET_REVERSAL: (
        "the sample carries a negative ctts composition offset lower than the "
        "previous sample's, pulling its presentation time back across the "
        "previous frame."
    ),
    CTTS_COMBINATION_REVERSAL: (
        "stts advanced and no offset is negative, but the adjacent ctts "
        "combination (larger offset followed by a smaller one) still moved "
        "the presentation time backwards across frames."
    ),
    TIMESTAMP_DISORDER_UNSPECIFIED: (
        "the interval is non-positive but the available stts/ctts data does "
        "not isolate a cause."
    ),
}

#: Bound on per-sample trigger records stored in reports; the summary counts
#: always reflect the full set even when the list is truncated.
MAX_ATTRIBUTION_TRIGGERS = 100


def _trigger_codes(
    stts_delta: int, prev_offset: int, offset: int, has_ctts: bool
) -> list[str]:
    """All attribution codes that apply to one non-positive interval."""
    codes: list[str] = []
    if stts_delta == 0:
        codes.append(STTS_ZERO_DELTA)
    if has_ctts:
        if offset < prev_offset:
            if offset < 0:
                codes.append(CTTS_NEGATIVE_OFFSET_REVERSAL)
            else:
                codes.append(CTTS_COMBINATION_REVERSAL)
        elif stts_delta == 0 and offset == prev_offset:
            codes.append(CTTS_DUPLICATE_OFFSET)
    if not codes:  # pragma: no cover - defensive; unreachable via PTS math
        codes.append(TIMESTAMP_DISORDER_UNSPECIFIED)
    return codes


def _primary_cause(codes: Sequence[str]) -> str:
    """Single most actionable cause for quick triage."""
    if CTTS_NEGATIVE_OFFSET_REVERSAL in codes:
        return CTTS_NEGATIVE_OFFSET_REVERSAL
    if STTS_ZERO_DELTA in codes:
        return STTS_ZERO_DELTA
    if CTTS_COMBINATION_REVERSAL in codes:
        return CTTS_COMBINATION_REVERSAL
    return codes[0]


def _detail(
    sample_index: int,
    prev_sample_index: int,
    pts: int | None,
    prev_pts: int | None,
    delta: int,
    stts_delta: int,
    offset: int | None,
    prev_offset: int | None,
    primary: str,
) -> str:
    if pts is not None and prev_pts is not None:
        relation = (
            f"PTS {pts} ticks does not follow sample {prev_sample_index} "
            f"PTS {prev_pts} (delta {delta})"
        )
    else:
        relation = f"presentation interval {delta} ticks is not positive"
    if offset is None:
        ctts_part = "ctts composition_offset=n/a (no ctts table)"
    else:
        ctts_part = (
            f"ctts composition_offset={offset} (prev {prev_offset})"
        )
    return (
        f"sample {sample_index}: {relation}; stts sample_delta={stts_delta}, "
        f"{ctts_part}; cause={primary}."
    )


def attribute_disorder(
    deltas_ticks: Sequence[int],
    stts_deltas: Sequence[int] | None,
    ctts_offsets: Sequence[int] | None,
    pts_ticks: Sequence[int] | None = None,
    *,
    track_id: int | None = None,
    max_triggers: int = MAX_ATTRIBUTION_TRIGGERS,
) -> dict[str, Any] | None:
    """Attribute every non-positive PTS delta to its stts/ctts root cause.

    ``deltas_ticks`` holds the consecutive PTS differences (length N-1 for N
    samples); ``stts_deltas`` and ``ctts_offsets`` are per-sample (length N).
    ``ctts_offsets`` may be ``None`` when the track has no ctts table.
    Returns ``None`` when the supplied sequences cannot be aligned, so callers
    can fall back to the plain verdict without attribution.
    """
    if stts_deltas is None:
        return None
    sample_count = len(deltas_ticks) + 1
    if len(stts_deltas) != sample_count:
        return None
    has_ctts = ctts_offsets is not None and len(ctts_offsets) == sample_count
    has_pts = pts_ticks is not None and len(pts_ticks) == sample_count

    triggers: list[dict[str, Any]] = []
    causes: dict[str, int] = {}
    reason_codes: list[str] = []
    sample_indices: list[int] = []
    for index, raw_delta in enumerate(deltas_ticks):
        delta = int(raw_delta)
        if delta > 0:
            continue
        stts_delta = int(stts_deltas[index])
        prev_offset = int(ctts_offsets[index]) if has_ctts else 0
        offset = int(ctts_offsets[index + 1]) if has_ctts else 0
        codes = _trigger_codes(stts_delta, prev_offset, offset, has_ctts)
        primary = _primary_cause(codes)
        causes[primary] = causes.get(primary, 0) + 1
        for code in codes:
            if code not in reason_codes:
                reason_codes.append(code)
        sample_index = index + 1
        sample_indices.append(sample_index)
        pts = int(pts_ticks[sample_index]) if has_pts else None
        prev_pts = int(pts_ticks[index]) if has_pts else None
        triggers.append(
            {
                "sample_index": sample_index,
                "prev_sample_index": index,
                "delta_index": index,
                "pts_delta_ticks": delta,
                "stts_sample_delta": stts_delta,
                "ctts_composition_offset": offset if has_ctts else None,
                "prev_ctts_composition_offset": prev_offset if has_ctts else None,
                "pts_ticks": pts,
                "prev_pts_ticks": prev_pts,
                "primary_cause": primary,
                "codes": codes,
                "detail": _detail(
                    sample_index,
                    index,
                    pts,
                    prev_pts,
                    delta,
                    stts_delta,
                    offset if has_ctts else None,
                    prev_offset if has_ctts else None,
                    primary,
                ),
            }
        )

    total = len(triggers)
    if total:
        parts = ", ".join(f"{cause} x{count}" for cause, count in causes.items())
        shown = sample_indices[:12]
        summary_text = (
            f"Root-cause attribution for {total} non-positive delta(s): "
            f"{parts}; trigger sample indices "
            f"{shown}{'...' if len(sample_indices) > 12 else ''}."
        )
    else:
        summary_text = "No non-positive deltas; nothing to attribute."
    return {
        "version": 1,
        "track_id": track_id,
        "trigger_count": total,
        "truncated": total > max_triggers,
        "causes": causes,
        "reason_codes": reason_codes,
        "summary_text": summary_text,
        "triggers": triggers[:max_triggers],
    }
