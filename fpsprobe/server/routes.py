"""Pure read-only route dispatch with strict identifier validation."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from ..report.json_report import build_json_report
from ..store.ledger import Ledger

RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
ALLOWED_VERDICT_FILTERS = {
    "ok",
    "review",
    "reject",
    "error",
    "CFR_OK",
    "CFR_MISMATCH",
    "VFR_SUSPECT",
    "TIMESTAMP_BROKEN",
}


def _error(status: int, code: str, message: str) -> tuple[int, dict]:
    return status, {"error": {"code": code, "message": message}}


def _validated_segments(path: str) -> list[str] | None:
    if not path.startswith("/") or "\x00" in path or "\\" in path:
        return None
    segments = path.split("/")[1:]
    if any(segment in {"", ".", ".."} for segment in segments):
        return None
    return segments


def dispatch(db_path: str | Path, raw_target: str) -> tuple[int, dict]:
    """Return (HTTP status, JSON object). No request data is used as a filesystem path."""
    try:
        target = urlsplit(raw_target)
    except ValueError:
        return _error(400, "BAD_REQUEST", "malformed request target")
    path = unquote(target.path)
    segments = _validated_segments(path)
    if segments is None:
        return _error(400, "BAD_PATH", "invalid or unsafe request path")
    if segments == ["health"]:
        return 200, {"status": "ok"}
    if not segments or segments[0] != "api":
        return _error(404, "NOT_FOUND", "route not found")

    with Ledger(db_path, read_only=True) as ledger:
        if segments == ["api", "runs"]:
            return 200, {"runs": ledger.list_runs()}
        if len(segments) == 3 and segments[1] == "runs":
            run_id = segments[2]
            if not RUN_ID_RE.match(run_id):
                return _error(400, "BAD_RUN_ID", "invalid run identifier")
            run = ledger.query_run(run_id)
            if run is None:
                return _error(404, "RUN_NOT_FOUND", "run not found")
            return 200, run
        if len(segments) == 4 and segments[1] == "runs" and segments[3] == "files":
            run_id = segments[2]
            if not RUN_ID_RE.match(run_id):
                return _error(400, "BAD_RUN_ID", "invalid run identifier")
            if ledger.query_run(run_id) is None:
                return _error(404, "RUN_NOT_FOUND", "run not found")
            query = parse_qs(target.query, keep_blank_values=True)
            verdict_values = query.get("verdict")
            verdict = verdict_values[0] if verdict_values else None
            if verdict is not None and verdict not in ALLOWED_VERDICT_FILTERS:
                return _error(400, "BAD_VERDICT_FILTER", "unsupported verdict filter")
            files = ledger.list_files(run_id, verdict)
            return 200, {"run_id": run_id, "files": files, "count": len(files)}
        if len(segments) == 3 and segments[1] == "report":
            filename = segments[2]
            if not filename.endswith(".json"):
                return _error(404, "NOT_FOUND", "report route requires a .json suffix")
            run_id = filename[:-5]
            if not RUN_ID_RE.match(run_id):
                return _error(400, "BAD_RUN_ID", "invalid run identifier")
            if ledger.query_run(run_id) is None:
                return _error(404, "RUN_NOT_FOUND", "run not found")
            return 200, build_json_report(ledger, run_id)
    return _error(404, "NOT_FOUND", "route not found")
