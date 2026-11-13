"""Threaded localhost-only HTTP server using the standard library."""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .. import __version__
from ..errors import InputError
from ..store.ledger import Ledger
from .routes import dispatch

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _json_bytes(payload: Any) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def make_handler(db_path: str | Path) -> type[BaseHTTPRequestHandler]:
    database = str(db_path)

    class Handler(BaseHTTPRequestHandler):
        server_version = f"fpsprobe/{__version__}"

        def _send(self, status: int, payload: Any) -> None:
            body = _json_bytes(payload)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
            try:
                status, payload = dispatch(database, self.path)
            except Exception:
                status, payload = 500, {
                    "error": {"code": "INTERNAL_ERROR", "message": "internal server error"}
                }
            self._send(status, payload)

        def _method_not_allowed(self) -> None:
            self._send(
                HTTPStatus.METHOD_NOT_ALLOWED,
                {"error": {"code": "METHOD_NOT_ALLOWED", "message": "read-only service"}},
            )

        do_POST = _method_not_allowed  # noqa: N815
        do_PUT = _method_not_allowed  # noqa: N815
        do_DELETE = _method_not_allowed  # noqa: N815
        do_PATCH = _method_not_allowed  # noqa: N815

        def log_message(self, format: str, *args: Any) -> None:
            return

    return Handler


def create_server(
    host: str = "127.0.0.1",
    port: int = 5180,
    db_path: str | Path = "workdir/fpsprobe.db",
) -> ThreadingHTTPServer:
    """Bind a server; non-loopback hosts are rejected rather than silently exposed."""
    if host not in LOOPBACK_HOSTS:
        raise InputError(
            "server host must be a loopback address (127.0.0.1, localhost, or ::1)",
            code="NON_LOOPBACK_BIND",
        )
    with Ledger(db_path):
        pass
    server = ThreadingHTTPServer((host, port), make_handler(db_path))
    server.daemon_threads = True
    return server


def serve(
    host: str = "127.0.0.1",
    port: int = 5180,
    db_path: str | Path = "workdir/fpsprobe.db",
) -> None:
    server = create_server(host, port, db_path)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
