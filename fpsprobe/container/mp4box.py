"""A small, read-only MP4 ISO-BMFF box walker.

Only box headers and explicitly requested metadata payloads are read. The
walker deliberately never interprets sample data (``mdat``).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import BinaryIO, Iterator

from ..errors import BoxFormatError, TruncatedBoxError

CONTAINER_TYPES = {
    "moov",
    "trak",
    "mdia",
    "minf",
    "stbl",
    "dinf",
    "edts",
    "udta",
}
MAX_METADATA_PAYLOAD = 32 * 1024 * 1024


@dataclass(frozen=True)
class Box:
    type: str
    start: int
    size: int
    header_size: int

    @property
    def data_start(self) -> int:
        return self.start + self.header_size

    @property
    def data_end(self) -> int:
        return self.start + self.size

    @property
    def end(self) -> int:
        return self.start + self.size

    @property
    def is_container(self) -> bool:
        return self.type in CONTAINER_TYPES

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Box({self.type!r}, start={self.start}, size={self.size})"


def _read_exact(stream: BinaryIO, length: int, *, offset: int) -> bytes:
    stream.seek(offset)
    data = stream.read(length)
    if len(data) != length:
        raise TruncatedBoxError(
            f"truncated box header at offset {offset}: needed {length} bytes, got {len(data)}"
        )
    return data


def iter_boxes(
    stream: BinaryIO, start: int = 0, end: int | None = None
) -> Iterator[Box]:
    """Iterate box headers in ``[start, end)``.

    Supports classic 32-bit sizes, 64-bit ``largesize`` (size == 1), and
    "box extends to the end of its parent" (size == 0). Unknown box types are
    skipped by advancing using their declared size.
    """
    if end is None:
        stream.seek(0, 2)
        end = stream.tell()
    if start < 0 or end < start:
        raise BoxFormatError("invalid box scan range")
    position = start
    while position < end:
        remaining = end - position
        if remaining < 8:
            raise TruncatedBoxError(
                f"truncated box header at offset {position}: only {remaining} bytes remain"
            )
        header = _read_exact(stream, 8, offset=position)
        size32, type_bytes = struct.unpack(">I4s", header)
        type_name = type_bytes.decode("latin-1")
        header_size = 8
        if size32 == 1:
            if remaining < 16:
                raise TruncatedBoxError(
                    f"truncated 64-bit box header at offset {position}"
                )
            (large_size,) = struct.unpack(">Q", _read_exact(stream, 8, offset=position + 8))
            size = int(large_size)
            header_size = 16
            if size < 16:
                raise BoxFormatError(
                    f"64-bit box {type_name!r} at offset {position} has invalid size {size}"
                )
        elif size32 == 0:
            size = end - position
        else:
            size = int(size32)
            if size < 8:
                raise BoxFormatError(
                    f"box {type_name!r} at offset {position} has invalid size {size}"
                )
        if position + size > end:
            raise TruncatedBoxError(
                f"box {type_name!r} at offset {position} claims size {size}, "
                f"but only {remaining} bytes remain"
            )
        yield Box(type_name, position, size, header_size)
        position += size


def read_payload(stream: BinaryIO, box: Box) -> bytes:
    length = box.size - box.header_size
    if length < 0:
        raise BoxFormatError(f"box {box.type!r} has a negative payload length")
    if length > MAX_METADATA_PAYLOAD:
        raise BoxFormatError(
            f"metadata box {box.type!r} is too large to read ({length} bytes)"
        )
    stream.seek(box.data_start)
    data = stream.read(length)
    if len(data) != length:
        raise TruncatedBoxError(f"truncated payload for box {box.type!r}")
    return data


def child_boxes(stream: BinaryIO, box: Box) -> list[Box]:
    if not box.is_container:
        return []
    return list(iter_boxes(stream, box.data_start, box.data_end))


def find_child(stream: BinaryIO, box: Box, type_name: str) -> Box | None:
    for child in child_boxes(stream, box):
        if child.type == type_name:
            return child
    return None


def find_path(stream: BinaryIO, box: Box, *types: str) -> Box | None:
    current: Box | None = box
    for type_name in types:
        if current is None:
            return None
        current = find_child(stream, current, type_name)
    return current
