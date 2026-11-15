from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from fixtures.generator import generate_fixtures
from fpsprobe.errors import InputError
from fpsprobe.pipeline.batch import run_batch, scan_inputs
from fpsprobe.store.ledger import Ledger


def test_batch_end_to_end_and_expected_structure(
    default_config, expected_report, assert_structure, fixture_dir: Path
):
    result = run_batch(default_config)
    assert result.exit_code == 1
    assert result.summary["total_files"] == 5
    assert result.summary["ok"] == 2
    assert result.summary["review"] == 2
    assert result.summary["reject"] == 1
    assert result.summary["error"] == 0
    assert result.summary["verdicts"] == {
        "CFR_OK": 2,
        "CFR_MISMATCH": 1,
        "VFR_SUSPECT": 1,
        "TIMESTAMP_BROKEN": 1,
    }
    assert result.json_path.is_file()
    assert result.markdown_path.is_file()
    on_disk = json.loads(result.json_path.read_text(encoding="utf-8"))
    assert_structure(on_disk, expected_report)
    assert on_disk["summary"]["total_files"] == 5
    reports = result.json_path.read_text(encoding="utf-8") + result.markdown_path.read_text(
        encoding="utf-8"
    )
    assert str(fixture_dir) not in reports
    assert str(default_config.base_dir) not in reports


def test_rerun_reuses_file_rows(default_config):
    first = run_batch(default_config)
    second = run_batch(default_config)
    assert first.run_id != second.run_id
    with Ledger(default_config.db_path) as ledger:
        counts = ledger.counts()
    assert counts["runs"] == 2
    assert counts["files"] == 5
    assert counts["file_verdicts"] == 10


def test_corrupt_file_is_recorded_without_aborting(
    default_config, tmp_path: Path, fixture_dir: Path
):
    source = tmp_path / "input"
    source.mkdir()
    shutil.copy(fixture_dir / "cfr_30.mp4", source / "good.mp4")
    (source / "bad.mp4").write_bytes(b"not-a-valid-mp4")
    config = replace(
        default_config,
        input_dirs=(source,),
        report_dir=tmp_path / "corrupt-out",
        db_path=tmp_path / "corrupt-out" / "ledger.db",
    )
    result = run_batch(config)
    assert result.summary["total_files"] == 2
    assert result.summary["ok"] == 1
    assert result.summary["error"] == 1
    assert result.summary["reject"] == 0
    assert len(result.report["failures"]) == 1
    assert result.report["failures"][0]["path"] == "bad.mp4"
    assert result.report["failures"][0]["error_code"]
    assert result.exit_code == 0


def test_batch_samples_timelines_beyond_detail_limit(default_config, tmp_path: Path):
    config = replace(
        default_config,
        max_detail_frames=10,
        report_dir=tmp_path / "sampled-out",
        db_path=tmp_path / "sampled-out" / "ledger.db",
    )
    run_batch(config)
    with Ledger(config.db_path) as ledger:
        joined = ledger.conn.execute(
            "SELECT f.path, t.*, v.payload_json FROM timeline_samples t "
            "JOIN files f ON f.id = t.file_id "
            "JOIN file_verdicts v ON v.file_id = t.file_id ORDER BY f.path"
        ).fetchall()
    assert len(joined) == 5
    for row in joined:
        summary = json.loads(row["summary_json"])
        assert row["truncated"] == 1
        assert row["frame_count"] == 60
        assert row["stored_frame_count"] == 10
        stored_pts = json.loads(row["pts_ticks_json"])
        stored_deltas = json.loads(row["deltas_ticks_json"])
        assert stored_pts[0] == 0
        assert len(stored_pts) == 10
        assert summary["original_frame_count"] == 60
        assert summary["stored_frame_count"] == 10
        assert summary["truncated"] is True
        assert summary["duration_deviation_ticks"] == 0
        assert (
            summary["reconstructed_duration_ticks"]
            == summary["original_duration_ticks"]
            == stored_pts[-1] - stored_pts[0]
        )
        assert sum(stored_deltas) == stored_pts[-1] - stored_pts[0]
        timestamps = json.loads(row["payload_json"])["timestamps"]
        assert summary["non_positive_indices"] == timestamps["non_positive_indices"]
        assert summary["non_positive_count"] == timestamps["non_positive_count"]


def test_scan_inputs_is_stable_and_filtered(default_config):
    pairs = scan_inputs(default_config)
    names = [display for _path, display in pairs]
    assert names == sorted(names)
    assert len(names) == 5
    assert all(name.endswith(".mp4") for name in names)


def test_scan_empty_input_is_config_error(default_config, tmp_path: Path):
    source = tmp_path / "empty"
    source.mkdir()
    config = replace(default_config, input_dirs=(source,))
    with pytest.raises(InputError) as caught:
        scan_inputs(config)
    assert caught.value.code == "NO_INPUT_FILES"


def test_fixture_generation_is_byte_deterministic(tmp_path: Path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    records_a = generate_fixtures(first)
    records_b = generate_fixtures(second)
    assert len(records_a) == 5
    assert [item["sha256"] for item in records_a] == [
        item["sha256"] for item in records_b
    ]
    for record in records_a:
        assert (first / record["name"]).read_bytes() == (
            second / record["name"]
        ).read_bytes()
    assert (first / "manifest.json").read_bytes() == (second / "manifest.json").read_bytes()
