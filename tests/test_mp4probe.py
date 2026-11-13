from __future__ import annotations

from pathlib import Path

import pytest

from fixtures.generator import TrackSpec, build_mp4
from fpsprobe.container.ffprobe_adapter import FfprobeAdapter
from fpsprobe.container.mp4probe import probe_mp4
from fpsprobe.errors import ProbeError


def test_cfr_30_metadata_and_timestamps(fixture_dir: Path):
    result = probe_mp4(fixture_dir / "cfr_30.mp4")
    assert result.format == "mp4"
    assert result.brand == "isom"
    assert result.movie_timescale == 1000
    assert result.movie_duration_seconds == 2.0
    assert result.primary_track.track_id == 1
    assert result.primary_track.timescale == 30000
    assert result.primary_timestamps.frame_count == 60
    assert result.primary_timestamps.pts_ticks[0] == 0
    assert result.primary_timestamps.pts_ticks[-1] == 59000
    assert result.declared_fps == 30.0
    assert result.moov_at_end is False


def test_cfr_2997_declared_vs_measured_mismatch_source(fixture_dir: Path):
    result = probe_mp4(fixture_dir / "cfr_2997.mp4")
    deltas = result.primary_timestamps.deltas_ticks
    assert result.primary_track.timescale == 30000
    assert result.primary_timestamps.frame_count == 60
    assert result.primary_timestamps.pts_ticks[0] == 0
    assert len(deltas) == 59
    assert set(deltas) == {1001}
    assert result.declared_fps == 30.0


def test_vfr_fixture_has_mixed_intervals(fixture_dir: Path):
    result = probe_mp4(fixture_dir / "vfr_jitter.mp4")
    deltas = result.primary_timestamps.deltas_ticks
    assert result.primary_track.timescale == 30000
    assert result.primary_track.track_id == 1
    assert result.primary_timestamps.frame_count == 60
    assert result.primary_timestamps.pts_ticks[0] == 0
    assert {500, 1500}.issubset(set(deltas))


def test_broken_order_fixture_keeps_bad_intervals(fixture_dir: Path):
    result = probe_mp4(fixture_dir / "broken_order.mp4")
    deltas = result.primary_timestamps.deltas_ticks
    assert result.primary_track.timescale == 30000
    assert result.primary_track.track_id == 1
    assert result.primary_timestamps.frame_count == 60
    assert result.primary_timestamps.pts_ticks[0] == 0
    assert result.primary_track.ctts_version == 1
    assert [index for index, delta in enumerate(deltas) if delta <= 0] == [9, 11, 13]
    assert any(delta < 0 for delta in deltas)


def test_moov_at_end_and_audio_track(fixture_dir: Path):
    result = probe_mp4(fixture_dir / "moov_at_end.mp4")
    assert result.moov_at_end is True
    assert len(result.tracks) == 2
    assert len(result.video_tracks) == 1
    assert len(result.audio_tracks) == 1
    assert result.primary_track.track_id == 1
    assert result.primary_track.timescale == 30000
    assert result.primary_timestamps.frame_count == 60
    assert result.primary_timestamps.pts_ticks[0] == 0
    assert result.declared_fps == 30.0


def test_primary_track_strategies(tmp_path: Path):
    tracks = [
        TrackSpec(10, "vide", 30000, ((30, 1000),), width=320, height=180),
        TrackSpec(20, "vide", 30000, ((120, 1000),), width=640, height=360),
    ]
    path = tmp_path / "multi.mp4"
    path.write_bytes(build_mp4(tracks))
    assert probe_mp4(path, "first_video").primary_track.track_id == 10
    assert probe_mp4(path, "longest_video").primary_track.track_id == 20
    assert probe_mp4(path, 1).primary_track.track_id == 20
    with pytest.raises(ProbeError) as caught:
        probe_mp4(path, 2)
    assert caught.value.code == "PRIMARY_TRACK_INVALID"


def test_audio_only_input_is_rejected(tmp_path: Path):
    path = tmp_path / "audio.mp4"
    path.write_bytes(
        build_mp4([TrackSpec(1, "soun", 48000, ((100, 1024),), channels=2)])
    )
    with pytest.raises(ProbeError) as caught:
        probe_mp4(path)
    assert caught.value.code == "NO_VIDEO_TRACK"


def test_missing_moov_is_clear(tmp_path: Path):
    path = tmp_path / "no_moov.mp4"
    path.write_bytes(b"\x00\x00\x00\x08free")
    with pytest.raises(ProbeError) as caught:
        probe_mp4(path)
    assert caught.value.code == "MOOV_NOT_FOUND"


def test_ffprobe_adapter_probe_is_optional_boolean():
    assert isinstance(FfprobeAdapter.available(), bool)
