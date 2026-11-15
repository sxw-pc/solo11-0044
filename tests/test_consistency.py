from __future__ import annotations

from fpsprobe.analysis.classify import classify
from fpsprobe.analysis.consistency import evaluate_consistency
from fpsprobe.models import Thresholds

BASE = Thresholds(
    fps_abs_tolerance=0.01,
    fps_rel_tolerance=0.0005,
    cfr_delta_rel_tolerance=0.01,
    vfr_jitter_threshold=0.08,
    vfr_p95_p50_threshold=1.5,
    mismatch_review_abs_fps=0.02,
)
MAPPING = {
    "CFR_OK": "ok",
    "CFR_MISMATCH": "review",
    "VFR_SUSPECT": "review",
    "TIMESTAMP_BROKEN": "reject",
}


def test_cfr_ok():
    result = evaluate_consistency(30.0, [1000] * 59, 30000, BASE)
    assert result["verdict"] == "CFR_OK"
    assert result["reason_codes"] == ["FPS_CONSISTENT"]
    assert result["stable"] is True
    assert result["measured_fps"] == 30.0


def test_cfr_mismatch_2997():
    result = evaluate_consistency(30.0, [1001] * 59, 30000, BASE)
    assert result["verdict"] == "CFR_MISMATCH"
    assert "DECLARED_FPS_MISMATCH" in result["reason_codes"]
    assert abs(result["fps_delta"] - (-0.02997002997)) < 1e-6
    assert result["selected_measured_fps"] == result["measured_fps"]
    block = result["estimate_block"]
    assert block["selected_estimator"] == "candidate_interval"
    assert block["estimators"]["candidate_interval"]["interval_ticks"] == 1001


def test_vfr_suspect_from_dispersion():
    deltas = [500, 1500] * 30
    result = evaluate_consistency(30.0, deltas, 30000, BASE)
    assert result["verdict"] == "VFR_SUSPECT"
    assert "HIGH_JITTER" in result["reason_codes"]
    assert result["reason_text"]


def test_timestamp_broken():
    result = evaluate_consistency(30.0, [1000, 0, -500, 1000], 30000, BASE)
    assert result["verdict"] == "TIMESTAMP_BROKEN"
    assert result["non_positive_indices"] == [1, 2]
    assert "TIMESTAMP_REVERSAL" in result["reason_codes"]
    assert "DUPLICATE_TIMESTAMP" in result["reason_codes"]


def test_small_jitter_inside_tolerance_is_cfr_ok():
    deltas = [995, 1005] * 29
    result = evaluate_consistency(30.0, deltas, 30000, BASE)
    assert result["stable"] is True
    assert result["verdict"] == "CFR_OK"


def test_no_intervals_is_diagnosable():
    result = evaluate_consistency(30.0, [], 30000, BASE)
    assert result["verdict"] == "CFR_OK"
    assert result["reason_codes"] == ["NO_FRAME_INTERVALS"]


def test_thresholds_are_external_configuration():
    loose = Thresholds(
        fps_abs_tolerance=1.0,
        fps_rel_tolerance=1.0,
        cfr_delta_rel_tolerance=1.0,
        vfr_jitter_threshold=10.0,
        vfr_p95_p50_threshold=10.0,
        mismatch_review_abs_fps=1.0,
    )
    result = evaluate_consistency(30.0, [500, 1500] * 30, 30000, loose)
    assert result["verdict"] == "CFR_OK"


def test_classification_mapping_and_review_threshold():
    high = classify("CFR_MISMATCH", ["DECLARED_FPS_MISMATCH"], 0.03, BASE, MAPPING)
    assert high["classification"] == "review"
    assert "MISMATCH_ABOVE_REVIEW_THRESHOLD" in high["reason_codes"]
    low = classify("CFR_MISMATCH", ["DECLARED_FPS_MISMATCH"], 0.005, BASE, MAPPING)
    assert low["classification"] == "ok"
    assert "MISMATCH_BELOW_REVIEW_THRESHOLD" in low["reason_codes"]
    broken = classify("TIMESTAMP_BROKEN", ["NON_POSITIVE_DELTA"], None, BASE, MAPPING)
    assert broken["classification"] == "reject"


def test_short_intervals_in_stable_segment_do_not_drag_selected_fps():
    deltas = (
        [1000] * 14 + [990] + [1000] * 14 + [990]
        + [1000] * 14 + [990] + [1000] * 13 + [990]
    )
    result = evaluate_consistency(30.0, deltas, 30000, BASE)
    assert result["stable"] is True
    # the legacy mean estimator is dragged past the fps tolerances
    assert result["measured_fps"] > 30.01
    # but the candidate interval from the stable segment is selected instead
    assert result["selected_measured_fps"] == 30.0
    assert result["estimate_block"]["selected_estimator"] == "candidate_interval"
    assert result["estimate_block"]["estimators"]["mean"]["fps"] > 30.01
    assert result["verdict"] == "CFR_OK"


def test_candidate_interval_is_not_pulled_toward_declared():
    # a declared 24 fps cannot select a matching estimator on stable 30 fps media
    result = evaluate_consistency(24.0, [1000] * 59, 30000, BASE)
    assert result["verdict"] == "CFR_MISMATCH"
    assert result["selected_measured_fps"] == 30.0
    block = result["estimate_block"]
    assert block["estimators"]["candidate_interval"]["interval_ticks"] == 1000
    assert block["estimators"]["candidate_interval"]["rel_delta"] == 0.25


def test_unstable_sequence_never_selects_candidate_estimator():
    deltas = [500, 1500] * 29 + [500]  # 59 deltas, same shape as vfr_jitter.mp4
    result = evaluate_consistency(30.0, deltas, 30000, BASE)
    assert result["verdict"] == "VFR_SUSPECT"
    block = result["estimate_block"]
    assert block["stable_segment_count"] == 0
    assert block["estimators"]["candidate_interval"]["fps"] is None
    assert block["selected_estimator"] == "median"
    assert result["selected_measured_fps"] == 60.0


def test_missing_declared_fps_falls_back_to_median():
    result = evaluate_consistency(None, [1000] * 59, 30000, BASE)
    assert result["verdict"] == "CFR_MISMATCH"
    assert "DECLARED_FPS_MISSING" in result["reason_codes"]
    assert result["estimate_block"]["selected_estimator"] == "median"
    assert result["selected_measured_fps"] == 30.0
    assert result["estimate_block"]["estimators"]["mean"]["rel_delta"] is None


def test_timestamp_broken_is_not_masked_by_estimator_selection():
    result = evaluate_consistency(30.0, [1000, 0, -500, 1000], 30000, BASE)
    assert result["verdict"] == "TIMESTAMP_BROKEN"
    assert result["estimate_block"]["selected_measured_fps"] is not None
    assert result["estimate_block"]["estimators"]["candidate_interval"]["fps"] is None


def test_no_intervals_estimate_block_is_none_safe():
    result = evaluate_consistency(30.0, [], 30000, BASE)
    assert result["selected_measured_fps"] is None
    block = result["estimate_block"]
    assert block["selected_estimator"] is None
    for estimator in block["estimators"].values():
        assert estimator["fps"] is None
