from __future__ import annotations

from pathlib import Path

import pytest

from fixtures.generator import TrackSpec, build_mp4
from fpsprobe.analysis.align import evaluate_alignment
from fpsprobe.container.mp4probe import probe_mp4
from fpsprobe.models import Thresholds
from fpsprobe.pipeline.runner import process_file

THRESHOLDS = Thresholds(
    fps_abs_tolerance=0.01,
    fps_rel_tolerance=0.0005,
    cfr_delta_rel_tolerance=0.01,
    vfr_jitter_threshold=0.08,
    vfr_p95_p50_threshold=1.5,
    mismatch_review_abs_fps=0.02,
    align_offset_ms=40.0,
    align_drift_ms_per_s=10.0,
)

VIDEO = TrackSpec(1, "vide", 30000, ((60, 1000),), width=640, height=360)


def _probe(tmp_path: Path, tracks, **kwargs):
    path = tmp_path / "aligned.mp4"
    path.write_bytes(build_mp4(list(tracks), **kwargs))
    return probe_mp4(path)


def test_dual_track_align_and_single_track_skip(default_config, fixture_dir: Path):
    dual = process_file(fixture_dir / "moov_at_end.mp4", default_config)
    align = dual.entry["align"]
    assert align["verdict"] == "ALIGNED"
    assert align["reference_track_id"] == 1
    assert align["audio_track_id"] == 2
    assert align["timescales"] == {"reference": 30000, "audio": 48000}
    assert align["sample_count"] == {"reference": 60, "audio": 94}
    assert align["truncated_by_movie"] is False
    assert align["first_offset_ms"] == pytest.approx(0.0)
    assert align["end_offset_ms"] == pytest.approx(17.333333, abs=1e-3)
    assert align["drift_ms_per_s"] == pytest.approx(8.813559, abs=1e-3)

    single = process_file(fixture_dir / "cfr_30.mp4", default_config)
    skipped = single.entry["align"]
    assert skipped["verdict"] == "SKIPPED_NO_AUDIO"
    assert skipped["audio_track_id"] is None
    assert skipped["first_offset_ms"] is None
    assert skipped["end_offset_ms"] is None
    assert skipped["drift_ms_per_s"] is None
    assert skipped["truncated_by_movie"] is False
    assert skipped["reference_track_id"] == 1
    assert skipped["timescales"] == {"reference": 30000, "audio": None}
    assert skipped["sample_count"] == {"reference": 60, "audio": None}


def test_first_frame_offset_exceeds_threshold(tmp_path: Path):
    audio = TrackSpec(2, "soun", 48000, ((60, 1024),), ctts=((60, 1920),))
    result = evaluate_alignment(_probe(tmp_path, [VIDEO, audio]), THRESHOLDS)
    assert result["first_offset_ms"] == pytest.approx(40.0)
    assert result["verdict"] == "OFFSET_EXCEEDED"


def test_end_offset_exceeds_threshold(tmp_path: Path):
    audio = TrackSpec(2, "soun", 48000, ((130, 1024),))
    result = evaluate_alignment(
        _probe(tmp_path, [VIDEO, audio], movie_duration_ms=4000), THRESHOLDS
    )
    assert result["first_offset_ms"] == pytest.approx(0.0)
    assert result["end_offset_ms"] == pytest.approx(785.333333, abs=1e-3)
    assert result["verdict"] == "OFFSET_EXCEEDED"


def test_slow_audio_clock_is_drift_suspect(tmp_path: Path):
    audio = TrackSpec(2, "soun", 48000, ((94, 1028),))
    result = evaluate_alignment(
        _probe(tmp_path, [VIDEO, audio], movie_duration_ms=3000), THRESHOLDS
    )
    assert result["truncated_by_movie"] is False
    assert abs(result["first_offset_ms"]) < THRESHOLDS.align_offset_ms
    assert abs(result["end_offset_ms"]) < THRESHOLDS.align_offset_ms
    assert result["drift_ms_per_s"] == pytest.approx(12.754237, abs=1e-3)
    assert result["verdict"] == "DRIFT_SUSPECT"


def test_audio_tail_beyond_movie_duration_is_truncated(tmp_path: Path):
    audio = TrackSpec(2, "soun", 48000, ((100, 1024),))
    result = evaluate_alignment(_probe(tmp_path, [VIDEO, audio]), THRESHOLDS)
    assert result["truncated_by_movie"] is True
    # End deviation is measured against the mvhd duration (2.0 s), not 2.112 s.
    assert result["end_offset_ms"] == pytest.approx(33.333333, abs=1e-3)
    assert result["verdict"] == "DRIFT_SUSPECT"


def test_video_only_input_skips_alignment(tmp_path: Path):
    result = _probe(tmp_path, [VIDEO])
    aligned = evaluate_alignment(result, THRESHOLDS)
    assert aligned["verdict"] == "SKIPPED_NO_AUDIO"
    assert aligned["audio_track_id"] is None
