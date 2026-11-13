"""Read-only localhost HTTP service."""

from .app import create_server, serve

__all__ = ["create_server", "serve"]
