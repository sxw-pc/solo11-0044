from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fixtures.generator import generate_fixtures  # noqa: E402
from fpsprobe.config import load_config  # noqa: E402
from fpsprobe.store.ledger import Ledger  # noqa: E402


@pytest.fixture(scope="session")
def fixture_dir(tmp_path_factory) -> Path:
    destination = tmp_path_factory.mktemp("fpsprobe-fixtures")
    generate_fixtures(destination)
    return destination


@pytest.fixture
def default_config(tmp_path: Path, fixture_dir: Path):
    config = load_config(ROOT / "fixtures" / "config" / "default.yaml", base_dir=tmp_path)
    return replace(
        config,
        input_dirs=(fixture_dir,),
        report_dir=tmp_path / "out",
        db_path=tmp_path / "out" / "fpsprobe.db",
    )


@pytest.fixture
def ledger(tmp_path: Path):
    instance = Ledger(tmp_path / "ledger.db")
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture(scope="session")
def expected_report() -> dict:
    return json.loads(
        (ROOT / "fixtures" / "expected" / "sample_report.json").read_text(
            encoding="utf-8"
        )
    )


@pytest.fixture
def entry_factory():
    return make_entry


@pytest.fixture(scope="session")
def assert_structure():
    def compare(actual, expected, path: str = "$") -> None:
        if isinstance(expected, dict):
            assert isinstance(actual, dict), f"{path}: expected object, got {type(actual)}"
            assert set(actual) == set(expected), (
                f"{path}: keys differ: actual={sorted(actual)} expected={sorted(expected)}"
            )
            for key in expected:
                compare(actual[key], expected[key], f"{path}.{key}")
            return
        if isinstance(expected, list):
            assert isinstance(actual, list), f"{path}: expected list"
            if expected and actual:
                pairs = zip(actual, expected) if len(actual) == len(expected) else [(actual[0], expected[0])]
                for index, (actual_item, expected_item) in enumerate(pairs):
                    compare(actual_item, expected_item, f"{path}[{index}]")
            return
        if expected is None:
            return
        if isinstance(expected, bool):
            assert isinstance(actual, bool), f"{path}: expected bool"
        elif isinstance(expected, int):
            assert isinstance(actual, int) and not isinstance(actual, bool), (
                f"{path}: expected int"
            )
        elif isinstance(expected, float):
            assert isinstance(actual, (int, float)) and not isinstance(actual, bool), (
                f"{path}: expected number"
            )
        else:
            assert isinstance(actual, type(expected)), (
                f"{path}: expected {type(expected).__name__}, got {type(actual).__name__}"
            )

    return compare


def make_entry(
    path: str,
    *,
    verdict: str = "CFR_OK",
    classification: str = "ok",
    status: str = "ok",
    error_code: str | None = None,
    error_message: str | None = None,
) -> dict:
    return {
        "path": path,
        "sha256": "0" * 64,
        "size_bytes": 100,
        "status": status,
        "error_code": error_code,
        "error_message": error_message,
        "container": {
            "format": "mp4",
            "brand": "isom",
            "adapter": "builtin",
            "stream_count": 1,
            "video_stream_count": 1,
            "audio_stream_count": 0,
            "movie_timescale": 1000,
            "movie_duration_seconds": 2.0,
            "declared_fps": 30.0,
            "primary_track_id": 1,
            "primary_track_timescale": 30000,
            "primary_track_duration_seconds": 2.0,
            "frame_count": 60,
            "moov_at_end": False,
        },
        "timestamps": {
            "frame_count": 60,
            "first_pts_ticks": 0,
            "last_pts_ticks": 59000,
            "first_pts_seconds": 0.0,
            "last_pts_seconds": 1.966,
            "delta_count": 59,
            "delta_ticks": {
                "count": 59,
                "min": 1000.0,
                "max": 1000.0,
                "mean": 1000.0,
                "median": 1000.0,
                "p95": 1000.0,
                "stdev": 0.0,
                "jitter": 0.0,
            },
            "delta_seconds": {
                "count": 59,
                "min": 0.033,
                "max": 0.033,
                "mean": 0.033,
                "median": 0.033,
                "p95": 0.033,
                "stdev": 0.0,
                "jitter": 0.0,
            },
            "measured_fps": 30.0,
            "selected_measured_fps": 30.0,
            "estimate_block": {
                "declared_fps": 30.0,
                "stable_segment_count": 59,
                "estimators": {
                    "mean": {"fps": 30.0, "rel_delta": 0.0},
                    "median": {"fps": 30.0, "rel_delta": 0.0},
                    "candidate_interval": {
                        "fps": 30.0,
                        "interval_ticks": 1000,
                        "rel_delta": 0.0,
                    },
                },
                "selected_estimator": "candidate_interval",
                "selected_measured_fps": 30.0,
            },
            "jitter": 0.0,
            "p95_p50": 1.0,
            "non_positive_count": 0,
            "non_positive_indices": [],
            "histogram": {
                "bucket_count": 1,
                "total": 59,
                "min": 1000.0,
                "max": 1000.0,
                "buckets": [{"lower": 1000.0, "upper": 1000.0, "count": 59}],
            },
        },
        "verdict": verdict,
        "reason_codes": [] if status == "ok" else ["PROBE_ERROR"],
        "reason_text": "test fixture entry",
        "classification": classification,
    }
