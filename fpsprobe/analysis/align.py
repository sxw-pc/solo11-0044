"""Audio-track time-base alignment diagnostic against the primary video track.

Both tracks' PTS are converted to seconds with their own mdhd timescale; no
frame-number pairing is used. All decision thresholds come from configuration.
"""

from __future__ import annotations

from typing import Any

from ..models import ProbeResult, Thresholds, TrackInfo, TrackTimestamps
from .timestamps import ticks_to_seconds


def _block(
    verdict: str,
    reference: TrackInfo,
    reference_ts: TrackTimestamps,
    *,
    first_offset_ms: float | None = None,
    end_offset_ms: float | None = None,
    drift_ms_per_s: float | None = None,
    truncated_by_movie: bool = False,
    audio: TrackInfo | None = None,
    audio_ts: TrackTimestamps | None = None,
) -> dict[str, Any]:
    return {
        "verdict": verdict,
        "first_offset_ms": first_offset_ms,
        "end_offset_ms": end_offset_ms,
        "drift_ms_per_s": drift_ms_per_s,
        "truncated_by_movie": truncated_by_movie,
        "reference_track_id": reference.track_id,
        "audio_track_id": audio.track_id if audio is not None else None,
        "timescales": {
            "reference": reference.timescale,
            "audio": audio.timescale if audio is not None else None,
        },
        "sample_count": {
            "reference": reference_ts.frame_count,
            "audio": audio_ts.frame_count if audio_ts is not None else None,
        },
    }


def evaluate_alignment(probe: ProbeResult, thresholds: Thresholds) -> dict[str, Any]:
    """Compare the first audio track's time base with the primary video track."""
    reference = probe.primary_track
    reference_ts = probe.primary_timestamps
    audio = next((track for track in probe.tracks if track.handler_type == "soun"), None)
    audio_ts = probe.timestamps_by_id.get(audio.track_id) if audio is not None else None
    if (
        audio is None
        or audio_ts is None
        or not audio_ts.pts_ticks
        or not reference_ts.pts_ticks
    ):
        return _block("SKIPPED_NO_AUDIO", reference, reference_ts)

    video_first = ticks_to_seconds(reference_ts.pts_ticks[0], reference.timescale)
    video_last = ticks_to_seconds(reference_ts.pts_ticks[-1], reference.timescale)
    audio_first = ticks_to_seconds(audio_ts.pts_ticks[0], audio.timescale)
    audio_last = ticks_to_seconds(audio_ts.pts_ticks[-1], audio.timescale)

    movie_seconds = probe.movie_duration_seconds
    truncated = bool(movie_seconds > 0 and audio_last > movie_seconds)
    audio_end = min(audio_last, movie_seconds) if movie_seconds > 0 else audio_last

    first_offset = audio_first - video_first
    end_offset = audio_end - video_last
    window_start = max(audio_first, video_first)
    window_end = min(audio_end, video_last)
    coverage = window_end - window_start
    drift = (end_offset - first_offset) / coverage * 1000.0 if coverage > 0 else 0.0

    first_offset_ms = first_offset * 1000.0
    end_offset_ms = end_offset * 1000.0
    if (
        abs(first_offset_ms) >= thresholds.align_offset_ms
        or abs(end_offset_ms) >= thresholds.align_offset_ms
    ):
        verdict = "OFFSET_EXCEEDED"
    elif abs(drift) >= thresholds.align_drift_ms_per_s:
        verdict = "DRIFT_SUSPECT"
    else:
        verdict = "ALIGNED"
    return _block(
        verdict,
        reference,
        reference_ts,
        first_offset_ms=first_offset_ms,
        end_offset_ms=end_offset_ms,
        drift_ms_per_s=drift,
        truncated_by_movie=truncated,
        audio=audio,
        audio_ts=audio_ts,
    )
