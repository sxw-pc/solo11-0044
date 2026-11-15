"""Idempotent SQLite ledger operations and cross-run configuration diffing."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import __version__
from ..errors import (
    ConfigSnapshotMissing,
    LedgerError,
    RunFinishedError,
    RunNotFound,
    RunNotFinished,
)
from .schema import connect

# Severity ordering for ok/review/reject triage levels.
CLASS_LEVELS = {"ok": 0, "review": 1, "reject": 2}

# Top-level configuration sections that can actually move a verdict or its
# triage level. Reporting/database/server plumbing is deliberately excluded so
# environment path changes never masquerade as classification drivers.
DECISION_SECTIONS = frozenset({"thresholds", "classification", "primary_track", "adapter"})

# Decision-relevant configuration leaves for each consistency verdict. A leaf is
# listed here only if changing it can actually move that verdict's classification.
VERDICT_CONFIG_KEYS: dict[str, frozenset[str]] = {
    "CFR_OK": frozenset(
        {
            "thresholds.fps_abs_tolerance",
            "thresholds.fps_rel_tolerance",
            "classification.cfr_ok",
        }
    ),
    "CFR_MISMATCH": frozenset(
        {
            "thresholds.fps_abs_tolerance",
            "thresholds.fps_rel_tolerance",
            "thresholds.mismatch_review_abs_fps",
            "classification.cfr_mismatch",
        }
    ),
    "VFR_SUSPECT": frozenset(
        {
            "thresholds.vfr_jitter_threshold",
            "thresholds.vfr_p95_p50_threshold",
            "thresholds.cfr_delta_rel_tolerance",
            "classification.vfr_suspect",
        }
    ),
    "TIMESTAMP_BROKEN": frozenset(
        {
            "classification.timestamp_broken",
        }
    ),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dump(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def flatten_config(value: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten a nested mapping into dotted leaves; lists are terminal values."""
    flattened: dict[str, Any] = {}
    if isinstance(value, dict):
        for key in sorted(value):
            label = f"{prefix}.{key}" if prefix else str(key)
            flattened.update(flatten_config(value[key], label))
        return flattened
    flattened[prefix] = value
    return flattened


def diff_configs(config_a: Any, config_b: Any) -> dict[str, dict[str, Any]]:
    """Return every added/removed/changed dotted leaf between two raw configs."""
    leaves_a = flatten_config(config_a)
    leaves_b = flatten_config(config_b)
    changes: dict[str, dict[str, Any]] = {}
    for key in sorted(set(leaves_a) | set(leaves_b)):
        if key not in leaves_a:
            changes[key] = {"run_a": None, "run_b": leaves_b[key], "kind": "added"}
        elif key not in leaves_b:
            changes[key] = {"run_a": leaves_a[key], "run_b": None, "kind": "removed"}
        elif leaves_a[key] != leaves_b[key]:
            changes[key] = {
                "run_a": leaves_a[key],
                "run_b": leaves_b[key],
                "kind": "changed",
            }
    return changes


def _direction(classification_a: str | None, classification_b: str | None) -> str:
    """Encode the ok/review/reject movement, including one-sided appearances."""
    if classification_a is None and classification_b is not None:
        return f"added:{classification_b}"
    if classification_b is None and classification_a is not None:
        return f"removed:{classification_a}"
    if classification_a == classification_b:
        return "unchanged"
    level_a = CLASS_LEVELS.get(str(classification_a), -1)
    level_b = CLASS_LEVELS.get(str(classification_b), -1)
    if level_b > level_a:
        return f"escalated:{classification_a}->{classification_b}"
    if level_b < level_a:
        return f"de-escalated:{classification_a}->{classification_b}"
    return f"changed:{classification_a}->{classification_b}"


def _trigger_keys(verdict_a: str | None, verdict_b: str | None) -> frozenset[str]:
    keys: set[str] = set()
    for verdict in (verdict_a, verdict_b):
        if verdict in VERDICT_CONFIG_KEYS:
            keys.update(VERDICT_CONFIG_KEYS[verdict])
    return frozenset(keys)


