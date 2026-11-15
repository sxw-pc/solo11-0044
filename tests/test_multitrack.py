from __future__ import annotations

import json
from pathlib import Path

import pytest

from fpsprobe.analysis.consistency import (
    MIN_USABLE_INTERVALS,
    evaluate_track_timing,
)
from fpsprobe.container.base import recommend_primary_track
from fpsprobe.models import Thresholds
from fpsprobe.pipeline.runner import process_file

BASE = Thresholds(
    fps_abs_tolerance=0.01,
    fps_rel_tolerance=0.0005,
    cfr_delta_rel_tolerance=0.01,
    vfr_jitter_threshold=0.08,
    vfr_p95_p50_threshold=1.5,
    mismatch_review_abs_fps=0.02,
)


def _entry(config, path: Path, primary_track=None) -> dict:
    outcome = process_file(path, config, primary_track=primary_track)
    assert outcome.entry["status"] == "ok"
    return outcome.entry


def _track(block: dict, track_id: int) -> dict:
    return next(t for t in block["tracks"] if t["track_id"] == track_id)


def test_multi_track_arbitration_block(default_config, fixture_dir: Path):
    entry = _entry(default_config, fixture_dir / "multi_track.mp4")
    block = entry["track_arbitration"]
    assert block["strategy"] == "first_video"
    assert block["configured_track_id"] == 1
    assert block["recommended_track_id"] == 1
    assert block["strategy_matches_recommendation"] is True
    assert block["reason_codes"] == ["RECOMMENDED_STABLE_CONSISTENT"]
    # Four video tracks are assessed; the audio track is not arbitrated.
    assert [t["track_id"] for t in block["tracks"]] == [1, 2, 3, 4]
    assert sum(1 for t in block["tracks"] if t["is_recommended"]) == 1

    good = _track(block, 1)
    assert good["usability"] == "usable"
    assert good["declared_status"] == "consistent"
    assert good["declared_fps"] == 30.0
    assert good["measured_fps"] == 30.0
    assert good["fps_deviation_band"] == {"min_rel": 0.0, "max_rel": 0.0}
    assert good["is_configured_primary"] is True
    assert good["is_recommended"] is True

    other = _track(block, 2)
    assert other["timescale"] == 24000
    assert other["usability"] == "usable"
    assert other["measured_fps"] == 24.0


def test_broken_and_tiny_tracks_are_never_recommended(default_config, fixture_dir: Path):
    block = _entry(default_config, fixture_dir / "multi_track.mp4")["track_arbitration"]
    broken = _track(block, 3)
    assert broken["usability"] == "unusable"
    assert broken["non_positive_count"] == 1
    assert "NON_POSITIVE_DELTA" in broken["reason_codes"]
    assert broken["is_recommended"] is False

    tiny = _track(block, 4)
    assert tiny["usability"] == "unusable"
    assert tiny["interval_count"] == 1
    assert "INSUFFICIENT_SAMPLES" in tiny["reason_codes"]
    assert tiny["is_recommended"] is False


def test_strategy_mismatch_is_reported_stably(default_config, fixture_dir: Path):
    path = fixture_dir / "multi_track.mp4"
    first = _entry(default_config, path, primary_track=1)["track_arbitration"]
    second = _entry(default_config, path, primary_track=1)["track_arbitration"]
    assert first == second
    assert first["configured_track_id"] == 2
    assert first["recommended_track_id"] == 1
    assert first["strategy_matches_recommendation"] is False
    assert "STRATEGY_DIFFERS_FROM_RECOMMENDATION" in first["reason_codes"]
    # The mismatch report is advisory: the top-level verdict still follows the
    # configured primary track.
    assert first["tracks"][1]["is_configured_primary"] is True


def test_missing_declared_rate_does_not_drop_track(default_config, fixture_dir: Path):
    entry = _entry(default_config, fixture_dir / "no_declared_fps.mp4")
    block = entry["track_arbitration"]
    track = _track(block, 1)
    assert track["declared_fps"] is None
    assert track["declared_status"] == "missing"
    assert track["fps_deviation_band"] is None
    assert track["measured_fps"] == 30.0
    assert track["usability"] == "usable"
    assert block["recommended_track_id"] == 1
    assert block["reason_codes"] == ["RECOMMENDED_STABLE_NO_DECLARED"]
    assert block["strategy_matches_recommendation"] is True


def test_no_usable_track_recommends_nothing(default_config, fixture_dir: Path):
    block = _entry(default_config, fixture_dir / "broken_order.mp4")["track_arbitration"]
    assert block["recommended_track_id"] is None
    assert "NO_USABLE_TRACK" in block["reason_codes"]
    assert "STRATEGY_DIFFERS_FROM_RECOMMENDATION" in block["reason_codes"]
    assert all(t["usability"] == "unusable" for t in block["tracks"])


def test_degraded_track_is_fallback_recommendation(default_config, fixture_dir: Path):
    block = _entry(default_config, fixture_dir / "vfr_jitter.mp4")["track_arbitration"]
    assert block["recommended_track_id"] == 1
    assert block["reason_codes"] == ["RECOMMENDED_DEGRADED_FALLBACK"]
    assert _track(block, 1)["usability"] == "degraded"


