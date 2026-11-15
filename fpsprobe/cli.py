"""Command-line entry points for fpsprobe."""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .config import Config, load_config
from .errors import FpsProbeError
from .pipeline.batch import run_batch
from .pipeline.runner import process_file
from .report.json_report import build_json_report
from .report.markdown_report import build_markdown_report
from .server.app import serve
from .store.ledger import Ledger

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = PROJECT_ROOT / "fixtures" / "config" / "default.yaml"
DEFAULT_DB = Path("workdir/fpsprobe.db")


def _relative_display(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.name


def _config_path(value: str | None) -> Path:
    return Path(value) if value else DEFAULT_CONFIG


def _load(path: str | None) -> Config:
    return load_config(_config_path(path), base_dir=Path.cwd())


def _strategy(value: str) -> str | int:
    if value in {"first_video", "longest_video"}:
        return value
    if value.isdigit():
        return int(value)
    raise argparse.ArgumentTypeError(
        "expected first_video, longest_video, or a non-negative index"
    )


def _emit(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))


def _probe(args: argparse.Namespace) -> int:
    config = _load(args.config)
    path = Path(args.file)
    outcome = process_file(
        path,
        config,
        display_path=_relative_display(path),
        primary_track=args.primary_track,
    )
    _emit(outcome.entry)
    if outcome.entry["status"] == "error":
        return 2
    return 1 if outcome.entry["classification"] == "reject" else 0


def _batch(args: argparse.Namespace) -> int:
    config = _load(args.config)
    if args.db:
        config = dataclasses.replace(config, db_path=Path(args.db).resolve())
    result = run_batch(config, primary_track=args.primary_track)
    _emit(
        {
            "run_id": result.run_id,
            "summary": result.summary,
            "exit_code": result.exit_code,
            "json_report": _relative_display(result.json_path),
            "markdown_report": _relative_display(result.markdown_path),
        }
    )
    return result.exit_code


def _report(args: argparse.Namespace) -> int:
    database = Path(args.db) if args.db else DEFAULT_DB
    with Ledger(database) as ledger:
        report = build_json_report(ledger, args.run_id)
    if args.format == "md":
        print(build_markdown_report(report))
    else:
        _emit(report)
    return 0


def _serve(args: argparse.Namespace) -> int:
    config = _load(args.config) if args.config else None
    host = args.host or (config.server_host if config else "127.0.0.1")
    port = args.port or (config.server_port if config else 5180)
    database = Path(args.db) if args.db else (config.db_path if config else DEFAULT_DB)
    serve(host=host, port=port, db_path=database)
    return 0


def _fixtures_gen(args: argparse.Namespace) -> int:
    from fixtures.generator import generate_fixtures

    records = generate_fixtures(args.out_dir)
    _emit({"files": records})
    return 0


def _selftest(_args: argparse.Namespace) -> int:
    from fixtures.generator import generate_fixtures

    workdir = Path("workdir") / "selftest"
    fixture_dir = workdir / "fixtures"
    generate_fixtures(fixture_dir)
    config = _load(None)
    config = dataclasses.replace(
        config,
        input_dirs=(fixture_dir.resolve(),),
        report_dir=workdir.resolve(),
        db_path=(workdir / "selftest.db").resolve(),
        json_report_name="report.json",
        markdown_report_name="report.md",
    )
    result = run_batch(config)
    expected = {"total_files": 6, "ok": 3, "review": 2, "reject": 1, "error": 0}
    actual = {key: result.summary.get(key) for key in expected}
    passed = actual == expected and result.report["summary"]["total_files"] == 6
    _emit({"selftest": "ok" if passed else "failed", "run_id": result.run_id, "summary": result.summary})
    if not passed:
        raise FpsProbeError(
            "selftest summary differed from expected fixture outcomes",
            code="SELFTEST_FAILED",
            exit_code=3,
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fpsprobe", description=__doc__)
    parser.add_argument("--version", action="version", version=f"fpsprobe {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    probe = subparsers.add_parser("probe", help="analyse one video file")
    probe.add_argument("file")
    probe.add_argument("--config")
    probe.add_argument("--primary-track", type=_strategy)
    probe.set_defaults(func=_probe)

    batch = subparsers.add_parser("batch", help="scan, analyse, and persist a batch")
    batch.add_argument("--config")
    batch.add_argument("--db")
    batch.add_argument("--primary-track", type=_strategy)
    batch.set_defaults(func=_batch)

    report = subparsers.add_parser("report", help="render a stored run")
    report.add_argument("--run-id", required=True)
    report.add_argument("--format", choices=("json", "md"), default="json")
    report.add_argument("--db")
    report.set_defaults(func=_report)

    server = subparsers.add_parser("serve", help="serve the read-only local HTTP API")
    server.add_argument("--host")
    server.add_argument("--port", type=int)
    server.add_argument("--db")
    server.add_argument("--config")
    server.set_defaults(func=_serve)

    fixtures_parser = subparsers.add_parser("fixtures", help="fixture utilities")
    fixtures_sub = fixtures_parser.add_subparsers(dest="fixtures_command", required=True)
    gen = fixtures_sub.add_parser("gen", help="generate deterministic MP4 fixtures")
    gen.add_argument("--out-dir", default="fixtures/out")
    gen.set_defaults(func=_fixtures_gen)

    selftest = subparsers.add_parser("selftest", help="run an end-to-end smoke test")
    selftest.set_defaults(func=_selftest)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except FpsProbeError as exc:
        print(json.dumps(exc.to_dict(), sort_keys=True), file=sys.stderr)
        return exc.exit_code
    except Exception as exc:  # pragma: no cover - last-resort boundary
        payload = {
            "error": {
                "code": "INTERNAL_ERROR",
                "message": f"{type(exc).__name__}: {exc}",
            }
        }
        print(json.dumps(payload, sort_keys=True), file=sys.stderr)
        return 3
