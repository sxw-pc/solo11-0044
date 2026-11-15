"""Cross-run configuration diff: identity merging, flip attribution, and CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fpsprobe.cli import main
from fpsprobe.errors import ConfigSnapshotMissing, RunNotFinished, RunNotFound
from fpsprobe.pipeline.batch import run_batch
from fpsprobe.store.ledger import Ledger, diff_configs, flatten_config

ROOT = Path(__file__).resolve().parents[1]


def _config(tmp_path: Path, fixture_dir: Path, name: str, overrides=(), db_name="shared.db"):
    raw = json.loads((ROOT / "fixtures" / "config" / "default.json").read_text())
    raw["input_dirs"] = [str(fixture_dir)]
    raw["report"]["output_dir"] = str(tmp_path / f"reports_{name}")
    raw["database"]["path"] = str(tmp_path / db_name)
    for section, key, value in overrides:
        raw[section][key] = value
    path = tmp_path / f"config_{name}.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    from fpsprobe.config import load_config

    return load_config(path, base_dir=tmp_path)


def _by_path(diff: dict) -> dict[str, dict]:
    return {entry["path"]: entry for entry in diff["files"]}


def test_flatten_and_diff_configs_isolate_dotted_leaves():
    flat = flatten_config(
        {
            "thresholds": {"mismatch_review_abs_fps": 0.02},
            "classification": {"vfr_suspect": "review"},
        }
    )
    assert flat["thresholds.mismatch_review_abs_fps"] == 0.02
    assert flat["classification.vfr_suspect"] == "review"

    changes = diff_configs(
        {"thresholds": {"a": 1, "b": 2}, "kept": [1]},
        {"thresholds": {"a": 9, "b": 2}, "added": "x", "kept": [1]},
    )
    assert changes == {
        "added": {"run_a": None, "run_b": "x", "kind": "added"},
        "thresholds.a": {"run_a": 1, "run_b": 9, "kind": "changed"},
    }


def test_end_to_end_flip_attribution_across_config_fingerprints(tmp_path, fixture_dir):
    config_a = _config(tmp_path, fixture_dir, "a")
    config_b = _config(
        tmp_path,
        fixture_dir,
        "b",
        overrides=[
            ("thresholds", "mismatch_review_abs_fps", 999.0),
            ("classification", "vfr_suspect", "ok"),
        ],
    )
    run_a = run_batch(config_a).run_id
    run_b = run_batch(config_b).run_id
    assert config_a.fingerprint != config_b.fingerprint

    with Ledger(config_a.db_path) as ledger:
        # Both runs share one physical database; file identities span fingerprints.
        diff = ledger.diff_runs(run_a, run_b)
        assert len(diff["files"]) == 5
        assert diff["summary"] == {
            "common": 5,
            "only_in_a": 0,
            "only_in_b": 0,
            "flipped": 2,
            "verdict_flipped": 0,
            "classification_flipped": 2,
            "attributed": 2,
            "total_identities": 5,
            "config_fields_changed": 2,
        }

    files = _by_path(diff)

    mismatch = files["cfr_2997.mp4"]
    assert mismatch["presence"] == "common"
    assert mismatch["verdict_a"] == mismatch["verdict_b"] == "CFR_MISMATCH"
    assert (mismatch["classification_a"], mismatch["classification_b"]) == ("review", "ok")
    assert mismatch["verdict_flipped"] is False
    assert mismatch["classification_flipped"] is True
    assert mismatch["flipped"] is True
    assert mismatch["direction"] == "de-escalated:review->ok"
    assert set(mismatch["trigger_fields"]) == {"thresholds.mismatch_review_abs_fps"}
    field = mismatch["trigger_fields"]["thresholds.mismatch_review_abs_fps"]
    assert field == {"run_a": 0.02, "run_b": 999.0, "kind": "changed"}
    assert mismatch["attributed"] is True

    vfr = files["vfr_jitter.mp4"]
    assert vfr["verdict_a"] == vfr["verdict_b"] == "VFR_SUSPECT"
    assert (vfr["classification_a"], vfr["classification_b"]) == ("review", "ok")
    assert vfr["direction"] == "de-escalated:review->ok"
    assert set(vfr["trigger_fields"]) == {"classification.vfr_suspect"}
    assert vfr["trigger_fields"]["classification.vfr_suspect"] == {
        "run_a": "review",
        "run_b": "ok",
        "kind": "changed",
    }

    # Unrelated changed leaves must not be attributed to a file whose verdict
    # never consults them; unchanged files report no flip.
    stable = files["cfr_30.mp4"]
    assert stable["flipped"] is False
    assert stable["direction"] == "unchanged"
    assert stable["trigger_fields"] == {}
    assert stable["attributed"] is False

    broken = files["broken_order.mp4"]
    assert broken["classification_a"] == broken["classification_b"] == "reject"
    assert broken["trigger_fields"] == {}

    # The opaque fingerprint is accompanied by concrete key-level config diffs.
    assert set(diff["config_diff"]) == {
        "thresholds.mismatch_review_abs_fps",
        "classification.vfr_suspect",
    }


def test_files_present_on_one_side_are_reported_with_null_partner(tmp_path, entry_factory):
    db = tmp_path / "onesided.db"
    config_a = {"thresholds": {"mismatch_review_abs_fps": 0.02}, "classification": {"x": "review"}}
    config_b = {"thresholds": {"mismatch_review_abs_fps": 9.0}, "classification": {"x": "review"}}
    with Ledger(db) as ledger:
        run_a = ledger.create_run("fp-a", run_id="run-a", config=config_a)
        shared_id = ledger.upsert_file(run_a, "shared.mp4", "a" * 64, 10)
        ledger.record_verdict(run_a, shared_id, entry_factory("shared.mp4"))
        only_a_id = ledger.upsert_file(run_a, "only_a.mp4", "b" * 64, 10)
        ledger.record_verdict(
            run_a,
            only_a_id,
            entry_factory("only_a.mp4", verdict="CFR_MISMATCH", classification="review"),
        )
        ledger.finish_run(run_a, {"total_files": 2})

        run_b = ledger.create_run("fp-b", run_id="run-b", config=config_b)
        shared_b_id = ledger.upsert_file(run_b, "shared.mp4", "a" * 64, 10)
        ledger.record_verdict(
            run_b,
            shared_b_id,
            entry_factory("shared.mp4", verdict="CFR_MISMATCH", classification="review"),
        )
        only_b_id = ledger.upsert_file(run_b, "only_b.mp4", "c" * 64, 10)
        ledger.record_verdict(run_b, only_b_id, entry_factory("only_b.mp4"))
        ledger.finish_run(run_b, {"total_files": 2})

        diff = ledger.diff_runs("run-a", "run-b")

    files = _by_path(diff)
    assert set(files) == {"shared.mp4", "only_a.mp4", "only_b.mp4"}
    only_a = files["only_a.mp4"]
    assert only_a["presence"] == "only_in_a"
    assert only_a["verdict_b"] is None
    assert only_a["classification_b"] is None
    assert only_a["classification_a"] == "review"
    assert only_a["direction"] == "removed:review"
    assert only_a["flipped"] is True
    only_b = files["only_b.mp4"]
    assert only_b["presence"] == "only_in_b"
    assert only_b["verdict_a"] is None
    assert only_b["direction"] == "added:ok"
    assert only_b["flipped"] is True

    shared = files["shared.mp4"]
    assert shared["presence"] == "common"
    assert shared["flipped"] is True  # CFR_OK -> CFR_MISMATCH verdict change
    assert shared["verdict_flipped"] is True
    assert shared["classification_flipped"] is True
    assert shared["direction"] == "escalated:ok->review"
    assert set(shared["trigger_fields"]) == {"thresholds.mismatch_review_abs_fps"}

    assert diff["summary"]["common"] == 1
    assert diff["summary"]["only_in_a"] == 1
    assert diff["summary"]["only_in_b"] == 1
    assert diff["summary"]["flipped"] == 3


def test_running_run_is_rejected_with_stable_code(tmp_path, entry_factory):
    db = tmp_path / "running.db"
    with Ledger(db) as ledger:
        finished = ledger.create_run("fp-a", run_id="run-done", config={"k": 1})
        file_id = ledger.upsert_file(finished, "a.mp4", "a" * 64, 1)
        ledger.record_verdict(finished, file_id, entry_factory("a.mp4"))
        ledger.finish_run(finished, {"total_files": 1})

        running = ledger.create_run("fp-b", run_id="run-open", config={"k": 2})
        file_id = ledger.upsert_file(running, "a.mp4", "a" * 64, 1)
        ledger.record_verdict(running, file_id, entry_factory("a.mp4"))
        # deliberately not finished

        with pytest.raises(RunNotFinished) as caught:
            ledger.diff_runs("run-done", "run-open")
        assert caught.value.code == "RUN_NOT_FINISHED"
        with pytest.raises(RunNotFinished):
            ledger.diff_runs("run-open", "run-done")


def test_missing_run_raises_run_not_found(tmp_path):
    with Ledger(tmp_path / "missing.db") as ledger:
        with pytest.raises(RunNotFound):
            ledger.diff_runs("nope-a", "nope-b")


def test_diff_without_config_snapshot_is_rejected(tmp_path, entry_factory):
    db = tmp_path / "nosnapshot.db"
    with Ledger(db) as ledger:
        # create_run without a config leaves no snapshot row
        run_a = ledger.create_run("fp-a", run_id="run-a")
        file_id = ledger.upsert_file(run_a, "a.mp4", "a" * 64, 1)
        ledger.record_verdict(run_a, file_id, entry_factory("a.mp4"))
        ledger.finish_run(run_a, {"total_files": 1})
        run_b = ledger.create_run("fp-b", run_id="run-b")
        file_id = ledger.upsert_file(run_b, "a.mp4", "a" * 64, 1)
        ledger.record_verdict(run_b, file_id, entry_factory("a.mp4"))
        ledger.finish_run(run_b, {"total_files": 1})

        with pytest.raises(ConfigSnapshotMissing) as caught:
            ledger.diff_runs("run-a", "run-b")
        assert caught.value.code == "CONFIG_SNAPSHOT_MISSING"


def test_cli_diff_command_and_changed_only(tmp_path, fixture_dir, capsys):
    config_a = _config(tmp_path, fixture_dir, "cli_a")
    config_b = _config(
        tmp_path,
        fixture_dir,
        "cli_b",
        overrides=[
            ("thresholds", "mismatch_review_abs_fps", 999.0),
            ("classification", "vfr_suspect", "ok"),
        ],
    )
    run_a = run_batch(config_a).run_id
    run_b = run_batch(config_b).run_id

    code = main(
        [
            "diff",
            "--run-a",
            run_a,
            "--run-b",
            run_b,
            "--db",
            str(config_a.db_path),
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["run_a"]["run_id"] == run_a
    assert payload["run_b"]["run_id"] == run_b
    assert len(payload["files"]) == 5
    assert payload["summary"]["flipped"] == 2

    code = main(
        [
            "diff",
            "--run-a",
            run_a,
            "--run-b",
            run_b,
            "--db",
            str(config_a.db_path),
            "--changed-only",
        ]
    )
    assert code == 0
    filtered = json.loads(capsys.readouterr().out)
    assert len(filtered["files"]) == 2
    assert filtered["summary"]["files_reported"] == 2
    assert all(entry["flipped"] for entry in filtered["files"])


def test_cli_diff_rejects_running_run(tmp_path, capsys, entry_factory):
    db = tmp_path / "cli-running.db"
    with Ledger(db) as ledger:
        done = ledger.create_run("fp-a", run_id="run-done", config={"k": 1})
        file_id = ledger.upsert_file(done, "a.mp4", "a" * 64, 1)
        ledger.record_verdict(done, file_id, entry_factory("a.mp4"))
        ledger.finish_run(done, {"total_files": 1})
        open_run = ledger.create_run("fp-b", run_id="run-open", config={"k": 2})
        file_id = ledger.upsert_file(open_run, "a.mp4", "a" * 64, 1)
        ledger.record_verdict(open_run, file_id, entry_factory("a.mp4"))

    code = main(
        [
            "diff",
            "--run-a",
            "run-done",
            "--run-b",
            "run-open",
            "--db",
            str(db),
        ]
    )
    assert code == 2
    error = json.loads(capsys.readouterr().err)
    assert error["error"]["code"] == "RUN_NOT_FINISHED"


def test_legacy_v1_database_migrates_and_supports_snapshots(tmp_path):
    import sqlite3

    from fpsprobe.store.schema import SCHEMA_VERSION

    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE runs (run_id TEXT PRIMARY KEY, config_fingerprint TEXT NOT NULL,
         started_at TEXT NOT NULL, finished_at TEXT, tool_version TEXT NOT NULL,
         source_dir TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'running',
         total_files INTEGER NOT NULL DEFAULT 0, summary_json TEXT);
        PRAGMA user_version = 1;
        """
    )
    conn.commit()
    conn.close()

    with Ledger(db) as ledger:
        assert int(ledger.conn.execute("PRAGMA user_version").fetchone()[0]) == SCHEMA_VERSION
        ledger.record_config_snapshot("fp", {"thresholds": {"x": 1}})
        assert ledger.get_config_snapshot("fp") == {"thresholds": {"x": 1}}


def test_rerun_same_config_reuses_snapshot_and_diff_is_stable(tmp_path, fixture_dir):
    config = _config(tmp_path, fixture_dir, "same")
    first = run_batch(config)
    second = run_batch(config)
    with Ledger(config.db_path) as ledger:
        diff = ledger.diff_runs(first.run_id, second.run_id)
    assert diff["config_diff"] == {}
    assert diff["summary"]["flipped"] == 0
    assert all(entry["direction"] == "unchanged" for entry in diff["files"])
    assert all(entry["presence"] == "common" for entry in diff["files"])
