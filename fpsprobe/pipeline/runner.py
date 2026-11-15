"""The per-file pipeline: probe -> timestamp analysis -> verdict -> triage."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from ..analysis.classify import classify
from ..analysis.consistency import evaluate_consistency
from ..analysis.fps_stats import describe, histogram
from ..analysis.timestamps import (
    non_positive_indices,
    ticks_sequence_to_seconds,
    ticks_to_seconds,
)
from ..config import Config
from ..container.ffprobe_adapter import FfprobeAdapter
from ..container.mp4probe import probe_mp4
from ..errors import FpsProbeError
from ..models import (
    DURATION_CONSISTENT,
    DURATION_MOVIE_SHORTER,
    DURATION_TRACK_INCONSISTENT,
    DURATION_TRACK_SHORTER,
    FileOutcome,
    ProbeResult,
)

WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _display_path(value: str | Path) -> str:
    text = str(value).replace("\\", "/")
    if WINDOWS_ABSOLUTE.match(text) or Path(text).is_absolute():
        return Path(text).name
    return text


def _error_entry(
    display: str,
    sha256: str,
    size: int,
    error: FpsProbeError,
) -> dict[str, Any]:
    return {
        "path": display,
        "sha256": sha256,
        "size_bytes": int(size),
        "status": "error",
        "error_code": error.code,
        "error_message": error.message,
        "container": None,
        "timestamps": None,
        "duration_reconciliation": None,
        "verdict": None,
        "reason_codes": ["PROBE_ERROR"],
        "reason_text": error.message,
        "classification": "error",
    }


def _probe(path: Path, config: Config, primary_track: str | int) -> ProbeResult:
    if config.adapter == "ffprobe" and FfprobeAdapter.available():
        return FfprobeAdapter().probe(str(path), primary_track)
    return probe_mp4(path, primary_track)


def _container_block(probe: ProbeResult) -> dict[str, Any]:
    primary = probe.primary_track
    return {
        "format": probe.format,
        "brand": probe.brand,
        "adapter": probe.adapter,
        "stream_count": len(probe.tracks),
        "video_stream_count": len(probe.video_tracks),
        "audio_stream_count": len(probe.audio_tracks),
        "movie_timescale": probe.movie_timescale,
        "movie_duration_seconds": probe.movie_duration_seconds,
        "declared_fps": probe.declared_fps,
        "primary_track_id": primary.track_id,
        "primary_track_timescale": primary.timescale,
        "primary_track_duration_seconds": primary.duration_seconds,
        "frame_count": probe.primary_timestamps.frame_count,
        "moov_at_end": probe.moov_at_end,
    }


def _rel_deviation(left: float, right: float) -> float:
    """Symmetric relative deviation between two durations, in [0, 1]."""
    reference = max(abs(left), abs(right))
    if reference <= 0.0:
        return 0.0
    return abs(left - right) / reference


def _duration_reconciliation(probe: ProbeResult, rel_tolerance: float) -> dict[str, Any]:
    """Cross-check mvhd, tkhd, and stts-accumulated durations on one baseline.

    mvhd and tkhd durations are expressed in the movie timescale, while the
    stts accumulation uses the primary track's media timescale; all three are
    converted to seconds before any comparison. The media-side value is the
    DTS accumulation end (sum of every sample's stts delta), never the PTS
    span, so files with composition offsets or a non-zero PTS origin are not
    misjudged as shorter than their track header declares.
    """
    movie_seconds = probe.movie_duration_seconds
    movie_timescale = probe.movie_timescale
    primary = probe.primary_track
    track_seconds = (
        primary.tkhd_duration_ticks / movie_timescale if movie_timescale > 0 else 0.0
    )
    media_ticks = sum(int(delta) for delta in probe.primary_timestamps.stts_deltas)
    media_seconds = media_ticks / primary.timescale if primary.timescale > 0 else 0.0

    movie_track_rel = _rel_deviation(movie_seconds, track_seconds)
    movie_media_rel = _rel_deviation(movie_seconds, media_seconds)
    track_media_rel = _rel_deviation(track_seconds, media_seconds)

    if track_media_rel > rel_tolerance:
        code = DURATION_TRACK_INCONSISTENT
    elif movie_track_rel > rel_tolerance or movie_media_rel > rel_tolerance:
        if movie_seconds < max(track_seconds, media_seconds):
            code = DURATION_MOVIE_SHORTER
        else:
            code = DURATION_TRACK_SHORTER
    else:
        code = DURATION_CONSISTENT

    return {
        "movie_duration_seconds": movie_seconds,
        "track_duration_seconds": track_seconds,
        "media_duration_seconds": media_seconds,
        "movie_track_delta_seconds": movie_seconds - track_seconds,
        "movie_media_delta_seconds": movie_seconds - media_seconds,
        "track_media_delta_seconds": track_seconds - media_seconds,
        "movie_track_rel_deviation": movie_track_rel,
        "movie_media_rel_deviation": movie_media_rel,
        "track_media_rel_deviation": track_media_rel,
        "rel_tolerance": rel_tolerance,
        "code": code,
    }


def process_file(
    path: str | Path,
    config: Config,
    *,
    display_path: str | Path | None = None,
    primary_track: str | int | None = None,
) -> FileOutcome:
    """Analyse one video; conversion failures become structured error entries."""
    source = Path(path)
    display = _display_path(display_path if display_path is not None else source)
    sha256 = ""
    size = 0
    strategy = config.primary_track if primary_track is None else primary_track
    try:
        size = source.stat().st_size
        sha256 = sha256_file(source)
        probe = _probe(source, config, strategy)
    except FpsProbeError as exc:
        if not size and source.exists():
            size = source.stat().st_size
        return FileOutcome(entry=_error_entry(display, sha256, size, exc))
    except OSError:
        error = FpsProbeError(
            f"cannot read video file: {source.name}", code="FILE_READ_FAILED", exit_code=2
        )
        return FileOutcome(entry=_error_entry(display, sha256, size, error))
    except Exception as exc:
        error = FpsProbeError(
            f"{type(exc).__name__} while analysing {source.name}",
            code="FILE_ANALYSIS_FAILED",
            exit_code=2,
        )
        return FileOutcome(entry=_error_entry(display, sha256, size, error))

    timestamps = probe.primary_timestamps
    deltas = timestamps.deltas_ticks
    ticks = timestamps.timescale
    delta_stats = describe(deltas)
    second_deltas = ticks_sequence_to_seconds(deltas, ticks)
    second_stats = describe(second_deltas)
    consistency = evaluate_consistency(
        probe.declared_fps, deltas, ticks, config.thresholds
    )
    triage = classify(
        consistency["verdict"],
        consistency["reason_codes"],
        consistency["fps_delta"],
        config.thresholds,
        config.classification,
    )
    reconciliation = _duration_reconciliation(probe, config.thresholds.duration_rel_tolerance)
    reason_codes = list(
        dict.fromkeys(
            consistency["reason_codes"] + triage["reason_codes"] + [reconciliation["code"]]
        )
    )
    reason_text = f"{consistency['reason_text']} {triage['reason_text']}".strip()
    pts = timestamps.pts_ticks
    frame_count = len(pts)
    timestamps_block = {
        "frame_count": frame_count,
        "first_pts_ticks": pts[0] if pts else None,
        "last_pts_ticks": pts[-1] if pts else None,
        "first_pts_seconds": ticks_to_seconds(pts[0], ticks) if pts else None,
        "last_pts_seconds": ticks_to_seconds(pts[-1], ticks) if pts else None,
        "delta_count": len(deltas),
        "delta_ticks": delta_stats,
        "delta_seconds": second_stats,
        "measured_fps": consistency["measured_fps"],
        "jitter": delta_stats["jitter"],
        "p95_p50": consistency["p95_p50"],
        "non_positive_count": len(non_positive_indices(deltas)),
        "non_positive_indices": consistency["non_positive_indices"],
        "histogram": histogram(deltas, config.histogram_buckets),
    }
    entry = {
        "path": display,
        "sha256": sha256,
        "size_bytes": size,
        "status": "ok",
        "error_code": None,
        "error_message": None,
        "container": _container_block(probe),
        "timestamps": timestamps_block,
        "duration_reconciliation": reconciliation,
        "verdict": consistency["verdict"],
        "reason_codes": reason_codes,
        "reason_text": reason_text,
        "classification": triage["classification"],
    }
    return FileOutcome(
        entry=entry,
        pts_ticks=list(pts),
        delta_ticks=list(deltas),
        stats=delta_stats,
    )