def test_arbitration_never_contradicts_primary_analysis(default_config, fixture_dir: Path):
    for path in sorted(fixture_dir.glob("*.mp4")):
        entry = _entry(default_config, path)
        block = entry["track_arbitration"]
        primary = next(t for t in block["tracks"] if t["is_configured_primary"])
        assert primary["verdict"] == entry["verdict"], path.name
        assert primary["declared_fps"] == entry["container"]["declared_fps"], path.name
        assert primary["measured_fps"] == entry["timestamps"]["measured_fps"], path.name
        assert primary["track_id"] == entry["container"]["primary_track_id"], path.name


def test_repeated_runs_are_identical(default_config, fixture_dir: Path):
    path = fixture_dir / "multi_track.mp4"
    first = _entry(default_config, path)
    second = _entry(default_config, path)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_deviation_band_compares_on_seconds_basis():
    # Identical tick deltas mean different rates on different timescales; the
    # comparison must happen in frames per second, not in ticks.
    fast = evaluate_track_timing(30.0, [1000] * 59, 30000, BASE)
    slow = evaluate_track_timing(24.0, [1000] * 47, 24000, BASE)
    assert fast["measured_fps"] == 30.0
    assert slow["measured_fps"] == 24.0
    assert fast["fps_deviation_band"] == {"min_rel": 0.0, "max_rel": 0.0}
    assert slow["fps_deviation_band"] == {"min_rel": 0.0, "max_rel": 0.0}

    mixed = evaluate_track_timing(30.0, [500, 1500] * 30, 30000, BASE)
    band = mixed["fps_deviation_band"]
    # Instantaneous rates are 60 fps and 20 fps against a declared 30 fps.
    assert band == {"min_rel": pytest.approx(-1 / 3), "max_rel": pytest.approx(1.0)}
    assert mixed["usability"] == "degraded"


def test_track_timing_usability_rules():
    broken = evaluate_track_timing(30.0, [1000, 0, -500] + [1000] * 20, 30000, BASE)
    assert broken["usability"] == "unusable"
    assert broken["non_positive_count"] == 2

    tiny = evaluate_track_timing(30.0, [1000] * (MIN_USABLE_INTERVALS - 1), 30000, BASE)
    assert tiny["usability"] == "unusable"
    assert "INSUFFICIENT_SAMPLES" in tiny["reason_codes"]

    enough = evaluate_track_timing(30.0, [1000] * MIN_USABLE_INTERVALS, 30000, BASE)
    assert enough["usability"] == "usable"

    missing = evaluate_track_timing(None, [1000] * 59, 30000, BASE)
    assert missing["usability"] == "usable"
    assert missing["declared_status"] == "missing"
    assert missing["fps_deviation_band"] is None


def test_recommendation_priority_and_tie_break():
    def assessment(track_id, usability, declared_status, samples, area, **extra):
        return {
            "track_id": track_id,
            "usability": usability,
            "declared_status": declared_status,
            "sample_count": samples,
            "width": area,
            "height": 1,
            **extra,
        }

    tracks = [
        assessment(1, "unusable", "consistent", 1000, 4096),
        assessment(2, "usable", "missing", 100, 640),
        assessment(3, "usable", "consistent", 50, 640),
        assessment(4, "degraded", "consistent", 5000, 4096),
    ]
    pick = recommend_primary_track(tracks)
    assert pick["recommended_track_id"] == 3
    assert pick["reason_codes"] == ["RECOMMENDED_STABLE_CONSISTENT"]

    # Sample count outranks resolution, the lowest track id breaks ties.
    tied = recommend_primary_track(
        [
            assessment(7, "usable", "consistent", 60, 320),
            assessment(3, "usable", "consistent", 60, 320),
            assessment(5, "usable", "consistent", 40, 1920),
        ]
    )
    assert tied["recommended_track_id"] == 3

    degraded_only = recommend_primary_track(
        [assessment(1, "unusable", "unjudged", 2, 0), assessment(2, "degraded", "unjudged", 9, 0)]
    )
    assert degraded_only["recommended_track_id"] == 2
    assert degraded_only["reason_codes"] == ["RECOMMENDED_DEGRADED_FALLBACK"]

    assert recommend_primary_track([assessment(1, "unusable", "unjudged", 2, 0)])[
        "recommended_track_id"
    ] is None
    assert recommend_primary_track([])["reason_codes"] == ["NO_USABLE_TRACK"]


def test_probe_cli_json_contains_arbitration(fixture_dir: Path, tmp_path: Path, capsys):
    from fpsprobe.cli import main

    config = tmp_path / "config.json"
    raw = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "config" / "default.json").read_text()
    )
    raw["input_dirs"] = [str(fixture_dir)]
    raw["report"]["output_dir"] = str(tmp_path / "reports")
    raw["database"]["path"] = str(tmp_path / "cli.db")
    config.write_text(json.dumps(raw), encoding="utf-8")

    code = main(["probe", str(fixture_dir / "multi_track.mp4"), "--config", str(config)])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    block = payload["track_arbitration"]
    assert block["recommended_track_id"] == 1
    assert len(block["tracks"]) == 4
    assert payload["verdict"] == "CFR_OK"


@pytest.mark.parametrize("strategy", ["first_video", "longest_video", 0])
def test_recommendation_independent_of_strategy(default_config, fixture_dir: Path, strategy):
    block = _entry(default_config, fixture_dir / "multi_track.mp4", primary_track=strategy)[
        "track_arbitration"
    ]
    assert block["recommended_track_id"] == 1
