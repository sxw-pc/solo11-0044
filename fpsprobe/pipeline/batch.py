"""Directory scanning and the deterministic batch orchestration."""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

from ..config import Config
from ..errors import InputError
from ..models import BatchResult
from ..report.json_report import build_json_report, write_json_report
from ..report.markdown_report import write_markdown_report
from ..store.ledger import Ledger
from .runner import process_file

VERDICTS = ("CFR_OK", "CFR_MISMATCH", "VFR_SUSPECT", "TIMESTAMP_BROKEN")


def _is_ignored(relative: Path, full: Path, patterns: tuple[str, ...]) -> bool:
    rel_text = relative.as_posix()
    name = relative.name
    for pattern in patterns:
        if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(rel_text, pattern):
            return True
        if relative.match(pattern) or full.match(pattern):
            return True
    return False


def _display_for(path: Path, root: Path, config: Config) -> str:
    if len(config.input_dirs) == 1:
        try:
            return path.relative_to(root).as_posix()
        except ValueError:  # pragma: no cover - path came from root
            return path.name
    try:
        return path.relative_to(config.base_dir).as_posix()
    except ValueError:
        return f"{root.name}/{path.relative_to(root).as_posix()}"


def scan_inputs(config: Config) -> list[tuple[Path, str]]:
    """Return stable, path-sorted (absolute path, report path) pairs."""
    if not config.input_dirs:
        raise InputError("no input directories configured", code="NO_INPUT_DIRS")
    missing = [str(path) for path in config.input_dirs if not path.exists()]
    if missing:
        raise InputError(
            f"input directory not found: {missing[0]}", code="INPUT_NOT_FOUND"
        )
    suffixes = set(config.include_suffixes)
    found: dict[str, Path] = {}
    for root in config.input_dirs:
        if not root.is_dir():
            raise InputError(
                f"configured input is not a directory: {root.name}", code="INPUT_NOT_DIRECTORY"
            )
        iterator = root.rglob("*") if config.recursive else root.iterdir()
        for candidate in iterator:
            if not candidate.is_file() or candidate.suffix.lower() not in suffixes:
                continue
            relative = candidate.relative_to(root)
            if _is_ignored(relative, candidate, config.ignore_globs):
                continue
            display = _display_for(candidate, root, config)
            if display in found:
                raise InputError(
                    f"duplicate report path from multiple inputs: {display}",
                    code="DUPLICATE_INPUT_PATH",
                )
            found[display] = candidate.resolve()
    if not found:
        raise InputError("no matching video files found", code="NO_INPUT_FILES")
    return [(found[key], key) for key in sorted(found)]


def _empty_summary(source_dir: str) -> dict[str, Any]:
    return {
        "total_files": 0,
        "ok": 0,
        "review": 0,
        "reject": 0,
        "error": 0,
        "verdicts": {verdict: 0 for verdict in VERDICTS},
        "source_dir": source_dir,
    }


def run_batch(
    config: Config,
    *,
    ledger: Ledger | None = None,
    primary_track: str | int | None = None,
) -> BatchResult:
    """Process every matching file; one bad file is recorded, not fatal."""
    inputs = scan_inputs(config)
    own_ledger = ledger is None
    ledger = ledger or Ledger(config.db_path)
    source_dir = config.input_label()
    summary = _empty_summary(source_dir)
    try:
        run_id = ledger.create_run(
            config.fingerprint,
            source_dir=source_dir,
            config=config.raw,
        )
        for path, display in inputs:
            outcome = process_file(
                path,
                config,
                display_path=display,
                primary_track=primary_track,
            )
            entry = outcome.entry
            classification = str(entry.get("classification", "error"))
            verdict = entry.get("verdict") or "ERROR"
            summary["total_files"] += 1
            if classification in {"ok", "review", "reject", "error"}:
                summary[classification] += 1
            if verdict in summary["verdicts"]:
                summary["verdicts"][verdict] += 1
            file_id = ledger.upsert_file(
                run_id,
                display,
                str(entry.get("sha256", "")),
                int(entry.get("size_bytes", 0)),
                status=str(entry.get("status", "error")),
                error_code=entry.get("error_code"),
                error_message=entry.get("error_message"),
            )
            ledger.record_verdict(run_id, file_id, entry)
            if outcome.stats is not None and outcome.pts_ticks is not None:
                ledger.record_timeline(
                    run_id,
                    file_id,
                    len(outcome.pts_ticks),
                    config.max_detail_frames,
                    outcome.stats,
                    outcome.pts_ticks,
                    outcome.delta_ticks,
                )
        ledger.finish_run(run_id, summary)
        report = build_json_report(ledger, run_id)
        config.report_dir.mkdir(parents=True, exist_ok=True)
        json_path = write_json_report(
            report, config.report_dir / config.json_report_name
        )
        markdown_path = write_markdown_report(
            report, config.report_dir / config.markdown_report_name
        )
        exit_code = 1 if summary["reject"] > 0 else 0
        return BatchResult(
            run_id=run_id,
            summary=summary,
            report=report,
            json_path=json_path,
            markdown_path=markdown_path,
            exit_code=exit_code,
        )
    finally:
        if own_ledger:
            ledger.close()
