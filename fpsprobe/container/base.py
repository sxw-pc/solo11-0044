"""Adapter interface and deterministic primary-track selection."""

from __future__ import annotations

from typing import Protocol, Sequence

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
