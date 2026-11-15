"""Human-readable Markdown report with overview and review/reject detail."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _num(value: Any, digits: int = 3) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _classification(entry: dict[str, Any]) -> str:
    return str(entry.get("classification", "error"))


def _file_row(index: int, entry: dict[str, Any]) -> str:
    container = entry.get("container") or {}
    timestamps = entry.get("timestamps") or {}
    declared = container.get("declared_fps")
    return (
        f"| {index} | `{entry.get('path', '')}` | {entry.get('verdict') or 'ERROR'} "
        f"| {_classification(entry)} | {_num(declared)} | {timestamps.get('frame_count', 0)} |"
    )


def _attribution_lines(attribution: dict[str, Any]) -> list[str]:
    lines = [
        f"- Timestamp disorder attribution: "
        f"{attribution.get('trigger_count', 0)} trigger(s) on primary track "
        f"{attribution.get('track_id')}"
    ]
    for trigger in attribution.get("triggers", []):
        offset = trigger.get("ctts_composition_offset")
        prev_offset = trigger.get("prev_ctts_composition_offset")
        ctts_text = (
            "n/a (no ctts table)"
            if offset is None
            else f"{offset} (prev {prev_offset})"
        )
        lines.append(
            f"  - sample {trigger.get('sample_index')}: "
            f"pts_delta={trigger.get('pts_delta_ticks')} ticks, "
            f"stts_sample_delta={trigger.get('stts_sample_delta')}, "
            f"ctts_offset={ctts_text} -> {trigger.get('primary_cause')}"
        )
    if attribution.get("truncated"):
        lines.append("  - ... (further triggers truncated in this report)")
    return lines


def _details(entry: dict[str, Any]) -> list[str]:
    container = entry.get("container") or {}
    timestamps = entry.get("timestamps") or {}
    stats = timestamps.get("delta_ticks") or {}
    lines = [
        f"### {entry.get('path', '')}",
        "",
        f"- Verdict: **{entry.get('verdict') or 'ERROR'}**",
        f"- Classification: **{_classification(entry)}**",
        f"- Reason codes: `{', '.join(entry.get('reason_codes') or []) or 'none'}`",
        f"- Reason: {entry.get('reason_text') or 'n/a'}",
        f"- Declared FPS: {_num(container.get('declared_fps'))}",
        f"- Measured FPS: {_num(timestamps.get('measured_fps'))}",
        f"- Frames / intervals: {timestamps.get('frame_count', 0)} / {stats.get('count', 0)}",
        f"- Delta min/median/p95/max (ticks): {_num(stats.get('min'), 1)} / "
        f"{_num(stats.get('median'), 1)} / {_num(stats.get('p95'), 1)} / "
        f"{_num(stats.get('max'), 1)}",
        f"- Jitter: {_num(stats.get('jitter'))}",
    ]
    attribution = timestamps.get("disorder_attribution")
    if attribution:
        lines.extend(_attribution_lines(attribution))
    lines.append("")
    return lines


def build_markdown_report(report: dict[str, Any]) -> str:
    meta = report.get("run_meta", {})
    summary = report.get("summary", {})
    files = report.get("files", [])
    verdicts = summary.get("verdicts", {})
    lines = [
        "# fpsprobe batch report",
        "",
        f"- Run: `{meta.get('run_id', '')}`",
        f"- Config fingerprint: `{meta.get('config_fingerprint', '')}`",
        f"- Started: {meta.get('started_at') or 'n/a'}",
        f"- Finished: {meta.get('finished_at') or 'n/a'}",
        f"- Tool version: {meta.get('tool_version', '')}",
        "",
        "## Overview",
        "",
        "| Metric | Count |",
        "| --- | ---: |",
        f"| Total files | {summary.get('total_files', 0)} |",
        f"| ok | {summary.get('ok', 0)} |",
        f"| review | {summary.get('review', 0)} |",
        f"| reject | {summary.get('reject', 0)} |",
        f"| error | {summary.get('error', 0)} |",
    ]
    for verdict in sorted(verdicts):
        lines.append(f"| {verdict} | {verdicts[verdict]} |")
    lines.extend(
        [
            "",
            "## Files",
            "",
            "| # | Path | Verdict | Class | Declared FPS | Frames |",
            "| ---: | --- | --- | --- | ---: | ---: |",
        ]
    )
    for index, entry in enumerate(files, start=1):
        lines.append(_file_row(index, entry))
    flagged = [
        entry for entry in files if _classification(entry) in {"review", "reject"}
    ]
    lines.extend(["", "## Review / reject detail", ""])
    if not flagged:
        lines.append("No files require review.")
    else:
        for entry in flagged:
            lines.extend(_details(entry))
    failures = report.get("failures", [])
    lines.extend(["## Failures", ""])
    if not failures:
        lines.append("No processing failures.")
    else:
        lines.extend(
            [
                "| Path | Error code | Message |",
                "| --- | --- | --- |",
            ]
        )
        for failure in failures:
            lines.append(
                f"| `{failure.get('path', '')}` | {failure.get('error_code', '')} "
                f"| {failure.get('message', '')} |"
            )
    lines.append("")
    return "\n".join(lines)


def write_markdown_report(report: dict[str, Any], path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(build_markdown_report(report), encoding="utf-8")
    return destination
