"""Configuration loading with YAML-or-JSON fallback and validation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ConfigError, ConfigInvalid, ConfigMissingField, ConfigNotFound
from .models import Thresholds

CLASSIFICATION_VALUES = {"ok", "review", "reject"}
PRIMARY_STRATEGIES = {"first_video", "longest_video"}
SUPPORTED_ADAPTERS = {"builtin", "ffprobe"}


@dataclass(frozen=True)
class Config:
    source_path: Path
    base_dir: Path
    input_dirs: tuple[Path, ...]
    recursive: bool
    include_suffixes: tuple[str, ...]
    ignore_globs: tuple[str, ...]
    primary_track: str | int
    max_detail_frames: int
    histogram_buckets: int
    report_dir: Path
    json_report_name: str
    markdown_report_name: str
    db_path: Path
    thresholds: Thresholds
    classification: dict[str, str]
    adapter: str
    server_host: str
    server_port: int
    raw: dict[str, Any]
    fingerprint: str

    def input_label(self) -> str:
        labels: list[str] = []
        for path in self.input_dirs:
            try:
                labels.append(path.resolve().relative_to(self.base_dir.resolve()).as_posix())
            except ValueError:
                labels.append(path.name)
        return ",".join(sorted(labels))


def _require(mapping: dict[str, Any], key: str, dotted: str) -> Any:
    if not isinstance(mapping, dict) or key not in mapping:
        raise ConfigMissingField(f"missing required configuration field: {dotted}")
    return mapping[key]


def _number(value: Any, field: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigInvalid(f"{field} must be a number")
    number = float(value)
    if number < minimum:
        raise ConfigInvalid(f"{field} must be >= {minimum}")
    return number


def _integer(value: Any, field: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigInvalid(f"{field} must be an integer")
    if value < minimum:
        raise ConfigInvalid(f"{field} must be >= {minimum}")
    return value


def _normalise_primary(value: Any) -> str | int:
    if isinstance(value, bool):
        raise ConfigInvalid("primary_track must be first_video, longest_video, or an index")
    if isinstance(value, int):
        if value < 0:
            raise ConfigInvalid("primary_track index must be >= 0")
        return value
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned in PRIMARY_STRATEGIES:
            return cleaned
        if cleaned.isdigit():
            return int(cleaned)
    raise ConfigInvalid("primary_track must be first_video, longest_video, or an index")


def _read_mapping(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8")
    try:
        if suffix == ".json":
            value = json.loads(text)
        else:
            try:
                import yaml  # type: ignore
            except ImportError as exc:  # pragma: no cover - backend availability
                raise ConfigError(
                    "YAML input requires PyYAML; provide a same-name .json file instead",
                    code="YAML_BACKEND_MISSING",
                ) from exc
            value = yaml.safe_load(text)
    except ConfigError:
        raise
    except Exception as exc:
        raise ConfigError(
            f"cannot parse configuration {path.name}: {exc}",
            code="CONFIG_PARSE_ERROR",
        ) from exc
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ConfigInvalid("configuration root must be a mapping/object")
    return value


def _load_mapping(requested: Path) -> dict[str, Any]:
    reader = requested
    if requested.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # noqa: F401
        except ImportError:
            reader = requested.with_suffix(".json")
            if not reader.exists():
                raise ConfigError(
                    "PyYAML is unavailable and no JSON fallback exists",
                    code="YAML_BACKEND_MISSING",
                )
    if not reader.exists():
        raise ConfigNotFound(f"configuration file not found: {requested}")
    return _read_mapping(reader)


def _classifications(raw: dict[str, Any]) -> dict[str, str]:
    section = _require(raw, "classification", "classification")
    if not isinstance(section, dict):
        raise ConfigInvalid("classification must be a mapping")
    result: dict[str, str] = {}
    for verdict in ("CFR_OK", "CFR_MISMATCH", "VFR_SUSPECT", "TIMESTAMP_BROKEN"):
        key = verdict.lower()
        value = _require(section, key, f"classification.{key}")
        if value not in CLASSIFICATION_VALUES:
            raise ConfigInvalid(f"classification.{key} must be ok, review, or reject")
        result[verdict] = value
    return result


def load_config(path: str | Path, base_dir: str | Path | None = None) -> Config:
    requested = Path(path)
    base = Path(base_dir or Path.cwd()).resolve()
    raw = _load_mapping(requested)

    input_raw = _require(raw, "input_dirs", "input_dirs")
    if isinstance(input_raw, str):
        input_raw = [input_raw]
    if not isinstance(input_raw, list) or not input_raw:
        raise ConfigInvalid("input_dirs must be a non-empty list")
    input_dirs = tuple(
        (base / str(item)).resolve() if not Path(str(item)).is_absolute() else Path(str(item))
        for item in input_raw
    )

    suffixes_raw = _require(raw, "include_suffixes", "include_suffixes")
    if not isinstance(suffixes_raw, list) or not suffixes_raw:
        raise ConfigInvalid("include_suffixes must be a non-empty list")
    suffixes = tuple(
        str(item).lower() if str(item).startswith(".") else "." + str(item).lower()
        for item in suffixes_raw
    )

    ignores = _require(raw, "ignore_globs", "ignore_globs")
    if not isinstance(ignores, list):
        raise ConfigInvalid("ignore_globs must be a list")

    report = _require(raw, "report", "report")
    database = _require(raw, "database", "database")
    threshold_raw = _require(raw, "thresholds", "thresholds")
    server = _require(raw, "server", "server")
    if not all(isinstance(item, dict) for item in (report, database, threshold_raw, server)):
        raise ConfigInvalid("report, database, thresholds, and server must be mappings")

    thresholds = Thresholds(
        fps_abs_tolerance=_number(
            _require(threshold_raw, "fps_abs_tolerance", "thresholds.fps_abs_tolerance"),
            "thresholds.fps_abs_tolerance",
        ),
        fps_rel_tolerance=_number(
            _require(threshold_raw, "fps_rel_tolerance", "thresholds.fps_rel_tolerance"),
            "thresholds.fps_rel_tolerance",
        ),
        cfr_delta_rel_tolerance=_number(
            _require(
                threshold_raw,
                "cfr_delta_rel_tolerance",
                "thresholds.cfr_delta_rel_tolerance",
            ),
            "thresholds.cfr_delta_rel_tolerance",
        ),
        vfr_jitter_threshold=_number(
            _require(
                threshold_raw,
                "vfr_jitter_threshold",
                "thresholds.vfr_jitter_threshold",
            ),
            "thresholds.vfr_jitter_threshold",
        ),
        vfr_p95_p50_threshold=_number(
            _require(
                threshold_raw,
                "vfr_p95_p50_threshold",
                "thresholds.vfr_p95_p50_threshold",
            ),
            "thresholds.vfr_p95_p50_threshold",
        ),
        mismatch_review_abs_fps=_number(
            _require(
                threshold_raw,
                "mismatch_review_abs_fps",
                "thresholds.mismatch_review_abs_fps",
            ),
            "thresholds.mismatch_review_abs_fps",
        ),
    )

    adapter = raw.get("adapter", "builtin")
    if adapter not in SUPPORTED_ADAPTERS:
        raise ConfigInvalid("adapter must be builtin or ffprobe")
    host = str(_require(server, "host", "server.host"))
    port = _integer(_require(server, "port", "server.port"), "server.port", minimum=1)
    if port > 65535:
        raise ConfigInvalid("server.port must be <= 65535")

    fingerprint_payload = json.dumps(raw, sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(fingerprint_payload.encode("utf-8")).hexdigest()[:16]

    return Config(
        source_path=requested,
        base_dir=base,
        input_dirs=input_dirs,
        recursive=bool(_require(raw, "recursive", "recursive")),
        include_suffixes=suffixes,
        ignore_globs=tuple(str(item) for item in ignores),
        primary_track=_normalise_primary(
            _require(raw, "primary_track", "primary_track")
        ),
        max_detail_frames=_integer(
            _require(raw, "max_detail_frames", "max_detail_frames"),
            "max_detail_frames",
        ),
        histogram_buckets=_integer(
            _require(raw, "histogram_buckets", "histogram_buckets"),
            "histogram_buckets",
            minimum=1,
        ),
        report_dir=(base / str(_require(report, "output_dir", "report.output_dir"))).resolve(),
        json_report_name=str(_require(report, "json_name", "report.json_name")),
        markdown_report_name=str(
            _require(report, "markdown_name", "report.markdown_name")
        ),
        db_path=(base / str(_require(database, "path", "database.path"))).resolve(),
        thresholds=thresholds,
        classification=_classifications(raw),
        adapter=adapter,
        server_host=host,
        server_port=port,
        raw=raw,
        fingerprint=fingerprint,
    )
