#!/usr/bin/env python3
"""Deterministic, dependency-free synthetic MP4 metadata fixtures.

The generated files contain valid ISO-BMFF box structure and timing tables, but
their sample payloads are inert zero bytes. They exist to exercise fpsprobe's
metadata reader, not to be decoded as video.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from dataclasses import dataclass
from pathlib import Path

MATRIX = struct.pack(
    ">9I", 0x00010000, 0, 0, 0, 0x00010000, 0, 0, 0, 0x40000000
)


@dataclass(frozen=True)
class TrackSpec:
    track_id: int
    handler: str
    timescale: int
    stts: tuple[tuple[int, int], ...]
    ctts: tuple[tuple[int, int], ...] | None = None
    width: int = 0
    height: int = 0
    channels: int = 2
    # Optional tkhd duration override in movie-timescale units; when None the
    # tkhd duration is derived from the stts accumulation (consistent file).
    tkhd_duration: int | None = None

    @property
    def sample_count(self) -> int:
        return sum(count for count, _delta in self.stts)

    @property
    def duration_ticks(self) -> int:
        return sum(count * delta for count, delta in self.stts)

    @property
    def duration_seconds(self) -> float:
        return self.duration_ticks / self.timescale


def _box(kind: str, payload: bytes) -> bytes:
    encoded = kind.encode("latin-1")
    return struct.pack(">I4s", 8 + len(payload), encoded) + payload


def _full(kind: str, version: int, flags: int, payload: bytes) -> bytes:
    return _box(kind, bytes([version]) + flags.to_bytes(3, "big") + payload)


def _mvhd(timescale: int, duration: int, next_track_id: int) -> bytes:
    body = struct.pack(">IIII", 0, 0, timescale, duration)
    body += struct.pack(">IHH", 0x00010000, 0x0100, 0)
    body += b"\x00" * 8 + MATRIX + b"\x00" * 24
    body += struct.pack(">I", next_track_id)
    return _full("mvhd", 0, 0, body)


def _tkhd(track: TrackSpec, movie_timescale: int) -> bytes:
    if track.tkhd_duration is not None:
        duration = track.tkhd_duration
    else:
        duration = round(track.duration_seconds * movie_timescale)
    volume = 0x0100 if track.handler == "soun" else 0
    body = struct.pack(">IIIII", 0, 0, track.track_id, 0, duration)
    body += b"\x00" * 8
    body += struct.pack(">hhhh", 0, 0, volume, 0)
    body += MATRIX
    body += struct.pack(">II", track.width << 16, track.height << 16)
    return _full("tkhd", 0, 0, body)


def _mdhd(track: TrackSpec) -> bytes:
    body = struct.pack(">IIIIHH", 0, 0, track.timescale, track.duration_ticks, 0x55C4, 0)
    return _full("mdhd", 0, 0, body)


def _hdlr(track: TrackSpec) -> bytes:
    name = b"VideoHandler\x00" if track.handler == "vide" else b"SoundHandler\x00"
    body = struct.pack(">I4s", 0, track.handler.encode("latin-1")) + b"\x00" * 12 + name
    return _full("hdlr", 0, 0, body)


def _sample_entry(track: TrackSpec) -> bytes:
    if track.handler == "vide":
        payload = b"\x00" * 6 + struct.pack(">H", 1) + b"\x00" * 16
        payload += struct.pack(">HH", track.width, track.height)
        payload += struct.pack(">II", 0x00480000, 0x00480000) + b"\x00" * 4
        payload += struct.pack(">H", 1) + b"\x00" * 32
        payload += struct.pack(">Hh", 0x0018, -1)
        return _box("avc1", payload)
    payload = b"\x00" * 6 + struct.pack(">H", 1) + b"\x00" * 8
    payload += struct.pack(">HHHH", track.channels, 16, 0, 0)
    payload += struct.pack(">I", 48000 << 16)
    return _box("mp4a", payload)


def _stbl(track: TrackSpec, chunk_offset: int) -> bytes:
    stsd_payload = struct.pack(">I", 1) + _sample_entry(track)
    boxes = [_full("stsd", 0, 0, stsd_payload)]
    stts_body = struct.pack(">I", len(track.stts))
    for count, delta in track.stts:
        stts_body += struct.pack(">II", count, delta)
    boxes.append(_full("stts", 0, 0, stts_body))
    if track.ctts:
        ctts_body = struct.pack(">I", len(track.ctts))
        for count, offset in track.ctts:
            ctts_body += struct.pack(">Ii", count, offset)
        boxes.append(_full("ctts", 1, 0, ctts_body))
    if track.sample_count:
        stsc_body = struct.pack(">IIII", 1, 1, track.sample_count, 1)
        stco_body = struct.pack(">II", 1, chunk_offset)
    else:
        stsc_body = struct.pack(">I", 0)
        stco_body = struct.pack(">I", 0)
    boxes.append(_full("stsc", 0, 0, stsc_body))
    boxes.append(_full("stsz", 0, 0, struct.pack(">II", 1, track.sample_count)))
    boxes.append(_full("stco", 0, 0, stco_body))
    return _box("stbl", b"".join(boxes))


def _minf(track: TrackSpec, chunk_offset: int) -> bytes:
    if track.handler == "vide":
        media_header = _full("vmhd", 0, 1, struct.pack(">HHHH", 0, 0, 0, 0))
    else:
        media_header = _full("smhd", 0, 0, struct.pack(">HH", 0, 0))
    return _box("minf", media_header + _stbl(track, chunk_offset))


def _trak(track: TrackSpec, movie_timescale: int, chunk_offset: int) -> bytes:
    mdia = _box("mdia", _mdhd(track) + _hdlr(track) + _minf(track, chunk_offset))
    return _box("trak", _tkhd(track, movie_timescale) + mdia)


def _moov(
    tracks: list[TrackSpec],
    movie_duration_ms: int,
    movie_timescale: int,
    chunk_base: int,
) -> bytes:
    children = [_mvhd(movie_timescale, movie_duration_ms, max(t.track_id for t in tracks) + 1)]
    offset = chunk_base
    for track in tracks:
        children.append(_trak(track, movie_timescale, offset))
        offset += track.sample_count
    return _box("moov", b"".join(children))


def _ftyp() -> bytes:
    return _box("ftyp", b"isom" + struct.pack(">I", 512) + b"isomiso2avc1mp41")


def build_mp4(
    tracks: list[TrackSpec],
    *,
    movie_duration_ms: int = 2000,
    movie_timescale: int = 1000,
    moov_at_end: bool = False,
) -> bytes:
    if not tracks:
        raise ValueError("at least one track is required")
    ftyp = _ftyp()
    mdat_payload = b"\x00" * sum(track.sample_count for track in tracks)
    mdat = _box("mdat", mdat_payload)
    if moov_at_end:
        chunk_base = len(ftyp) + 8
        moov = _moov(tracks, movie_duration_ms, movie_timescale, chunk_base)
        return ftyp + mdat + moov
    placeholder = _moov(tracks, movie_duration_ms, movie_timescale, 0)
    chunk_base = len(ftyp) + len(placeholder) + 8
    moov = _moov(tracks, movie_duration_ms, movie_timescale, chunk_base)
    if len(moov) != len(placeholder):  # pragma: no cover - structural invariant
        raise AssertionError("moov size changed after chunk offset calculation")
    return ftyp + moov + mdat


CFR_30 = [
    TrackSpec(1, "vide", 30000, ((60, 1000),), width=640, height=360),
]
CFR_2997 = [
    TrackSpec(1, "vide", 30000, ((60, 1001),), width=1280, height=720),
]
VFR_JITTER = [
    TrackSpec(
        1,
        "vide",
        30000,
        tuple([(1, 500), (1, 1500)] * 30),
        width=640,
        height=360,
    ),
]
BROKEN_ORDER = [
    TrackSpec(
        1,
        "vide",
        30000,
        ((60, 1000),),
        ctts=((10, 0), (1, -2000), (1, -1000), (1, -2000), (1, -1000), (1, -2000), (45, 0)),
        width=640,
        height=360,
    ),
]
MOOV_AT_END = [
    TrackSpec(1, "vide", 30000, ((60, 1000),), width=640, height=360),
    TrackSpec(2, "soun", 48000, ((94, 1024),), channels=2),
]


def fixture_definitions() -> list[dict]:
    return [
        {
            "name": "cfr_30.mp4",
            "description": "60 frames at exactly 30 fps, moov before mdat",
            "tracks": CFR_30,
            "moov_at_end": False,
        },
        {
            "name": "cfr_2997.mp4",
            "description": "29.97 fps intervals with a rounded 30 fps movie declaration",
            "tracks": CFR_2997,
            "moov_at_end": False,
        },
        {
            "name": "vfr_jitter.mp4",
            "description": "alternating short/long intervals, high jitter",
            "tracks": VFR_JITTER,
            "moov_at_end": False,
        },
        {
            "name": "broken_order.mp4",
            "description": "signed composition offsets causing reversals and duplicates",
            "tracks": BROKEN_ORDER,
            "moov_at_end": False,
        },
        {
            "name": "moov_at_end.mp4",
            "description": "video plus audio, moov after mdat",
            "tracks": MOOV_AT_END,
            "moov_at_end": True,
        },
    ]


def generate_fixtures(output_dir: str | Path) -> list[dict]:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    for definition in fixture_definitions():
        data = build_mp4(
            definition["tracks"],
            movie_duration_ms=2000,
            moov_at_end=definition["moov_at_end"],
        )
        path = destination / definition["name"]
        path.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        records.append(
            {
                "name": definition["name"],
                "path": definition["name"],
                "description": definition["description"],
                "sha256": digest,
                "size_bytes": len(data),
            }
        )
    manifest = {"fixture_version": 1, "files": records}
    (destination / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return records


generate_all = generate_fixtures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate deterministic fpsprobe fixtures")
    parser.add_argument("--out-dir", default="fixtures/out")
    arguments = parser.parse_args(argv)
    records = generate_fixtures(arguments.out_dir)
    for record in records:
        print(f"{record['sha256']}  {record['name']}  ({record['size_bytes']} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
