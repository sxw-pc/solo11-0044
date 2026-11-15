"""Shared immutable data structures used across the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_DURATION_REL_TOLERANCE = 0.005

DURATION_CONSISTENT = "DURATION_CONSISTENT"
DURATION_MOVIE_SHORTER = "DURATION_MOVIE_SHORTER"
DURATION_TRACK_SHORTER = "DURATION_TRACK_SHORTER"
DURATION_TRACK_INCONSISTENT = "DURATION_TRACK_INCONSISTENT"
DURATION_CODES = (
    DURATION_CONSISTENT,
    DURATION_MOVIE_SHORTER,
    DURATION_TRACK_SHORTER,
    DURATION_TRACK_INCONSISTENT,
)


@dataclass(frozen=True)
class Thresholds:
    """All numeric decision thresholds; populated from configuration only."""

    fps_abs_tolerance: float
    fps_rel_tolerance: float
    cfr_delta_rel_tolerance: float
    vfr_jitter_threshold: float
    vfr_p95_p50_threshold: float
    mismatch_review_abs_fps: float
    duration_rel_tolerance: float = DEFAULT_DURATION_REL_TOLERANCE


@dataclass(frozen=True)
class TrackInfo:
    track_id: int
    handler_type: str
    timescale: int
    duration_ticks: int
    sample_count: int
    width: int = 0
    height: int = 0
    stts_entries: tuple[tuple[int, int], ...] = ()
    ctts_version: int | None = None
    # tkhd duration, expressed in the *movie* timescale; 0 when no tkhd box.
    tkhd_duration_ticks: int = 0

    @property
    def duration_seconds(self) -> float:
        if self.timescale <= 0:
            return 0.0
        return self.duration_ticks / self.timescale

    def to_dict(self) -> dict[str, Any]:
        return {
            "track_id": self.track_id,
            "handler_type": self.handler_type,
            "timescale": self.timescale,
            "duration_ticks": self.duration_ticks,
            "duration_seconds": self.duration_seconds,
            "sample_count": self.sample_count,
            "width": self.width,
            "height": self.height,
            "ctts_version": self.ctts_version,
            "tkhd_duration_ticks": self.tkhd_duration_ticks,
        }


@dataclass
class TrackTimestamps:
    track: TrackInfo
    pts_ticks: list[int] = field(default_factory=list)
    dts_ticks: list[int] = field(default_factory=list)
    stts_deltas: list[int] = field(default_factory=list)

    @property
    def timescale(self) -> int:
        return self.track.timescale

    @property
    def deltas_ticks(self) -> list[int]:
        return [b - a for a, b in zip(self.pts_ticks, self.pts_ticks[1:])]

    @property
    def frame_count(self) -> int:
        return len(self.pts_ticks)


@dataclass
class ProbeResult:
    path: Path
    file_size: int
    format: str
    brand: str | None
    movie_timescale: int
    movie_duration_ticks: int
    tracks: list[TrackInfo]
    primary_track: TrackInfo
    primary_timestamps: TrackTimestamps
    declared_fps: float | None
    moov_at_end: bool
    adapter: str = "builtin"

    @property
    def movie_duration_seconds(self) -> float:
        if self.movie_timescale <= 0:
            return 0.0
        return self.movie_duration_ticks / self.movie_timescale

    @property
    def video_tracks(self) -> list[TrackInfo]:
        return [track for track in self.tracks if track.handler_type == "vide"]

    @property
    def audio_tracks(self) -> list[TrackInfo]:
        return [track for track in self.tracks if track.handler_type == "soun"]


@dataclass
class FileOutcome:
    """Result of analysing one file, including bounded raw timeline detail."""

    entry: dict[str, Any]
    pts_ticks: list[int] | None = None
    delta_ticks: list[int] | None = None
    stats: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.entry.get("status") == "ok"


@dataclass
class BatchResult:
    run_id: str
    summary: dict[str, Any]
    report: dict[str, Any]
    json_path: Path
    markdown_path: Path
    exit_code: int
