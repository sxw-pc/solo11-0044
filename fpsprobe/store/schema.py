"""SQLite schema for runs, files, verdicts, and bounded timeline samples."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
    run_id             TEXT PRIMARY KEY,
    config_fingerprint TEXT NOT NULL,
    started_at         TEXT NOT NULL,
    finished_at        TEXT,
    tool_version       TEXT NOT NULL,
    source_dir         TEXT NOT NULL DEFAULT '',
    status             TEXT NOT NULL DEFAULT 'running',
    total_files        INTEGER NOT NULL DEFAULT 0,
    summary_json       TEXT
);

CREATE TABLE IF NOT EXISTS files (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    path               TEXT NOT NULL,
    sha256             TEXT NOT NULL,
    size_bytes         INTEGER NOT NULL,
    config_fingerprint TEXT NOT NULL,
    status             TEXT NOT NULL,
    error_code         TEXT,
    error_message      TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    UNIQUE(path, sha256, config_fingerprint)
);

CREATE TABLE IF NOT EXISTS file_verdicts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         TEXT NOT NULL,
    file_id        INTEGER NOT NULL,
    verdict        TEXT,
    classification TEXT NOT NULL,
    reason_codes   TEXT NOT NULL,
    reason_text    TEXT NOT NULL,
    stats_json     TEXT,
    payload_json   TEXT NOT NULL,
    recorded_at    TEXT NOT NULL,
    UNIQUE(run_id, file_id),
    FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS timeline_samples (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id             TEXT NOT NULL,
    file_id            INTEGER NOT NULL,
    frame_count        INTEGER NOT NULL,
    stored_frame_count INTEGER NOT NULL,
    truncated          INTEGER NOT NULL,
    summary_json       TEXT NOT NULL,
    pts_ticks_json     TEXT,
    deltas_ticks_json  TEXT,
    UNIQUE(run_id, file_id),
    FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_files_fingerprint
    ON files(config_fingerprint, path);
CREATE INDEX IF NOT EXISTS idx_verdicts_run_class
    ON file_verdicts(run_id, classification);
CREATE INDEX IF NOT EXISTS idx_verdicts_run_verdict
    ON file_verdicts(run_id, verdict);
PRAGMA user_version = 1;
"""


def connect(path: str | Path, *, read_only: bool = False) -> sqlite3.Connection:
    """Open a ledger connection; read-only callers never initialise or write."""
    if read_only:
        connection = sqlite3.connect(
            Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=15.0
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        return connection
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=15.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    initialize(connection)
    return connection


def initialize(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA_SQL)
    connection.commit()
