"""Container readers. The built-in metadata-only MP4 reader is the default."""

from .base import select_primary_track
from .mp4probe import probe_mp4

__all__ = ["probe_mp4", "select_primary_track"]
