from __future__ import annotations

import json
from pathlib import Path

from fixtures.generator import TrackSpec, build_mp4
from fpsprobe.analysis.consistency import evaluate_consistency
from fpsprobe.analysis.disorder import (
    CTTS_COMBINATION_REVERSAL,
    CTTS_DUPLICATE_OFFSET,
    CTTS_NEGATIVE_OFFSET_REVERSAL,
    STTS_ZERO_DELTA,
    attribute_disorder,
)
from fpsprobe.models import Thresholds
from fpsprobe.pipeline.runner import process_file
from fpsprobe.report.json_report import build_json_report
from fpsprobe.report.markdown_report import build_markdown_report
from fpsprobe.pipeline.batch import run_batch
from fpsprobe.store.ledger import Ledger

BASE = Thresholds(
    fps_abs_tolerance=0.01,
    fps_rel_tolerance=0.0005,
    cfr_delta_rel_tolerance=0.01,
    vfr_jitter_threshold=0.08,
    vfr_p95_p50_threshold=1.5,
    mismatch_review_abs_fps=0.02,
)


def test_negative_ctts_offset_is_attributed_per_sample():
    result = attribute_disorder(
        [-1000, 2000],
        [1000, 1000, 1000],
        [0, -2000, 0],
        [0, -1000, 1000],
        track_id=1,
    )
    assert result is not None
    assert result["trigger_count"] == 1
    assert result["reason_codes"] == [CTTS_NEGATIVE_OFFSET_REVERSAL]
    trigger = result["triggers"][0]
    assert trigger["sample_index"] == 1
    assert trigger["prev_sample_index"] == 0
    assert trigger["delta_index"] == 0
    assert trigger["stts_sample_delta"] == 1000
    assert trigger["ctts_composition_offset"] == -2000
    assert trigger["prev_ctts_composition_offset"] == 0
    assert trigger["pts_delta_ticks"] == -1000
    assert trigger["primary_cause"] == CTTS_NEGATIVE_OFFSET_REVERSAL


def test_zero_stts_delta_without_ctts_is_stts_only():
    result = attribute_disorder([1000, 1000, 0], [1000, 1000, 0, 1000], None)
    assert result is not None
    trigger = result["triggers"][0]
    assert trigger["sample_index"] == 3
    assert trigger["stts_sample_delta"] == 0
    assert trigger["codes"] == [STTS_ZERO_DELTA]
    assert trigger["primary_cause"] == STTS_ZERO_DELTA
    assert trigger["ctts_composition_offset"] is None
    assert trigger["prev_ctts_composition_offset"] is None
    assert "n/a" in trigger["detail"]


def test_zero_stts_with_matching_ctts_offset_marks_duplicate_offset():
    result = attribute_disorder([0], [0, 1000], [300, 300])
    trigger = result["triggers"][0]
    assert trigger["codes"] == [STTS_ZERO_DELTA, CTTS_DUPLICATE_OFFSET]
    assert trigger["primary_cause"] == STTS_ZERO_DELTA


def test_positive_stts_with_ctts_step_back_is_combination_reversal():
    result = attribute_disorder([-1500], [1000, 1000], [2000, 500])
    trigger = result["triggers"][0]
    assert trigger["codes"] == [CTTS_COMBINATION_REVERSAL]
    assert trigger["primary_cause"] == CTTS_COMBINATION_REVERSAL
    assert trigger["stts_sample_delta"] == 1000
    assert trigger["ctts_composition_offset"] == 500


def test_negative_offset_outranks_zero_stts_as_primary_cause():
    result = attribute_disorder([-500], [0, 1000], [0, -500])
    trigger = result["triggers"][0]
    assert trigger["codes"] == [STTS_ZERO_DELTA, CTTS_NEGATIVE_OFFSET_REVERSAL]
    assert trigger["primary_cause"] == CTTS_NEGATIVE_OFFSET_REVERSAL


def test_misaligned_tables_disable_attribution():
    assert attribute_disorder([0], [1000], None) is None
    assert attribute_disorder([0], None, None) is None
    # A ctts table that cannot be aligned falls back to stts-only attribution
    # rather than dropping the (still truthful) stts findings.
    result = attribute_disorder([0], [0, 1000], [1, 2, 3])
    assert result["triggers"][0]["codes"] == [STTS_ZERO_DELTA]
    assert result["triggers"][0]["ctts_composition_offset"] is None


def test_clean_sequence_has_no_triggers():
    result = attribute_disorder([1000, 1000], [1000, 1000, 1000], None)
    assert result is not None
    assert result["trigger_count"] == 0
    assert result["triggers"] == []
    assert result["reason_codes"] == []


