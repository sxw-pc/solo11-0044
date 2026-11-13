"""Stable, path-safe JSON report construction from the ledger."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..errors import RunNotFound
from ..store.ledger import Ledger

WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")


def _safe_path(value: Any) -> str:
    text = "" if value is None else str(value)
    if not text:
        return ""
    if WINDOWS_ABSOLUTE.match(text) or Path(text).is_absolute():
        return Path(text).name
    return text.replace("\\", "/")


def _normalise_summary(summary: dict, run: dict) -> dict[str, Any]:
    verdicts = summary.get("verdicts")
    if not isinstance(verdicts, dict):
        verdicts = {}
    return {
        "total_files": int(summary.get("total_files", run.get("total_files", 0)) or 0),
        "ok": int(summary.get("ok", 0) or 0),
        "review": int(summary.get("review", 0) or 0),
        "reject": int(summary.get("reject", 0) or 0),
        "error": int(summary.get("error", 0) or 0),
        "verdicts": {str(key): int(value) for key, value in sorted(verdicts.items())},
        "source_dir": _safe_path(summary.get("source_dir", run.get("source_dir", ""))),
    }


def build_json_report(ledger: Ledger, run_id: str) -> dict[str, Any]:
    run = ledger.query_run(run_id)
    if run is None:
        raise RunNotFound(f"run not found: {run_id}")
    files = []
    for item in run["files"]:
        copy = dict(item)
        copy["path"] = _safe_path(copy.get("path"))
        files.append(copy)
    failures = [
        {
            "path": _safe_path(failure.get("path")),
            "error_code": str(failure.get("error_code", "UNKNOWN_ERROR")),
            "message": str(failure.get("message", "")),
        }
        for failure in run["failures"]
    ]
    return {
        "run_meta": {
            "run_id": run["run_id"],
            "config_fingerprint": run["config_fingerprint"],
            "started_at": run["started_at"],
            "finished_at": run["finished_at"],
            "tool_version": run["tool_version"],
        },
        "summary": _normalise_summary(run["summary"], run),
        "files": files,
        "failures": failures,
    }


def write_json_report(report: dict[str, Any], path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return destination
