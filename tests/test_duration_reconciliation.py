from __future__ import annotations

from pathlib import Path

import pytest

from fixtures.generator import TrackSpec, build_mp4
from fpsprobe.container.mp4probe import probe_mp4
from fpsprobe.models import (
    DURATION_CODES,
    DURATION_CONSISTENT,
    DURATION_MOVIE_SHORTER,
    DURATION_TRACK_INCONSISTENT,
    DURATION_TRACK_SHORTER,
)
from fpsprobe.pipeline.runner import process_file

FIXTURE_VERDICTS = {
    "cfr_30.mp4": "CFR_OK",
    "cfr_2997.mp4": "CFR_MISMATCH",
    "vfr_jitter.mp4": "VFR_SUSPECT",
    "broken_order.mp4": "TIMESTAMP_BROKEN",
    "moov_at_end.mp4": "CFR_OK",
}


def _entry(path: Path, default_config) -> dict:
    outcome = process_file(path, default_config, display_path=path.name)
    assert outcome.entry["status"] == "ok"
    return outcome.entry


def _write(tmp_path: Path, name: str, tracks, **kwargs) -> Path:
    path = tmp_path / name
    path.write_bytes(build_mp4(tracks, **kwargs))
    return path


def test_stock_fixtures_reconcile_as_consistent(default_config, fixture_dir: Path):
    for name, verdict in FIXTURE_VERDICTS.items():
        entry = _entry(fixture_dir / name, default_config)
        block = entry["duration_reconciliation"]
        assert block["code"] == DURATION_CONSISTENT, name
        assert block["code"] in DURATION_CODES
        assert block["code"] in entry["reason_codes"]
        # Existing verdict semantics are untouched by the new block.
        assert entry["verdict"] == verdict, name


def test_media_duration_uses_dts_accumulation_not_pts_span(
    default_config, fixture_dir: Path
):
    for name in ("broken_order.mp4", "moov_at_end.mp4"):
        entry = _entry(fixture_dir / name, default_config)
        block = entry["duration_reconciliation"]
        # stts accumulates to exactly 2.0 s; the PTS span is only ~1.9667 s
        # and would wrongly read as a short track.
        assert block["media_duration_seconds"] == pytest.approx(2.0)
        assert block["media_duration_seconds"] == pytest.approx(
            block["track_duration_seconds"]
        )
        assert block["code"] == DURATION_CONSISTENT


def test_movie_shorter_than_track_and_media(default_config, tmp_path: Path):
    path = _write(
        tmp_path,
        "movie_short.mp4",
        [TrackSpec(1, "vide", 30000, ((60, 1000),), width=640, height=360)],
        movie_duration_ms=1500,
    )
    entry = _entry(path, default_config)
    block = entry["duration_reconciliation"]
    assert block["movie_duration_seconds"] == pytest.approx(1.5)
    assert block["track_duration_seconds"] == pytest.approx(2.0)
    assert block["media_duration_seconds"] == pytest.approx(2.0)
    assert block["movie_track_delta_seconds"] == pytest.approx(-0.5)
    assert block["movie_media_rel_deviation"] == pytest.approx(0.25)
    assert block["code"] == DURATION_MOVIE_SHORTER
    assert DURATION_MOVIE_SHORTER in entry["reason_codes"]


def test_exaggerated_tkhd_is_track_inconsistent(default_config, tmp_path: Path):
    path = _write(
        tmp_path,
        "tkhd_exaggerated.mp4",
        [
            TrackSpec(
                1,
                "vide",
                30000,
                ((60, 1000),),
                width=640,
                height=360,
                tkhd_duration=3000,
            )
        ],
    )
    entry = _entry(path, default_config)
    block = entry["duration_reconciliation"]
    assert block["movie_duration_seconds"] == pytest.approx(2.0)
    assert block["track_duration_seconds"] == pytest.approx(3.0)
    assert block["media_duration_seconds"] == pytest.approx(2.0)
    assert block["track_media_delta_seconds"] == pytest.approx(1.0)
    assert block["track_media_rel_deviation"] == pytest.approx(1.0 / 3.0)
    assert block["code"] == DURATION_TRACK_INCONSISTENT
    assert DURATION_TRACK_INCONSISTENT in entry["reason_codes"]


def test_track_shorter_than_movie(default_config, tmp_path: Path):
    path = _write(
        tmp_path,
        "track_short.mp4",
        [TrackSpec(1, "vide", 30000, ((60, 1000),), width=640, height=360)],
        movie_duration_ms=3000,
    )
    entry = _entry(path, default_config)
    block = entry["duration_reconciliation"]
    assert block["movie_duration_seconds"] == pytest.approx(3.0)
    assert block["track_duration_seconds"] == pytest.approx(2.0)
    assert block["media_duration_seconds"] == pytest.approx(2.0)
    assert block["code"] == DURATION_TRACK_SHORTER
    assert DURATION_TRACK_SHORTER in entry["reason_codes"]


def test_non_zero_pts_origin_stays_consistent(default_config, tmp_path: Path):
    # B-frame style composition offset: every PTS shifts +2000 ticks, so the
    # presentation timeline starts at 2000 and spans only 59000 ticks.
    path = _write(
        tmp_path,
        "pts_offset.mp4",
        [
            TrackSpec(
                1,
                "vide",
                30000,
                ((60, 1000),),
                ctts=((60, 2000),),
                width=640,
                height=360,
            )
        ],
    )
    probe = probe_mp4(path)
    assert probe.primary_timestamps.pts_ticks[0] == 2000
    pts_span = (
        probe.primary_timestamps.pts_ticks[-1] - probe.primary_timestamps.pts_ticks[0]
    )
    assert pts_span == 59000  # PTS span alone would underestimate the duration
    entry = _entry(path, default_config)
    block = entry["duration_reconciliation"]
    assert block["media_duration_seconds"] == pytest.approx(2.0)
    assert block["code"] == DURATION_CONSISTENT
    assert entry["verdict"] == "CFR_OK"


def test_tkhd_duration_is_exposed_in_movie_timescale(fixture_dir: Path):
    result = probe_mp4(fixture_dir / "cfr_30.mp4")
    assert result.primary_track.tkhd_duration_ticks == 2000
    assert result.movie_timescale == 1000
    cfr_2997 = probe_mp4(fixture_dir / "cfr_2997.mp4")
    assert cfr_2997.primary_track.tkhd_duration_ticks == 2002


def test_error_entry_carries_null_reconciliation(default_config, tmp_path: Path):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not-a-valid-mp4")
    outcome = process_file(broken, default_config, display_path=broken.name)
    assert outcome.entry["status"] == "error"
    assert outcome.entry["duration_reconciliation"] is None