def test_trigger_list_is_capped_but_counts_stay_full():
    deltas = [0] * 10
    stts = [0] * 11
    result = attribute_disorder(deltas, stts, None, max_triggers=3)
    assert result["trigger_count"] == 10
    assert result["truncated"] is True
    assert len(result["triggers"]) == 3
    assert result["causes"] == {STTS_ZERO_DELTA: 10}


def test_consistency_without_tables_keeps_legacy_reason_codes():
    result = evaluate_consistency(30.0, [1000, 0, -500, 1000], 30000, BASE)
    assert result["verdict"] == "TIMESTAMP_BROKEN"
    assert result["attribution"] is None
    assert result["reason_codes"] == [
        "TIMESTAMP_REVERSAL",
        "DUPLICATE_TIMESTAMP",
        "NON_POSITIVE_DELTA",
    ]


def test_consistency_with_tables_appends_attribution_codes():
    result = evaluate_consistency(
        30.0,
        [-1000],
        30000,
        BASE,
        stts_deltas=[1000, 1000],
        ctts_offsets=[0, -2000],
        pts_ticks=[0, -1000],
        track_id=7,
    )
    assert result["verdict"] == "TIMESTAMP_BROKEN"
    assert result["reason_codes"][-1] == CTTS_NEGATIVE_OFFSET_REVERSAL
    assert result["attribution"]["track_id"] == 7
    assert "sample indices [1]" in result["reason_text"]


def test_broken_order_entry_carries_structured_attribution(
    default_config, fixture_dir: Path
):
    outcome = process_file(fixture_dir / "broken_order.mp4", default_config)
    entry = outcome.entry
    assert entry["verdict"] == "TIMESTAMP_BROKEN"
    assert entry["classification"] == "reject"
    assert entry["reason_codes"] == [
        "TIMESTAMP_REVERSAL",
        "DUPLICATE_TIMESTAMP",
        "NON_POSITIVE_DELTA",
        CTTS_NEGATIVE_OFFSET_REVERSAL,
    ]
    attribution = entry["timestamps"]["disorder_attribution"]
    assert attribution["track_id"] == 1
    assert attribution["trigger_count"] == 3
    assert attribution["truncated"] is False
    assert attribution["causes"] == {CTTS_NEGATIVE_OFFSET_REVERSAL: 3}
    samples = [trigger["sample_index"] for trigger in attribution["triggers"]]
    assert samples == [10, 12, 14]
    for trigger in attribution["triggers"]:
        assert trigger["stts_sample_delta"] == 1000
        assert trigger["ctts_composition_offset"] == -2000
        assert trigger["primary_cause"] == CTTS_NEGATIVE_OFFSET_REVERSAL
    deltas = entry["timestamps"]["non_positive_indices"]
    assert [t["delta_index"] for t in attribution["triggers"]] == deltas


def test_ctts_v0_step_back_attributes_combination_reversal(
    default_config, tmp_path: Path
):
    path = tmp_path / "v0_step_back.mp4"
    path.write_bytes(
        build_mp4(
            [
                TrackSpec(
                    1,
                    "vide",
                    30000,
                    ((3, 1000),),
                    ctts=((1, 3000), (2, 0)),
                    ctts_version=0,
                    width=320,
                    height=180,
                )
            ]
        )
    )
    outcome = process_file(path, default_config)
    entry = outcome.entry
    assert entry["verdict"] == "TIMESTAMP_BROKEN"
    attribution = entry["timestamps"]["disorder_attribution"]
    assert attribution["trigger_count"] == 1
    trigger = attribution["triggers"][0]
    assert trigger["sample_index"] == 1
    assert trigger["stts_sample_delta"] == 1000
    assert trigger["ctts_composition_offset"] == 0
    assert trigger["prev_ctts_composition_offset"] == 3000
    assert trigger["codes"] == [CTTS_COMBINATION_REVERSAL]
    assert CTTS_COMBINATION_REVERSAL in entry["reason_codes"]


def test_multi_entry_stts_zero_delta_points_at_correct_sample(
    default_config, tmp_path: Path
):
    path = tmp_path / "stts_zero.mp4"
    path.write_bytes(
        build_mp4(
            [
                TrackSpec(
                    1,
                    "vide",
                    30000,
                    ((2, 1000), (1, 0), (2, 1000)),
                    width=320,
                    height=180,
                )
            ]
        )
    )
    outcome = process_file(path, default_config)
    entry = outcome.entry
    assert entry["verdict"] == "TIMESTAMP_BROKEN"
    attribution = entry["timestamps"]["disorder_attribution"]
    # The zero stts entry is the third sample's delta; the PTS of sample 3
    # (0-based) is the one that fails to advance past sample 2.
    assert [t["sample_index"] for t in attribution["triggers"]] == [3]
    trigger = attribution["triggers"][0]
    assert trigger["stts_sample_delta"] == 0
    assert trigger["codes"] == [STTS_ZERO_DELTA]
    assert trigger["ctts_composition_offset"] is None
    assert STTS_ZERO_DELTA in entry["reason_codes"]
    assert CTTS_DUPLICATE_OFFSET not in entry["reason_codes"]


