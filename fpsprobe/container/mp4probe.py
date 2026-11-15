"""Metadata-only MP4 probing: mvhd/tkhd/mdhd/stts/ctts -> timestamps."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import BinaryIO

from ..errors import BoxFormatError, ProbeError
from ..models import ProbeResult, TrackInfo, TrackTimestamps
from .base import select_primary_track
from .mp4box import Box, child_boxes, find_child, iter_boxes, read_payload


def _u32(data: bytes, offset: int, label: str) -> int:
    if offset + 4 > len(data):
        raise BoxFormatError(f"truncated {label} field")
    return struct.unpack_from(">I", data, offset)[0]


def _u64(data: bytes, offset: int, label: str) -> int:
    if offset + 8 > len(data):
        raise BoxFormatError(f"truncated {label} field")
    return struct.unpack_from(">Q", data, offset)[0]


def _version_flags(payload: bytes, label: str) -> tuple[int, int]:
    if len(payload) < 4:
        raise BoxFormatError(f"{label} box is too small")
    version = payload[0]
    flags = int.from_bytes(payload[1:4], "big")
    return version, flags


def parse_mvhd(payload: bytes) -> tuple[int, int]:
    version, _flags = _version_flags(payload, "mvhd")
    if version == 0:
        timescale = _u32(payload, 12, "mvhd timescale")
        duration = _u32(payload, 16, "mvhd duration")
    elif version == 1:
        timescale = _u32(payload, 20, "mvhd timescale")
        duration = _u64(payload, 24, "mvhd duration")
    else:
        raise BoxFormatError(f"unsupported mvhd version {version}")
    if timescale <= 0:
        raise ProbeError("mvhd timescale must be positive", code="MP4_INVALID_TIMESCALE")
    return timescale, int(duration)


def parse_tkhd(payload: bytes) -> tuple[int, int, int]:
    version, _flags = _version_flags(payload, "tkhd")
    if version == 0:
        track_id = _u32(payload, 12, "tkhd track id")
        duration = _u32(payload, 20, "tkhd duration")
    elif version == 1:
        track_id = _u32(payload, 20, "tkhd track id")
        duration = _u64(payload, 28, "tkhd duration")
    else:
        raise BoxFormatError(f"unsupported tkhd version {version}")
    width = _u32(payload, len(payload) - 8, "tkhd width") if len(payload) >= 84 else 0
    height = _u32(payload, len(payload) - 4, "tkhd height") if len(payload) >= 84 else 0
    return int(track_id), int(duration), (width >> 16, height >> 16)


def parse_mdhd(payload: bytes) -> tuple[int, int]:
    version, _flags = _version_flags(payload, "mdhd")
    if version == 0:
        timescale = _u32(payload, 12, "mdhd timescale")
        duration = _u32(payload, 16, "mdhd duration")
    elif version == 1:
        timescale = _u32(payload, 20, "mdhd timescale")
        duration = _u64(payload, 24, "mdhd duration")
    else:
        raise BoxFormatError(f"unsupported mdhd version {version}")
    if timescale <= 0:
        raise ProbeError("mdhd timescale must be positive", code="MP4_INVALID_TIMESCALE")
    return timescale, int(duration)


def parse_handler(payload: bytes) -> str:
    if len(payload) < 12:
        return ""
    return payload[8:12].decode("latin-1")


def parse_stts(payload: bytes) -> list[tuple[int, int]]:
    _version, _flags = _version_flags(payload, "stts")
    count = _u32(payload, 4, "stts entry count")
    if 8 + count * 8 > len(payload):
        raise BoxFormatError("stts entry table is truncated")
    entries: list[tuple[int, int]] = []
    for index in range(count):
        base = 8 + index * 8
        sample_count, sample_delta = struct.unpack_from(">II", payload, base)
        if sample_count and sample_delta < 0:
            raise BoxFormatError("stts delta must be non-negative")
        entries.append((int(sample_count), int(sample_delta)))
    return entries


def parse_ctts(payload: bytes) -> tuple[int, list[tuple[int, int]]]:
    version, _flags = _version_flags(payload, "ctts")
    if version not in (0, 1):
        raise BoxFormatError(f"unsupported ctts version {version}")
    count = _u32(payload, 4, "ctts entry count")
    if 8 + count * 8 > len(payload):
        raise BoxFormatError("ctts entry table is truncated")
    entries: list[tuple[int, int]] = []
    fmt = ">Ii" if version == 1 else ">II"
    for index in range(count):
        base = 8 + index * 8
        sample_count, offset = struct.unpack_from(fmt, payload, base)
        entries.append((int(sample_count), int(offset)))
    return version, entries


def _expand(entries: list[tuple[int, int]]) -> list[int]:
    values: list[int] = []
    for count, value in entries:
        if count > 10_000_000:
            raise ProbeError("sample table exceeds safety limit", code="MP4_TOO_MANY_SAMPLES")
        values.extend([value] * count)
    return values


def _build_timestamps(
    track: TrackInfo,
    stts_entries: list[tuple[int, int]],
    ctts: tuple[int, list[tuple[int, int]]] | None,
) -> TrackTimestamps:
    deltas = _expand(stts_entries)
    if len(deltas) != track.sample_count:
        raise BoxFormatError("stts sample count is inconsistent")
    dts: list[int] = []
    cursor = 0
    for delta in deltas:
        dts.append(cursor)
        cursor += delta
    if ctts is not None:
        offsets = _expand(ctts[1])
        if len(offsets) != len(dts):
            raise BoxFormatError(
                f"ctts covers {len(offsets)} samples but track has {len(dts)}"
            )
    else:
        offsets = [0] * len(dts)
    pts = [dts_value + offset for dts_value, offset in zip(dts, offsets)]
    return TrackTimestamps(
        track=track,
        pts_ticks=pts,
        dts_ticks=dts,
        stts_deltas=deltas,
    )


def _read_track(
    stream: BinaryIO, trak: Box, fallback_id: int
) -> tuple[TrackInfo, TrackTimestamps] | None:
    mdia = find_child(stream, trak, "mdia")
    if mdia is None:
        return None
    mdhd = find_child(stream, mdia, "mdhd")
    if mdhd is None:
        return None
    timescale, duration = parse_mdhd(read_payload(stream, mdhd))
    handler = ""
    hdlr = find_child(stream, mdia, "hdlr")
    if hdlr is not None:
        handler = parse_handler(read_payload(stream, hdlr))
    track_id = fallback_id
    width = height = 0
    tkhd = find_child(stream, trak, "tkhd")
    if tkhd is not None:
        track_id, _track_duration, (width, height) = parse_tkhd(
            read_payload(stream, tkhd)
        )
    stbl = find_child(stream, mdia, "minf")
    stbl = find_child(stream, stbl, "stbl") if stbl is not None else None
    stts_box = find_child(stream, stbl, "stts") if stbl is not None else None
    if stts_box is None:
        if handler in {"vide", "soun"}:
            raise ProbeError(
                f"track {track_id} has no stts sample table",
                code="MP4_MISSING_STTS",
            )
        return None
    stts_entries = parse_stts(read_payload(stream, stts_box))
    sample_count = sum(count for count, _delta in stts_entries)
    ctts_box = find_child(stream, stbl, "ctts") if stbl is not None else None
    ctts = parse_ctts(read_payload(stream, ctts_box)) if ctts_box is not None else None
    if duration <= 0:
        duration = sum(count * delta for count, delta in stts_entries)
    info = TrackInfo(
        track_id=track_id,
        handler_type=handler,
        timescale=timescale,
        duration_ticks=duration,
        sample_count=sample_count,
        width=width,
        height=height,
        stts_entries=tuple(stts_entries),
        ctts_version=ctts[0] if ctts is not None else None,
    )
    return info, _build_timestamps(info, stts_entries, ctts)


def probe_mp4(path: str | Path, primary_track: str | int = "first_video") -> ProbeResult:
    """Read metadata and per-frame timestamps without decoding sample data."""
    source = Path(path)
    try:
        file_size = source.stat().st_size
        stream = source.open("rb")
    except OSError as exc:
        raise ProbeError(f"cannot open video file: {source.name}", code="FILE_OPEN_FAILED") from exc
    with stream:
        top_level = list(iter_boxes(stream, 0, file_size))
        top_types = [box.type for box in top_level]
        moov = next((box for box in top_level if box.type == "moov"), None)
        if moov is None:
            raise ProbeError("moov box not found", code="MOOV_NOT_FOUND")
        brand = None
        ftyp = next((box for box in top_level if box.type == "ftyp"), None)
        if ftyp is not None:
            payload = read_payload(stream, ftyp)
            brand = payload[:4].decode("latin-1") if len(payload) >= 4 else None
        moov_children = child_boxes(stream, moov)
        mvhd = next((box for box in moov_children if box.type == "mvhd"), None)
        if mvhd is None:
            raise ProbeError("mvhd box not found", code="MP4_MISSING_MVHD")
        movie_timescale, movie_duration = parse_mvhd(read_payload(stream, mvhd))
        tracks: list[TrackInfo] = []
        timestamps_by_id: dict[int, TrackTimestamps] = {}
        for index, trak in enumerate(
            box for box in moov_children if box.type == "trak"
        ):
            parsed = _read_track(stream, trak, index + 1)
            if parsed is None:
                continue
            info, timestamps = parsed
            tracks.append(info)
            timestamps_by_id[info.track_id] = timestamps
    primary_info, _primary_index = select_primary_track(tracks, primary_track)
    primary_timestamps = timestamps_by_id[primary_info.track_id]
    duration_seconds = movie_duration / movie_timescale if movie_duration > 0 else 0.0
    declared_fps = (
        primary_info.sample_count / duration_seconds if duration_seconds > 0 else None
    )
    moov_at_end = "mdat" in top_types and top_types.index("moov") > top_types.index("mdat")
    return ProbeResult(
        path=source,
        file_size=file_size,
        format="mp4",
        brand=brand,
        movie_timescale=movie_timescale,
        movie_duration_ticks=movie_duration,
        tracks=tracks,
        primary_track=primary_info,
        primary_timestamps=primary_timestamps,
        declared_fps=declared_fps,
        moov_at_end=moov_at_end,
        timestamps_by_id=timestamps_by_id,
    )
