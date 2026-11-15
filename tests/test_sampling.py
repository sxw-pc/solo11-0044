"""Contract tests for the exact-anchor timeline downsampler."""

from __future__ import annotations

import json
import random

import pytest

from fpsprobe.analysis.sampling import (
    build_timeline_record,
    downsample_deltas,
    downsample_pts,
    select_sample_indices,
)
from fpsprobe.analysis.timestamps import compute_deltas, non_positive_indices


def _seeded_deltas(count: int, seed: int = 20261115) -> list[int]:
    """Deterministic deltas including zero and negative intervals."""
    rng = random.Random(seed)
    deltas: list[int] = []
    for index in range(count):
        if index % 97 == 41:
            deltas.append(0)
        elif index % 89 == 17:
            deltas.append(-rng.randint(1, 900))
        else:
            deltas.append(rng.randint(800, 1200))
    return deltas


def _pts_from_deltas(deltas: list[int], start: int = 9000) -> list[int]:
    pts = [start]
    for delta in deltas:
        pts.append(pts[-1] + delta)
    return pts


def _assert_exact_reconstruction(
    sample, sequence: list[int], limit: int, *, frame_count: int | None = None
) -> None:
    original_frames = len(sequence) if frame_count is None else frame_count
    assert sample.reconstructed_duration_ticks == sample.original_duration_ticks
    assert sample.duration_deviation_ticks == 0
    assert sample.sample_indices[0] == 0
    assert sample.sample_indices[-1] == len(sequence) - 1
    assert sample.truncated == (original_frames > limit)
    assert sample.original_frame_count == original_frames


@pytest.mark.parametrize("count", [1, 2, 3, 59, 240, 241, 5000])
def test_pts_sampling_keeps_endpoints_and_exact_duration(count):
    deltas = _seeded_deltas(max(count - 1, 0))
    pts = _pts_from_deltas(deltas)[: max(count, 1)]
    limit = 37
    sample = downsample_pts(pts, limit)
    assert sample.frame_count == len(pts)
    assert sample.truncated == (len(pts) > limit)
    assert sample.sample_indices[0] == 0
    assert sample.sample_indices[-1] == len(pts) - 1
    assert sample.sampled_values[0] == pts[0]
    assert sample.sampled_values[-1] == pts[-1]
    assert sample.reconstructed_duration_ticks == pts[-1] - pts[0]
    assert sample.duration_deviation_ticks == 0
    assert sample.non_positive_indices == non_positive_indices(compute_deltas(pts))
    _assert_exact_reconstruction(sample, pts, limit)


@pytest.mark.parametrize("limit", [1, 2, 3, 16, 240])
def test_delta_sampling_preserves_total_duration_exactly(limit):
    deltas = _seeded_deltas(3000)
    sample = downsample_deltas(deltas, limit)
    assert sample.truncated is True
    assert sample.stored_frame_count >= 2
    assert sample.stored_frame_count <= max(limit, 2)
    assert sum(sample.sampled_values) == sum(deltas)
    assert sample.reconstructed_duration_ticks == sample.original_duration_ticks
    assert sample.duration_deviation_ticks == 0
    # All non-positive delta indices survive sampling in the summary.
    assert sample.non_positive_indices == non_positive_indices(deltas)
    assert sample.non_positive_count == len(non_positive_indices(deltas))
    _assert_exact_reconstruction(sample, deltas, limit)


def test_zero_limit_still_keeps_both_anchors_and_flags_truncation():
    deltas = _seeded_deltas(500)
    sample = downsample_deltas(deltas, 0)
    assert sample.truncated is True
    assert sample.sample_indices == [0, len(deltas) - 1]
    assert sample.sampled_values == [sum(deltas[:-1]), deltas[-1]]
    assert sum(sample.sampled_values) == sum(deltas)
    assert sample.duration_deviation_ticks == 0

    pts = _pts_from_deltas(deltas)
    pts_sample = downsample_pts(pts, 0)
    assert pts_sample.truncated is True
    assert pts_sample.sample_indices == [0, len(pts) - 1]
    assert pts_sample.sampled_values == [pts[0], pts[-1]]
    assert pts_sample.reconstructed_duration_ticks == pts[-1] - pts[0]


def test_not_truncated_behaviour_is_byte_identical_to_the_input():
    deltas = _seeded_deltas(120)
    assert downsample_deltas(deltas, 120).truncated is False
    assert downsample_deltas(deltas, 120).sampled_values == deltas
    assert downsample_deltas(deltas, 121).sampled_values == deltas
    pts = _pts_from_deltas(deltas)
    sample = downsample_pts(pts, len(pts))
    assert sample.sample_indices == list(range(len(pts)))
    assert sample.sampled_values == pts
    assert sample.sampled_deltas == deltas


