from __future__ import annotations

import json

import pytest

from fpsprobe.errors import RunNotFound
from fpsprobe.pipeline.batch import run_batch
from fpsprobe.report.json_report import build_json_report, write_json_report
from fpsprobe.report.markdown_report import build_markdown_report
from fpsprobe.store.ledger import Ledger


def test_json_report_key_contract(default_config, expected_report, assert_structure):
    result = run_batch(default_config)
    with Ledger(default_config.db_path) as ledger:
        report = build_json_report(ledger, result.run_id)
    assert set(report) == {"run_meta", "summary", "files", "failures"}
    assert set(report["run_meta"]) == {
        "run_id",
        "config_fingerprint",
        "started_at",
        "finished_at",
        "tool_version",
    }
    assert set(report["summary"]) == {
        "total_files",
        "ok",
        "review",
        "reject",
        "error",
        "verdicts",
        "source_dir",
    }
    assert_structure(report, expected_report)
    assert isinstance(report["files"], list) and len(report["files"]) == 7
    assert all("reason_codes" in item for item in report["files"])


def test_markdown_overview_and_flagged_details(default_config):
    result = run_batch(default_config)
    with Ledger(default_config.db_path) as ledger:
        report = build_json_report(ledger, result.run_id)
    markdown = build_markdown_report(report)
    assert "# fpsprobe batch report" in markdown
    assert "| Total files | 7 |" in markdown
    assert "| ok | 3 |" in markdown
    assert "| review | 3 |" in markdown
    assert "| reject | 1 |" in markdown
    assert "### broken_order.mp4" in markdown
    assert "### cfr_2997.mp4" in markdown
    assert "### vfr_jitter.mp4" in markdown
    assert "### no_declared_fps.mp4" in markdown
    assert "### cfr_30.mp4" not in markdown
    assert "### multi_track.mp4" not in markdown
    assert "TIMESTAMP_BROKEN" in markdown
    assert "Measured FPS: 29.970" in markdown


def test_json_report_file_round_trip(default_config, tmp_path):
    result = run_batch(default_config)
    with Ledger(default_config.db_path) as ledger:
        report = build_json_report(ledger, result.run_id)
    destination = write_json_report(report, tmp_path / "nested" / "report.json")
    loaded = json.loads(destination.read_text(encoding="utf-8"))
    assert loaded == report
    assert destination.read_text(encoding="utf-8").endswith("\n")


def test_missing_run_is_explicit(default_config):
    with Ledger(default_config.db_path) as ledger:
        with pytest.raises(RunNotFound):
            build_json_report(ledger, "missing-run")
