from __future__ import annotations

import struct

import pytest

from fpsprobe.container.mp4box import (
    Box,
    child_boxes,
    find_path,
    iter_boxes,
    read_payload,
)
from fpsprobe.errors import BoxFormatError, TruncatedBoxError


def box(kind: str, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), kind.encode("latin-1")) + payload


def box64(kind: str, payload: bytes) -> bytes:
    total = 16 + len(payload)
    return struct.pack(">I4sQ", 1, kind.encode("latin-1"), total) + payload


def write(tmp_path, name: str, data: bytes):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_walks_32_bit_boxes(tmp_path):
    path = write(
        tmp_path,
        "plain.mp4",
        box("ftyp", b"isom") + box("free", b"abcd") + box("moov", b""),
    )
    with path.open("rb") as stream:
        boxes = list(iter_boxes(stream))
    assert [item.type for item in boxes] == ["ftyp", "free", "moov"]
    assert all(isinstance(item, Box) for item in boxes)
    assert boxes[0].header_size == 8
    assert boxes[0].data_start == 8


def test_walks_64_bit_largesize(tmp_path):
    path = write(tmp_path, "large.mp4", box64("free", b"payload") + box("ftyp", b"isom"))
    with path.open("rb") as stream:
        boxes = list(iter_boxes(stream))
        assert boxes[0].size == 23
        assert boxes[0].header_size == 16
        assert read_payload(stream, boxes[0]) == b"payload"
    assert [item.type for item in boxes] == ["free", "ftyp"]


def test_unknown_boxes_are_skipped(tmp_path):
    path = write(
        tmp_path,
        "unknown.mp4",
        box("zzzz", b"whatever") + box("ftyp", b"isom"),
    )
    with path.open("rb") as stream:
        boxes = list(iter_boxes(stream))
    assert [item.type for item in boxes] == ["zzzz", "ftyp"]


def test_size_zero_extends_to_end(tmp_path):
    path = write(tmp_path, "zero.mp4", struct.pack(">I4s", 0, b"mdat") + b"12345")
    with path.open("rb") as stream:
        boxes = list(iter_boxes(stream))
    assert len(boxes) == 1
    assert boxes[0].size == path.stat().st_size
    assert boxes[0].data_end == path.stat().st_size


def test_nested_boxes_can_be_found(tmp_path):
    mvhd = box("mvhd", b"data")
    stbl = box("stbl", box("stts", b"stts") + box("stco", b""))
    minf = box("minf", box("dinf", b"") + stbl)
    moov = box("moov", mvhd + box("trak", box("mdia", minf)))
    path = write(tmp_path, "nested.mp4", box("ftyp", b"isom") + moov)
    with path.open("rb") as stream:
        top = list(iter_boxes(stream))
        moov_box = top[1]
        assert [item.type for item in child_boxes(stream, moov_box)] == ["mvhd", "trak"]
        found = find_path(stream, moov_box, "trak", "mdia", "minf", "stbl", "stts")
        assert found is not None
        assert found.type == "stts"
        assert find_path(stream, moov_box, "trak", "mdia", "nope") is None


def test_truncated_header_is_explicit(tmp_path):
    path = write(tmp_path, "short.mp4", b"\x00\x00\x00")
    with path.open("rb") as stream:
        with pytest.raises(TruncatedBoxError) as caught:
            list(iter_boxes(stream))
    assert "truncated box header" in str(caught.value)
    assert caught.value.code == "MP4_TRUNCATED_BOX"


def test_declared_size_beyond_file_is_explicit(tmp_path):
    path = write(tmp_path, "overrun.mp4", struct.pack(">I4s", 100, b"mdat") + b"abc")
    with path.open("rb") as stream:
        with pytest.raises(TruncatedBoxError) as caught:
            list(iter_boxes(stream))
    assert "claims size 100" in str(caught.value)


def test_invalid_small_size_is_reported(tmp_path):
    path = write(tmp_path, "bad.mp4", struct.pack(">I4s", 4, b"free"))
    with path.open("rb") as stream:
        with pytest.raises(BoxFormatError):
            list(iter_boxes(stream))