def test_huge_timeline_is_bounded_and_exact():
    count = 1_000_001
    deltas = [1000] * (count - 1)
    deltas[123] = 0
    deltas[456789] = -250
    sample = downsample_deltas(deltas, 240)
    assert sample.truncated is True
    assert sample.stored_frame_count == 240
    assert sample.reconstructed_duration_ticks == sum(deltas)
    assert sample.duration_deviation_ticks == 0
    assert sample.non_positive_indices == [123, 456789]
    assert sample.non_positive_count == 2
    json.dumps(sample.summary)  # summary stays JSON-serialisable and small


def test_truncated_flag_is_strictly_frame_count_over_limit():
    deltas = _seeded_deltas(200)
    assert downsample_deltas(deltas, 199).truncated is True
    assert downsample_deltas(deltas, 200).truncated is False
    assert downsample_deltas(deltas, 201).truncated is False
    # The two-sample anchor floor can store fewer samples than the limit
    # without changing the strict truncation definition.
    assert downsample_deltas(deltas, 1).truncated is True


@pytest.mark.parametrize("index", [0, 1, 2, 3, 4, 10, 50])
def test_fixed_structural_golden(index):
    deltas = [1000, 1000, 0, -500, 1000, 1000, 1000, 2000, 1000, 1000, 1000]
    sample = downsample_deltas(deltas, index)
    golden = {
        0: ([0, 10], [8500, 1000]),
        1: ([0, 10], [8500, 1000]),
        2: ([0, 10], [8500, 1000]),
        3: ([0, 5, 10], [2500, 6000, 1000]),
        4: ([0, 3, 6, 10], [2000, 1500, 5000, 1000]),
        5: ([0, 2, 5, 7, 10], [2000, 500, 2000, 4000, 1000]),
        10: (
            [0, 1, 2, 3, 4, 5, 6, 7, 8, 10],
            [1000, 1000, 0, -500, 1000, 1000, 1000, 2000, 2000, 1000],
        ),
        50: ([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10], deltas),
    }
    expected_indices, expected_values = golden[index]
    assert sample.sample_indices == expected_indices
    assert sample.sampled_values == expected_values
    assert sum(sample.sampled_values) == sum(deltas) == 9500
    assert sample.non_positive_indices == [2, 3]
    assert sample.non_positive_count == 2
    assert sample.truncated == (11 > index)
    assert sample.summary["original_frame_count"] == 11
    assert sample.summary["stored_frame_count"] == len(expected_indices)
    assert sample.summary["duration_deviation_ticks"] == 0


def test_fixed_pts_golden_and_summary_shape():
    pts = [500, 1500, 2500, 2500, 2000, 3000, 4000]
    sample = downsample_pts(pts, 3)
    assert sample.sample_indices == [0, 3, 6]
    assert sample.sampled_values == [500, 2500, 4000]
    assert sample.sampled_deltas == [2000, 1500]
    assert sample.reconstructed_duration_ticks == 3500
    assert sample.original_duration_ticks == pts[-1] - pts[0]
    assert sample.non_positive_indices == [2, 3]
    assert sample.summary == {
        "input_kind": "pts",
        "frame_count": 7,
        "original_frame_count": 7,
        "stored_frame_count": 3,
        "truncated": True,
        "sample_indices": [0, 3, 6],
        "original_duration_ticks": 3500,
        "reconstructed_duration_ticks": 3500,
        "duration_deviation_ticks": 0,
        "non_positive_count": 2,
        "non_positive_indices": [2, 3],
    }


def test_select_sample_indices_is_sorted_unique_and_anchored():
    for count in range(0, 40):
        for limit in range(0, 12):
            indices = select_sample_indices(count, limit)
            assert indices == sorted(set(indices))
            if count:
                assert indices[0] == 0
                assert indices[-1] == count - 1


def test_invalid_limit_and_kind_are_rejected():
    for invalid in (-1, 1.5, True, "10"):
        with pytest.raises(ValueError):
            downsample_pts([0, 1], invalid)
        with pytest.raises(ValueError):
            downsample_deltas([1], invalid)


def test_build_timeline_record_keeps_short_timelines_verbatim():
    stats = {"count": 2, "mean": 1000.0}
    record = build_timeline_record(3, 240, stats, [0, 1000, 2000], [1000, 1000])
    assert record.truncated is False
    assert record.summary is stats
    assert record.pts_ticks == [0, 1000, 2000]
    assert record.deltas_ticks == [1000, 1000]
    assert record.stored_frame_count == 3


def test_build_timeline_record_samples_only_when_truncated():
    pts = _pts_from_deltas(_seeded_deltas(1000))
    record = build_timeline_record(1000, 50, {"count": 999}, pts, None)
    assert record.truncated is True
    assert record.stored_frame_count == 50
    assert record.pts_ticks[0] == pts[0]
    assert record.pts_ticks[-1] == pts[-1]
    assert sum(record.deltas_ticks) == pts[-1] - pts[0]
    assert record.summary["duration_deviation_ticks"] == 0
    assert record.summary["original_frame_count"] == 1000
    assert record.summary["stored_frame_count"] == 50
    assert record.summary["truncated"] is True
    assert record.summary["non_positive_indices"] == non_positive_indices(
        compute_deltas(pts)
    )
