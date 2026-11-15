from __future__ import annotations

import json

import pytest

from fpsprobe.errors import RunFinishedError


def _summary(total: int, ok: int, review: int, reject: int = 0, error: int = 0) -> dict:
    return {
        "total_files": total,
        "ok": ok,
        "review": review,
        "reject": reject,
        "error": error,
        "verdicts": {
            "CFR_OK": ok,
            "CFR_MISMATCH": review,
            "VFR_SUSPECT": 0,
            "TIMESTAMP_BROKEN": reject,
        },
        "source_dir": "fixtures/out",
    }


def test_idempotent_files_verdicts_and_finish(ledger, entry_factory):
    run_id = ledger.create_run(
        "fingerprint-a", "fixtures/out", run_id="run-001", started_at="2026-01-01T00:00:00+00:00"
    )
    first = ledger.upsert_file(run_id, "cfr_30.mp4", "a" * 64, 100)
    second = ledger.upsert_file(run_id, "cfr_30.mp4", "a" * 64, 100)
    assert first == second
    ok_entry = entry_factory("cfr_30.mp4")
    ledger.record_verdict(run_id, first, ok_entry)
    ledger.record_verdict(run_id, first, ok_entry)
    ledger.record_timeline(
        run_id, first, 60, 240, ok_entry["timestamps"]["delta_ticks"], [0, 1000], [1000]
    )
    review = ledger.upsert_file(run_id, "cfr_2997.mp4", "b" * 64, 200)
    ledger.record_verdict(
        run_id,
        review,
        entry_factory("cfr_2997.mp4", verdict="CFR_MISMATCH", classification="review"),
    )
    counts = ledger.counts()
    assert counts == {
        "runs": 1,
        "files": 2,
        "file_verdicts": 2,
        "timeline_samples": 1,
    }
    assert len(ledger.list_files(run_id, "review")) == 1
    assert len(ledger.list_files(run_id, "CFR_MISMATCH")) == 1
    assert len(ledger.list_files(run_id, "reject")) == 0

    assert ledger.finish_run(run_id, _summary(2, 1, 1)) is True
    run = ledger.query_run(run_id)
    assert run is not None
    assert run["status"] == "finished"
    assert run["summary"]["review"] == 1
    finished_at = run["finished_at"]
    assert ledger.finish_run(run_id, _summary(2, 1, 1)) is False
    assert ledger.query_run(run_id)["finished_at"] == finished_at
    with pytest.raises(RunFinishedError):
        ledger.upsert_file(run_id, "late.mp4", "c" * 64, 1)


def test_file_rows_are_reused_across_runs_with_same_fingerprint(ledger, entry_factory):
    first_run = ledger.create_run("fingerprint-a", run_id="run-a")
    file_id = ledger.upsert_file(first_run, "cfr_30.mp4", "d" * 64, 123)
    ledger.record_verdict(first_run, file_id, entry_factory("cfr_30.mp4"))
    ledger.finish_run(first_run, _summary(1, 1, 0))
    second_run = ledger.create_run("fingerprint-a", run_id="run-b")
    reused = ledger.upsert_file(second_run, "cfr_30.mp4", "d" * 64, 123)
    assert reused == file_id
    assert ledger.counts()["files"] == 1
    ledger.record_verdict(second_run, reused, entry_factory("cfr_30.mp4"))
    ledger.finish_run(second_run, _summary(1, 1, 0))
    assert ledger.counts()["runs"] == 2
    assert ledger.counts()["file_verdicts"] == 2


def test_timeline_above_threshold_stores_exact_anchor_sample(ledger, entry_factory):
    run_id = ledger.create_run("fingerprint-b", run_id="run-large")
    file_id = ledger.upsert_file(run_id, "large.mp4", "e" * 64, 1000)
    entry = entry_factory("large.mp4")
    ledger.record_verdict(run_id, file_id, entry)
    pts = list(range(0, 5000 * 1000, 1000))
    deltas = [1000] * (len(pts) - 1)
    ledger.record_timeline(
        run_id, file_id, len(pts), 100, entry["timestamps"]["delta_ticks"], pts, deltas
    )
    row = ledger.conn.execute(
        "SELECT * FROM timeline_samples WHERE run_id=?", (run_id,)
    ).fetchone()
    assert row["frame_count"] == len(pts)
    assert row["stored_frame_count"] == 100
    assert row["truncated"] == 1
    stored_pts = json.loads(row["pts_ticks_json"])
    stored_deltas = json.loads(row["deltas_ticks_json"])
    assert len(stored_pts) == 100
    assert stored_pts[0] == pts[0]
    assert stored_pts[-1] == pts[-1]
    summary = json.loads(row["summary_json"])
    assert summary["original_frame_count"] == len(pts)
    assert summary["stored_frame_count"] == 100
    assert summary["truncated"] is True
    assert summary["duration_deviation_ticks"] == 0
    assert sum(stored_deltas) == stored_pts[-1] - stored_pts[0]


def test_query_missing_run_is_none(ledger):
    assert ledger.query_run("does-not-exist") is None
