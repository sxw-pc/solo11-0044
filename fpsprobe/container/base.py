"""Adapter interface and deterministic primary-track selection."""

from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence

from ..errors import ProbeError
from ..models import ProbeResult, TrackInfo


class ContainerAdapter(Protocol):
    """Minimal interface an optional metadata adapter must satisfy."""

    name: str

    def probe(self, path: str, primary_track: str | int = "first_video") -> ProbeResult:
        ...


def select_primary_track(
    tracks: Sequence[TrackInfo], strategy: str | int
) -> tuple[TrackInfo, int]:
    """Select a video track using a stable, documented strategy.

    The returned integer is the zero-based position within the video track list.
    """
    videos = [track for track in tracks if track.handler_type == "vide"]
    if not videos:
        raise ProbeError("no video track found", code="NO_VIDEO_TRACK")
    if isinstance(strategy, bool):
        raise ProbeError("invalid primary track strategy", code="PRIMARY_TRACK_INVALID")
    if isinstance(strategy, int):
        if strategy >= len(videos):
            raise ProbeError(
                f"primary video index {strategy} is out of range ({len(videos)} video tracks)",
                code="PRIMARY_TRACK_INVALID",
            )
        return videos[strategy], strategy
    if strategy == "first_video":
        return videos[0], 0
    if strategy == "longest_video":
        index = max(
            range(len(videos)),
            key=lambda pos: (videos[pos].duration_seconds, -videos[pos].track_id),
        )
        return videos[index], index
    raise ProbeError(
        f"unknown primary track strategy: {strategy}", code="PRIMARY_TRACK_INVALID"
    )


# Recommendation ranking for per-track arbitration assessments. The order is a
# total order, so re-running on the same file always yields the same pick:
#   1. usability: "usable" beats "degraded"; "unusable"/"unknown" never win.
#   2. declared-rate status: consistent > missing > mismatch > unjudged.
#   3. more samples (a longer observable timeline is stronger evidence).
#   4. larger coded area (width*height), a proxy for the main rendition.
#   5. lowest track id, as a stable final tie-break.
_USABILITY_RANK = {"usable": 2, "degraded": 1}
_DECLARED_RANK = {"consistent": 2, "missing": 1, "mismatch": 0, "unjudged": -1}

_RECOMMENDATION_CODES = {
    ("usable", "consistent"): "RECOMMENDED_STABLE_CONSISTENT",
    ("usable", "missing"): "RECOMMENDED_STABLE_NO_DECLARED",
    ("usable", "mismatch"): "RECOMMENDED_STABLE_DECLARED_MISMATCH",
}


def _recommendation_key(assessment: Mapping[str, Any]) -> tuple:
    return (
        _USABILITY_RANK[assessment["usability"]],
        _DECLARED_RANK.get(assessment.get("declared_status"), -1),
        int(assessment.get("sample_count") or 0),
        int(assessment.get("width") or 0) * int(assessment.get("height") or 0),
        -int(assessment["track_id"]),
    )


def recommend_primary_track(
    assessments: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Pick the recommended primary video track from per-track assessments.

    Only tracks whose timing evidence can be acted on are candidates: tracks
    with too few samples or non-positive deltas are reported by the caller but
    never recommended. A missing declared rate lowers a track's rank without
    removing it. The result is a pure function of the assessments, so it is
    stable across repeated runs on the same file.
    """
    candidates = [
        assessment
        for assessment in assessments
        if assessment.get("usability") in _USABILITY_RANK
    ]
    if not candidates:
        return {
            "recommended_track_id": None,
            "reason_codes": ["NO_USABLE_TRACK"],
            "reason_text": (
                "No video track is usable: every track has too few samples, "
                "non-positive intervals, or no readable timing table."
            ),
        }
    best = max(candidates, key=_recommendation_key)
    usability = best["usability"]
    code = _RECOMMENDATION_CODES.get(
        (usability, best.get("declared_status")), "RECOMMENDED_DEGRADED_FALLBACK"
    )
    if usability == "usable":
        text = (
            f"Track {best['track_id']} has stable intervals and the best "
            f"declared-rate agreement among {len(candidates)} candidate(s)."
        )
    else:
        text = (
            f"No fully stable video track exists; track {best['track_id']} is "
            "the least degraded candidate."
        )
    return {
        "recommended_track_id": best["track_id"],
        "reason_codes": [code],
        "reason_text": text,
    }
