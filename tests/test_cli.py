from __future__ import annotations

import json
from pathlib import Path

from fpsprobe.cli import main
from fpsprobe.store.ledger import Ledger

ROOT = Path(__file__).resolve().parents[1]


def _config_file(tmp_path: Path, fixture_dir: Path, database: Path | None = None, drop=None) -> Path:
    raw = json.loads((ROOT / "fixtures" / "config" / "default.json").read_text(encoding="utf-8"))
    raw["input_dirs"] = [str(fixture_dir)]
    raw["report"]["output_dir"] = str(tmp_path / "reports")
    raw["database"]["path"] = str(database or (tmp_path / "cli.db"))
    if drop:
        section, key = drop
        del raw[section][key]
    path = tmp_path / "config.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def test_probe_outputs_valid_json_and_reject_exit_code(tmp_path, fixture_dir, capsys):
    config = _config_file(tmp_path, fixture_dir)
    code = main(["probe", str(fixture_dir / "vfr_jitter.mp4"), "--config", str(config)])
    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out)
    assert payload["verdict"] == "VFR_SUSPECT"
    assert payload["classification"] == "review"
    assert not Path(payload["path"]).is_absolute()

    code = main(["probe", str(fixture_dir / "broken_order.mp4"), "--config", str(config)])
    assert code == 1
    assert json.loads(capsys.readouterr().out)["classification"] == "reject"


def test_batch_writes_reports_and_db_override(tmp_path, fixture_dir, capsys):
    config = _config_file(tmp_path, fixture_dir)
    code = main(["batch", "--config", str(config)])
    first = json.loads(capsys.readouterr().out)
    assert code == 1
    assert first["exit_code"] == 1
    assert (tmp_path / "reports" / "report.json").is_file()
    assert (tmp_path / "reports" / "report.md").is_file()

    override = tmp_path / "override.db"
    code = main(["batch", "--config", str(config), "--db", str(override)])
    second = json.loads(capsys.readouterr().out)
    assert code == 1
    assert second["run_id"] != first["run_id"]
    with Ledger(override) as ledger:
        assert ledger.counts()["runs"] == 1
        assert ledger.counts()["files"] == 6


def test_report_command_reads_ledger(tmp_path, fixture_dir, capsys):
    database = tmp_path / "report.db"
    config = _config_file(tmp_path, fixture_dir, database=database)
    assert main(["batch", "--config", str(config)]) == 1
    batch_output = json.loads(capsys.readouterr().out)
    code = main(
        [
            "report",
            "--run-id",
            batch_output["run_id"],
            "--db",
            str(database),
            "--format",
            "json",
        ]
    )
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert report["summary"]["total_files"] == 6
    assert set(report["run_meta"]) == {
        "run_id",
        "config_fingerprint",
        "started_at",
        "finished_at",
        "tool_version",
    }


def test_missing_configuration_field_has_stable_code(tmp_path, fixture_dir, capsys):
    config = _config_file(tmp_path, fixture_dir, drop=("thresholds", "vfr_jitter_threshold"))
    code = main(["batch", "--config", str(config)])
    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert code == 2
    assert payload["error"]["code"] == "CONFIG_MISSING_FIELD"
    assert "vfr_jitter_threshold" in payload["error"]["message"]