class Ledger:
    def __init__(
        self, path: str | Path = "workdir/fpsprobe.db", *, read_only: bool = False
    ) -> None:
        self.path = str(path)
        self.conn = connect(self.path, read_only=read_only)
    def close(self) -> None:
        self.conn.close()
    def __enter__(self) -> "Ledger":
        return self
    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
    def _run_row(self, run_id: str):
        row = self.conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise RunNotFound(f"run not found: {run_id}")
        return row
    def _ensure_mutable(self, run_id: str) -> None:
        row = self._run_row(run_id)
        if row["finished_at"] is not None or row["status"] == "finished":
            raise RunFinishedError(f"run is finished and immutable: {run_id}")
    def record_config_snapshot(
        self, config_fingerprint: str, raw_config: dict[str, Any]
    ) -> None:
        """Persist the full configuration behind a fingerprint once; idempotent."""
        if not config_fingerprint:
            raise LedgerError("config fingerprint is required")
        if not isinstance(raw_config, dict):
            raise LedgerError("config snapshot must be a mapping")
        self.conn.execute(
            "INSERT INTO config_snapshots(config_fingerprint, config_json, recorded_at) "
            "VALUES (?, ?, ?) ON CONFLICT(config_fingerprint) DO NOTHING",
            (config_fingerprint, _dump(raw_config), _now()),
        )
        self.conn.commit()

    def create_run(
        self,
        config_fingerprint: str,
        source_dir: str = "",
        *,
        run_id: str | None = None,
        started_at: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> str:
        if not config_fingerprint:
            raise LedgerError("config fingerprint is required")
        identifier = run_id or uuid.uuid4().hex[:12]
        try:
            self.conn.execute(
                "INSERT INTO runs(run_id, config_fingerprint, started_at, tool_version, "
                "source_dir, status) VALUES (?, ?, ?, ?, ?, 'running')",
                (
                    identifier,
                    config_fingerprint,
                    started_at or _now(),
                    __version__,
                    source_dir,
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            raise LedgerError(f"run already exists: {identifier}", code="RUN_EXISTS") from exc
        if config is not None:
            self.record_config_snapshot(config_fingerprint, config)
        return identifier

    def get_config_snapshot(self, config_fingerprint: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT config_json FROM config_snapshots WHERE config_fingerprint = ?",
            (config_fingerprint,),
        ).fetchone()
        if row is None:
            return None
        return _loads(row["config_json"], None)
    def upsert_file(
        self,
        run_id: str,
        path: str,
        sha256: str,
        size_bytes: int,
        *,
        status: str = "ok",
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> int:
        """Insert a file once per (path, sha256, config fingerprint), then update it."""
        self._ensure_mutable(run_id)
        run = self._run_row(run_id)
        now = _now()
        self.conn.execute(
            "INSERT INTO files(path, sha256, size_bytes, config_fingerprint, status, "
            "error_code, error_message, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(path, sha256, config_fingerprint) DO UPDATE SET "
            "status=excluded.status, error_code=excluded.error_code, "
            "error_message=excluded.error_message, updated_at=excluded.updated_at",
            (
                path,
                sha256,
                int(size_bytes),
                run["config_fingerprint"],
                status,
                error_code,
                error_message,
                now,
                now,
            ),
        )
        row = self.conn.execute(
            "SELECT id FROM files WHERE path=? AND sha256=? AND config_fingerprint=?",
            (path, sha256, run["config_fingerprint"]),
        ).fetchone()
        self.conn.commit()
        if row is None:  # pragma: no cover - defensive
            raise LedgerError("file row was not persisted")
        return int(row["id"])
    def record_verdict(self, run_id: str, file_id: int, entry: dict[str, Any]) -> None:
        self._ensure_mutable(run_id)
        file_row = self.conn.execute(
            "SELECT id FROM files WHERE id = ?", (file_id,)
        ).fetchone()
        if file_row is None:
            raise LedgerError(f"file row not found: {file_id}")
        stats = (entry.get("timestamps") or {}).get("delta_ticks")
        self.conn.execute(
            "INSERT INTO file_verdicts(run_id, file_id, verdict, classification, "
            "reason_codes, reason_text, stats_json, payload_json, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(run_id, file_id) DO UPDATE SET "
            "verdict=excluded.verdict, classification=excluded.classification, "
            "reason_codes=excluded.reason_codes, reason_text=excluded.reason_text, "
            "stats_json=excluded.stats_json, payload_json=excluded.payload_json, "
            "recorded_at=excluded.recorded_at",
            (
                run_id,
                file_id,
                entry.get("verdict"),
                entry.get("classification", "error"),
                _dump(entry.get("reason_codes", [])),
                entry.get("reason_text", ""),
                _dump(stats) if stats is not None else None,
                _dump(entry),
                _now(),
            ),
        )
        self.conn.commit()
    def record_timeline(
        self,
        run_id: str,
        file_id: int,
        frame_count: int,
        max_detail_frames: int,
        stats: dict[str, Any],
        pts_ticks: list[int] | None,
        deltas_ticks: list[int] | None,
    ) -> None:
        """Store raw points only for short files; large files keep a summary only."""
        self._ensure_mutable(run_id)
        truncated = int(frame_count) > int(max_detail_frames)
        stored_count = 0 if truncated else len(pts_ticks or [])
        self.conn.execute(
            "INSERT INTO timeline_samples(run_id, file_id, frame_count, "
            "stored_frame_count, truncated, summary_json, pts_ticks_json, "
            "deltas_ticks_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(run_id, file_id) DO UPDATE SET "
            "frame_count=excluded.frame_count, stored_frame_count=excluded.stored_frame_count, "
            "truncated=excluded.truncated, summary_json=excluded.summary_json, "
            "pts_ticks_json=excluded.pts_ticks_json, "
            "deltas_ticks_json=excluded.deltas_ticks_json",
            (
                run_id,
                file_id,
                int(frame_count),
                stored_count,
                1 if truncated else 0,
                _dump(stats),
                None if truncated else _dump(pts_ticks or []),
                None if truncated else _dump(deltas_ticks or []),
            ),
        )
        self.conn.commit()
    def finish_run(self, run_id: str, summary: dict[str, Any]) -> bool:
        """Finalise a run once. A second call is a no-op, preserving the first result."""
        row = self._run_row(run_id)
        if row["finished_at"] is not None:
            return False
        total = int(summary.get("total_files", 0))
        self.conn.execute(
            "UPDATE runs SET finished_at=?, status='finished', total_files=?, "
            "summary_json=? WHERE run_id=?",
            (_now(), total, _dump(summary), run_id),
        )
        self.conn.commit()
        return True
    def list_runs(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM runs ORDER BY started_at DESC, run_id ASC"
        ).fetchall()
        return [
            {
                "run_id": row["run_id"],
                "config_fingerprint": row["config_fingerprint"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                "tool_version": row["tool_version"],
                "source_dir": row["source_dir"],
                "status": row["status"],
                "total_files": row["total_files"],
                "summary": _loads(row["summary_json"], {}),
            }
            for row in rows
        ]
    def _fallback_entry(self, row) -> dict[str, Any]:
        return {
            "path": row["path"],
            "sha256": row["sha256"],
            "size_bytes": row["size_bytes"],
            "status": row["status"],
            "error_code": row["error_code"],
            "error_message": row["error_message"],
            "container": None,
            "timestamps": None,
            "verdict": row["verdict"],
            "reason_codes": _loads(row["reason_codes"], []),
            "reason_text": row["reason_text"],
            "classification": row["classification"],
        }
    def list_files(self, run_id: str, verdict: str | None = None) -> list[dict[str, Any]]:
        query = (
            "SELECT f.path, f.sha256, f.size_bytes, f.status, f.error_code, "
            "f.error_message, v.verdict, v.classification, v.reason_codes, "
            "v.reason_text, v.payload_json FROM files f "
            "JOIN file_verdicts v ON v.file_id = f.id WHERE v.run_id = ?"
        )
        parameters: list[Any] = [run_id]
        if verdict is not None:
            query += " AND (v.verdict = ? OR v.classification = ?)"
            parameters.extend([verdict, verdict])
        query += " ORDER BY f.path ASC"
        result: list[dict[str, Any]] = []
        for row in self.conn.execute(query, parameters).fetchall():
            payload = _loads(row["payload_json"], None)
            result.append(payload if isinstance(payload, dict) else self._fallback_entry(row))
        return result
    def query_run(self, run_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            return None
        files = self.list_files(run_id)
        summary = _loads(row["summary_json"], {})
        if not summary:
            summary = {
                "total_files": len(files),
                "ok": sum(1 for item in files if item.get("classification") == "ok"),
                "review": sum(1 for item in files if item.get("classification") == "review"),
                "reject": sum(1 for item in files if item.get("classification") == "reject"),
                "error": sum(1 for item in files if item.get("classification") == "error"),
                "verdicts": {},
                "source_dir": row["source_dir"],
            }
        failures = [
            {
                "path": item.get("path"),
                "error_code": item.get("error_code") or "UNKNOWN_ERROR",
                "message": item.get("error_message") or "unknown failure",
            }
            for item in files
            if item.get("status") == "error"
        ]
        return {
            "run_id": row["run_id"],
            "config_fingerprint": row["config_fingerprint"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "tool_version": row["tool_version"],
            "source_dir": row["source_dir"],
            "status": row["status"],
            "total_files": row["total_files"],
            "summary": summary,
            "files": files,
            "failures": failures,
        }
    def counts(self) -> dict[str, int]:
        return {
            table: int(
                self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            )
            for table in ("runs", "files", "file_verdicts", "timeline_samples")
        }

    def _require_finished_run(self, run_id: str):
        row = self._run_row(run_id)
        if row["status"] != "finished" or row["finished_at"] is None:
            raise RunNotFinished(
                f"run is still {row['status']} and cannot be compared yet: {run_id}"
            )
        return row

    def _run_file_verdicts(self, run_id: str) -> dict[tuple[str, str], dict[str, Any]]:
        """Map (path, sha256) identity to the run's verdict, decoupled from fingerprint."""
        rows = self.conn.execute(
            "SELECT f.path AS path, f.sha256 AS sha256, "
            "v.verdict AS verdict, v.classification AS classification "
            "FROM file_verdicts v "
            "JOIN files f ON f.id = v.file_id "
            "WHERE v.run_id = ? ORDER BY f.path ASC",
            (run_id,),
        ).fetchall()
        return {
            (row["path"], row["sha256"]): {
                "verdict": row["verdict"],
                "classification": row["classification"],
            }
            for row in rows
        }

    def diff_runs(self, run_a: str, run_b: str) -> dict[str, Any]:
        """Compare two finished runs, merging file identities across config fingerprints.

        Files are keyed by (path, sha256) so the same material stored under several
        config fingerprints is aligned. Per-file verdict/classification movement and
        the configuration leaves that could have triggered it are reported, rather
        than opaque fingerprint strings.
        """
        row_a = self._require_finished_run(run_a)
        row_b = self._require_finished_run(run_b)
        files_a = self._run_file_verdicts(run_a)
        files_b = self._run_file_verdicts(run_b)

        snapshot_a = self.get_config_snapshot(row_a["config_fingerprint"])
        snapshot_b = self.get_config_snapshot(row_b["config_fingerprint"])
        if snapshot_a is None or snapshot_b is None:
            missing = run_a if snapshot_a is None else run_b
            raise ConfigSnapshotMissing(
                "configuration snapshot missing for run "
                f"{missing}; rerun with a current build to enable diffing"
            )
        decision_a = {
            key: value
            for key, value in snapshot_a.items()
            if key in DECISION_SECTIONS
        }
        decision_b = {
            key: value
            for key, value in snapshot_b.items()
            if key in DECISION_SECTIONS
        }
        config_changes = diff_configs(decision_a, decision_b)

        entries: list[dict[str, Any]] = []
        counts = {
            "common": 0,
            "only_in_a": 0,
            "only_in_b": 0,
            "flipped": 0,
            "verdict_flipped": 0,
            "classification_flipped": 0,
            "attributed": 0,
        }
        for identity in sorted(set(files_a) | set(files_b), key=lambda item: item[0]):
            path, sha256 = identity
            side_a = files_a.get(identity)
            side_b = files_b.get(identity)
            verdict_a = side_a["verdict"] if side_a else None
            verdict_b = side_b["verdict"] if side_b else None
            class_a = side_a["classification"] if side_a else None
            class_b = side_b["classification"] if side_b else None
            if side_a and side_b:
                presence = "common"
            elif side_a:
                presence = "only_in_a"
            else:
                presence = "only_in_b"
            counts[presence] += 1

            verdict_flipped = verdict_a != verdict_b
            classification_flipped = class_a != class_b
            flipped = verdict_flipped or classification_flipped
            if flipped:
                counts["flipped"] += 1
            if verdict_flipped:
                counts["verdict_flipped"] += 1
            if classification_flipped:
                counts["classification_flipped"] += 1

            relevant = _trigger_keys(verdict_a, verdict_b)
            trigger_fields = {
                key: change
                for key, change in config_changes.items()
                if key in relevant
            }
            attributed = bool(trigger_fields)
            if attributed:
                counts["attributed"] += 1

            entries.append(
                {
                    "path": path,
                    "sha256": sha256,
                    "presence": presence,
                    "verdict_a": verdict_a,
                    "verdict_b": verdict_b,
                    "classification_a": class_a,
                    "classification_b": class_b,
                    "verdict_flipped": verdict_flipped,
                    "classification_flipped": classification_flipped,
                    "flipped": flipped,
                    "direction": _direction(class_a, class_b),
                    "trigger_fields": trigger_fields,
                    "attributed": attributed,
                }
            )

        return {
            "run_a": {
                "run_id": run_a,
                "config_fingerprint": row_a["config_fingerprint"],
                "status": row_a["status"],
                "config_recorded": True,
            },
            "run_b": {
                "run_id": run_b,
                "config_fingerprint": row_b["config_fingerprint"],
                "status": row_b["status"],
                "config_recorded": True,
            },
            "config_diff": config_changes,
            "summary": {
                **counts,
                "total_identities": len(entries),
                "config_fields_changed": len(config_changes),
            },
            "files": entries,
        }
