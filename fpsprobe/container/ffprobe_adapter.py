"""Optional ffprobe adapter. It is disabled by default and never auto-required.

The built-in reader is sufficient for offline operation; this adapter offers a
local cross-check when the user explicitly configures ``adapter: ffprobe`` and
an ffprobe binary is already installed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from ..errors import AdapterUnavailable, ProbeError
from ..models import ProbeResult, TrackInfo, TrackTimestamps
from .base import select_primary_track

MICROSECOND_TIMESCALE = 1_000_000


class FfprobeAdapter:
    name = "ffprobe"

    @staticmethod
    def available() -> bool:
        return shutil.which("ffprobe") is not None

    def _run(self, arguments: list[str]) -> dict:
        if not self.available():
            raise AdapterUnavailable(
                "ffprobe is not installed; the built-in MP4 reader remains available"
            )
        command = [
            "ffprobe",
            "-hide_banner",
            "-v",
            "error",
            "-print_format",
            "json",
            *arguments,
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ProbeError(f"ffprobe execution failed: {exc}", code="FFPROBE_FAILED") from exc
        if completed.returncode != 0:
            message = completed.stderr.strip().splitlines()[:1]
            raise ProbeError(
                f"ffprobe rejected the file: {message[0] if message else 'unknown error'}",
                code="FFPROBE_FAILED",
            )
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ProbeError("ffprobe returned invalid JSON", code="FFPROBE_BAD_JSON") from exc

    @staticmethod
    def _rational(value: str | None) -> float | None:
        if not value or value in {"0/0", "N/A"}:
            return None
        try:
            numerator, denominator = value.split("/", 1)
            if float(denominator) == 0:
                return None
            return float(numerator) / float(denominator)
        except (ValueError, ZeroDivisionError):
            return None

    @staticmethod
    def _timescale(stream: dict) -> int:
        time_base = str(stream.get("time_base", "1/1000"))
        try:
            denominator = int(time_base.split("/", 1)[1])
            return denominator if denominator > 0 else 1000
        except (ValueError, IndexError):
            return 1000

    def _track(self, stream: dict) -> TrackInfo:
        codec = str(stream.get("codec_type", ""))
        handler = "vide" if codec == "video" else "soun" if codec == "audio" else codec
        timescale = self._timescale(stream)
        duration = float(stream.get("duration") or 0.0)
        frames = stream.get("nb_frames")
        try:
            sample_count = int(frames) if frames not in (None, "N/A") else 0
        except (TypeError, ValueError):
            sample_count = 0
        return TrackInfo(
            track_id=int(stream.get("index", 0)) + 1,
            handler_type=handler,
            timescale=timescale,
            duration_ticks=round(duration * timescale),
            sample_count=sample_count,
            width=int(stream.get("width") or 0),
            height=int(stream.get("height") or 0),
        )

    def probe(self, path: str, primary_track: str | int = "first_video") -> ProbeResult:
        source = Path(path)
        if not source.is_file():
            raise ProbeError(f"cannot open video file: {source.name}", code="FILE_OPEN_FAILED")
        info = self._run(["-show_format", "-show_streams", str(source)])
        streams = info.get("streams", [])
        tracks = [self._track(stream) for stream in streams]
        video_pairs = [
            (stream, track)
            for stream, track in zip(streams, tracks)
            if track.handler_type == "vide"
        ]
        if not video_pairs:
            raise ProbeError("no video track found", code="NO_VIDEO_TRACK")
        video_tracks = [track for _stream, track in video_pairs]
        chosen_info, chosen_position = select_primary_track(video_tracks, primary_track)
        chosen_stream = video_pairs[chosen_position][0]
        packets = self._run(
            [
                "-select_streams",
                str(chosen_stream.get("index", 0)),
                "-show_entries",
                "packet=pts_time,dts_time",
                "-show_packets",
                str(source),
            ]
        ).get("packets", [])
        pts: list[int] = []
        dts: list[int] = []
        for packet in packets:
            try:
                if packet.get("pts_time") is not None:
                    pts.append(round(float(packet["pts_time"]) * MICROSECOND_TIMESCALE))
                if packet.get("dts_time") is not None:
                    dts.append(round(float(packet["dts_time"]) * MICROSECOND_TIMESCALE))
            except (TypeError, ValueError):
                continue
        if not pts:
            raise ProbeError(
                "ffprobe returned no presentation timestamps", code="FFPROBE_NO_TIMESTAMPS"
            )
        pts.sort()
        if len(dts) != len(pts):
            dts = []
        primary = TrackInfo(
            track_id=chosen_info.track_id,
            handler_type="vide",
            timescale=MICROSECOND_TIMESCALE,
            duration_ticks=(pts[-1] - pts[0]) if len(pts) > 1 else 0,
            sample_count=len(pts),
            width=chosen_info.width,
            height=chosen_info.height,
        )
        timestamps = TrackTimestamps(
            track=primary,
            pts_ticks=pts,
            dts_ticks=dts,
            stts_deltas=[b - a for a, b in zip(pts, pts[1:])],
        )
        format_info = info.get("format", {})
        duration = float(format_info.get("duration") or 0.0)
        movie_ticks = round(duration * MICROSECOND_TIMESCALE)
        declared = self._rational(
            str(chosen_stream.get("avg_frame_rate") or chosen_stream.get("r_frame_rate") or "")
        )
        if declared is None and duration > 0:
            declared = len(pts) / duration
        replaced = [
            primary if track.track_id == chosen_info.track_id else track for track in tracks
        ]
        return ProbeResult(
            path=source,
            file_size=source.stat().st_size,
            format="mp4",
            brand=None,
            movie_timescale=MICROSECOND_TIMESCALE,
            movie_duration_ticks=movie_ticks,
            tracks=replaced,
            primary_track=primary,
            primary_timestamps=timestamps,
            declared_fps=declared,
            moov_at_end=False,
            adapter=self.name,
        )
