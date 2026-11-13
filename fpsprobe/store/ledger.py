"""Idempotent SQLite ledger operations."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import __version__
from ..errors import LedgerError, RunFinishedError, RunNotFound
from .schema import connect


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
    def create_run(
        self,
        config_fingerprint: str,
        source_dir: str = "",
        *,
        run_id: str | None = None,
        started_at: str | None = None,
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
        return identifier
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