def test_multi_entry_stts_with_flat_ctts_marks_duplicate_offset(
    default_config, tmp_path: Path
):
    path = tmp_path / "stts_zero_ctts.mp4"
    path.write_bytes(
        build_mp4(
            [
                TrackSpec(
                    1,
                    "vide",
                    30000,
                    ((2, 1000), (1, 0), (2, 1000)),
                    ctts=((5, 500),),
                    ctts_version=0,
                    width=320,
                    height=180,
                )
            ]
        )
    )
    outcome = process_file(path, default_config)
    trigger = outcome.entry["timestamps"]["disorder_attribution"]["triggers"][0]
    assert trigger["sample_index"] == 3
    assert trigger["codes"] == [STTS_ZERO_DELTA, CTTS_DUPLICATE_OFFSET]
    assert trigger["ctts_composition_offset"] == 500


def test_audio_track_disorder_is_not_misattributed(
    default_config, tmp_path: Path
):
    path = tmp_path / "audio_zero.mp4"
    path.write_bytes(
        build_mp4(
            [
                TrackSpec(1, "vide", 30000, ((60, 1000),), width=320, height=180),
                TrackSpec(2, "soun", 48000, ((2, 1024), (1, 0), (91, 1024))),
            ]
        )
    )
    outcome = process_file(path, default_config)
    entry = outcome.entry
    assert entry["verdict"] == "CFR_OK"
    assert entry["reason_codes"] == ["FPS_CONSISTENT"]
    assert entry["timestamps"]["disorder_attribution"] is None


def test_clean_fixtures_have_no_attribution(default_config, fixture_dir: Path):
    for name in ("cfr_30.mp4", "cfr_2997.mp4", "vfr_jitter.mp4", "moov_at_end.mp4"):
        entry = process_file(fixture_dir / name, default_config).entry
        assert entry["timestamps"]["disorder_attribution"] is None, name
        assert not any(
            code.startswith("CTTS_") or code.startswith("STTS_")
            for code in entry["reason_codes"]
        ), name
    moov = process_file(fixture_dir / "moov_at_end.mp4", default_config).entry
    assert moov["reason_codes"] == ["FPS_CONSISTENT"]
    assert moov["container"]["audio_stream_count"] == 1


def test_attribution_survives_ledger_round_trip_and_markdown(default_config):
    result = run_batch(default_config)
    with Ledger(default_config.db_path) as ledger:
        report = build_json_report(ledger, result.run_id)
    files = {item["path"]: item for item in report["files"]}
    broken = files["broken_order.mp4"]
    attribution = broken["timestamps"]["disorder_attribution"]
    assert attribution["trigger_count"] == 3
    assert [t["sample_index"] for t in attribution["triggers"]] == [10, 12, 14]
    assert CTTS_NEGATIVE_OFFSET_REVERSAL in broken["reason_codes"]
    for name in ("cfr_30.mp4", "cfr_2997.mp4", "vfr_jitter.mp4", "moov_at_end.mp4"):
        assert files[name]["timestamps"]["disorder_attribution"] is None, name

    markdown = build_markdown_report(report)
    section = markdown.split("### broken_order.mp4", 1)[1].split("###", 1)[0]
    assert "Timestamp disorder attribution: 3 trigger(s) on primary track 1" in section
    assert "sample 10: pts_delta=-1000 ticks, stts_sample_delta=1000" in section
    assert "ctts_offset=-2000 (prev 0) -> CTTS_NEGATIVE_OFFSET_REVERSAL" in section
    clean_section = markdown.split("### cfr_2997.mp4", 1)[1].split("###", 1)[0]
    assert "disorder attribution" not in clean_section


def test_report_json_serialises_attribution(default_config, tmp_path: Path):
    result = run_batch(default_config)
    payload = json.loads(result.json_path.read_text(encoding="utf-8"))
    broken = next(
        item for item in payload["files"] if item["path"] == "broken_order.mp4"
    )
    assert broken["timestamps"]["disorder_attribution"]["causes"] == {
        CTTS_NEGATIVE_OFFSET_REVERSAL: 3
    }
