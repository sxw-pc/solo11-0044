"""Exact-anchor downsampling for bounded per-frame timeline detail.

A sampled timeline may drop middle frames, but never the first or last frame.
Keeping both absolute PTS anchors makes the reconstructed span exactly equal to
the original span: the duration deviation is ``0`` ticks, not an approximate
bound.  Non-positive deltas are reported in full, regardless of sampling.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Sequence

from .timestamps import compute_deltas, non_positive_indices

__all__ = [
    "TimelineRecord",
    "TimelineSample",
    "build_timeline_record",
    "downsample",
    "downsample_deltas",
    "downsample_pts",
    "downsample_timeline",
    "sample_deltas",
    "sample_pts",
    "select_sample_indices",
]


def _limit(max_detail_frames: int) -> int:
    if (
        isinstance(max_detail_frames, bool)
        or not isinstance(max_detail_frames, int)
        or max_detail_frames < 0
    ):
        raise ValueError("max_detail_frames must be a non-negative integer")
    return max_detail_frames


def select_sample_indices(frame_count: int, max_detail_frames: int) -> list[int]:
    """Return sorted, unique indices that always include both endpoints.

    Timelines that fit return every index.  Oversized timelines keep evenly
    spaced detail with a floor of two samples, because the first and last
    absolute anchors cannot be dropped without breaking exact reconstruction.
    That floor is why ``truncated`` is the strict frame-count comparison rather
    than a check of whether every frame fit.
    """
    count = int(frame_count)
    limit = _limit(max_detail_frames)
    if count <= 0:
        return []
    if count <= limit:
        return list(range(count))
    sample_count = min(count, max(limit, 2))
    if sample_count >= count:
        return list(range(count))
    # Integer arithmetic keeps selection deterministic and float-free.
    return [
        position * (count - 1) // (sample_count - 1)
        for position in range(sample_count)
    ]


@dataclass(frozen=True)
class TimelineSample(Mapping):
    """A downsampled timeline plus the fields needed to verify it.

    ``sample_indices`` index the sequence passed in: frames for PTS input and
    intervals for delta input.  Duration totals use the input's integer ticks.
    """

    input_kind: str
    frame_count: int
    truncated: bool
    sample_indices: list[int]
    sampled_values: list[int]
    sampled_deltas: list[int]
    original_duration_ticks: int
    reconstructed_duration_ticks: int
    non_positive_indices: list[int]

    @property
    def kept_indices(self) -> list[int]:
        return list(self.sample_indices)

    @property
    def original_frame_count(self) -> int:
        return self.frame_count

    @property
    def stored_frame_count(self) -> int:
        return len(self.sample_indices)

    @property
    def non_positive_count(self) -> int:
        return len(self.non_positive_indices)

    @property
    def duration_deviation_ticks(self) -> int:
        return self.reconstructed_duration_ticks - self.original_duration_ticks

    @property
    def summary(self) -> dict[str, Any]:
        """The bounded summary fields, keyed for direct comparison."""
        return {
            "input_kind": self.input_kind,
            "frame_count": self.frame_count,
            "original_frame_count": self.frame_count,
            "stored_frame_count": self.stored_frame_count,
            "truncated": self.truncated,
            "sample_indices": list(self.sample_indices),
            "original_duration_ticks": self.original_duration_ticks,
            "reconstructed_duration_ticks": self.reconstructed_duration_ticks,
            "duration_deviation_ticks": self.duration_deviation_ticks,
            "non_positive_count": self.non_positive_count,
            "non_positive_indices": list(self.non_positive_indices),
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self.summary
        payload.update(
            {
                "kept_indices": list(self.sample_indices),
                "sampled_values": list(self.sampled_values),
                "sampled_deltas": list(self.sampled_deltas),
            }
        )
        return payload

    as_dict = to_dict

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())


def _check_exact_span(original: int, reconstructed: int) -> None:
    if reconstructed != original:  # pragma: no cover - guaranteed by anchors
        raise AssertionError(
            "downsampling must preserve the timeline span exactly: "
            f"original={original} reconstructed={reconstructed}"
        )


def downsample_pts(pts: Sequence[int], max_detail_frames: int) -> TimelineSample:
    """Downsample absolute PTS while retaining the first and last anchors."""
    values = [int(value) for value in pts]
    limit = _limit(max_detail_frames)
    indices = select_sample_indices(len(values), limit)
    truncated = len(values) > limit
    sampled_values = [values[index] for index in indices]
    all_deltas = compute_deltas(values)
    sampled_deltas = compute_deltas(sampled_values) if truncated else all_deltas
    original = values[-1] - values[0] if values else 0
    reconstructed = sampled_values[-1] - sampled_values[0] if sampled_values else 0
    _check_exact_span(original, reconstructed)
    return TimelineSample(
        input_kind="pts",
        frame_count=len(values),
        truncated=truncated,
        sample_indices=list(indices),
        sampled_values=list(sampled_values),
        sampled_deltas=list(sampled_deltas),
        original_duration_ticks=original,
        reconstructed_duration_ticks=reconstructed,
        non_positive_indices=non_positive_indices(all_deltas),
    )


def _merge_deltas(values: list[int], indices: list[int]) -> list[int]:
    """Merge dropped intervals into the retained interval that starts them."""
    stored: list[int] = []
    for position, start in enumerate(indices):
        end = indices[position + 1] if position + 1 < len(indices) else len(values)
        stored.append(sum(values[start:end]))
    return stored


def downsample_deltas(
    deltas: Sequence[int],
    max_detail_frames: int,
    *,
    frame_count: int | None = None,
) -> TimelineSample:
    """Downsample intervals while preserving their exact total duration.

    Dropped intervals are merged into the preceding retained interval, so the
    sum of stored deltas equals the sum of the originals.  ``frame_count`` may
    override the default ``len(deltas)`` used for the truncation comparison.
    """
    values = [int(value) for value in deltas]
    limit = _limit(max_detail_frames)
    count = len(values) if frame_count is None else int(frame_count)
    indices = select_sample_indices(len(values), limit)
    truncated = count > limit
    sampled = _merge_deltas(values, indices) if truncated else list(values)
    original = sum(values)
    _check_exact_span(original, sum(sampled))
    return TimelineSample(
        input_kind="deltas",
        frame_count=count,
        truncated=truncated,
        sample_indices=list(indices),
        sampled_values=list(sampled),
        sampled_deltas=list(sampled),
        original_duration_ticks=original,
        reconstructed_duration_ticks=sum(sampled),
        non_positive_indices=non_positive_indices(values),
    )


def downsample_timeline(
    sequence: Sequence[int], max_detail_frames: int, *, kind: str = "pts"
) -> TimelineSample:
    """Dispatch to :func:`downsample_pts` or :func:`downsample_deltas`."""
    if kind in {"pts", "pt", "timestamps"}:
        return downsample_pts(sequence, max_detail_frames)
    if kind in {"deltas", "delta", "intervals"}:
        return downsample_deltas(sequence, max_detail_frames)
    raise ValueError("kind must be 'pts' or 'deltas'")


downsample = downsample_timeline
sample_pts = downsample_pts
sample_deltas = downsample_deltas


@dataclass
class TimelineRecord:
    """The bounded payload the ledger writes for one timeline row."""

    frame_count: int
    stored_frame_count: int
    truncated: bool
    summary: dict[str, Any]
    pts_ticks: list[int] | None
    deltas_ticks: list[int] | None


def build_timeline_record(
    frame_count: int,
    max_detail_frames: int,
    stats: dict[str, Any],
    pts_ticks: Sequence[int] | None,
    deltas_ticks: Sequence[int] | None,
) -> TimelineRecord:
    """Prepare a ``timeline_samples`` row.

    Timelines within the limit are untouched, preserving the previous raw-detail
    behaviour.  Larger timelines keep evenly spaced sample points plus a
    verifiable reconstruction summary; ``truncated`` stays exactly
    ``frame_count > max_detail_frames``.
    """
    original_count = int(frame_count)
    limit = _limit(max_detail_frames)
    if original_count <= limit:
        return TimelineRecord(
            frame_count=original_count,
            stored_frame_count=len(pts_ticks or []),
            truncated=False,
            summary=stats,
            pts_ticks=list(pts_ticks or []),
            deltas_ticks=list(deltas_ticks or []),
        )
    if pts_ticks:
        sample = downsample_pts(pts_ticks, limit)
        stored_pts: list[int] | None = list(sample.sampled_values)
        stored_deltas: list[int] | None = list(sample.sampled_deltas)
    elif deltas_ticks:
        sample = downsample_deltas(deltas_ticks, limit, frame_count=original_count)
        stored_pts, stored_deltas = None, list(sample.sampled_values)
    else:
        sample = downsample_pts([], limit)
        stored_pts, stored_deltas = None, None
    summary = dict(stats)
    summary.update(sample.summary)
    # The caller-supplied frame count is authoritative for the ledger row.
    summary["frame_count"] = original_count
    summary["original_frame_count"] = original_count
    summary["truncated"] = True
    return TimelineRecord(
        frame_count=original_count,
        stored_frame_count=sample.stored_frame_count,
        truncated=True,
        summary=summary,
        pts_ticks=stored_pts,
        deltas_ticks=stored_deltas,
    )
