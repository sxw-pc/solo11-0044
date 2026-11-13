from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path

from fpsprobe.server.app import create_server
from fpsprobe.store.ledger import Ledger


def _seed(database: Path, entry_factory) -> str:
    with Ledger(database) as ledger:
        run_id = ledger.create_run(
            "server-fingerprint",
            "fixtures/out",
            run_id="run-server-01",
            started_at="2026-01-01T00:00:00+00:00",
        )
        for name, verdict, classification, sha in (
            ("cfr_30.mp4", "CFR_OK", "ok", "a" * 64),
            ("cfr_2997.mp4", "CFR_MISMATCH", "review", "b" * 64),
            ("vfr_jitter.mp4", "VFR_SUSPECT", "review", "c" * 64),
            ("broken_order.mp4", "TIMESTAMP_BROKEN", "reject", "d" * 64),
        ):
            file_id = ledger.upsert_file(run_id, name, sha, 100)
            ledger.record_verdict(
                run_id,
                file_id,
                entry_factory(name, verdict=verdict, classification=classification),
            )
        ledger.finish_run(
            run_id,
            {
                "total_files": 4,
                "ok": 1,
                "review": 2,
                "reject": 1,
                "error": 0,
                "verdicts": {
                    "CFR_OK": 1,
                    "CFR_MISMATCH": 1,
                    "VFR_SUSPECT": 1,
                    "TIMESTAMP_BROKEN": 1,
                },
                "source_dir": "fixtures/out",
            },
        )
    return run_id


def _request(port: int, path: str, method: str = "GET") -> tuple[int, str]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(method, path)
        response = connection.getresponse()
        return response.status, response.read().decode("utf-8")
    finally:
        connection.close()


def test_read_only_api_and_path_traversal(tmp_path: Path, entry_factory):
    database = tmp_path / "server.db"
    run_id = _seed(database, entry_factory)
    server = create_server("127.0.0.1", 0, database)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    thread.start()
    try:
        status, body = _request(port, "/health")
        assert status == 200
        assert json.loads(body) == {"status": "ok"}

        status, body = _request(port, "/api/runs")
        assert status == 200
        assert json.loads(body)["runs"][0]["run_id"] == run_id

        status, body = _request(port, f"/api/runs/{run_id}")
        assert status == 200
        assert json.loads(body)["summary"]["total_files"] == 4

        status, body = _request(port, f"/api/runs/{run_id}/files?verdict=review")
        assert status == 200
        assert json.loads(body)["count"] == 2

        status, body = _request(port, f"/api/runs/{run_id}/files")
        assert status == 200
        assert json.loads(body)["count"] == 4

        status, body = _request(port, f"/api/report/{run_id}.json")
        assert status == 200
        assert json.loads(body)["summary"]["reject"] == 1

        status, body = _request(port, "/api/runs/does-not-exist")
        assert status == 404
        assert "root:" not in body

        for attack in (
            "/api/runs/../../etc/passwd",
            "/api/runs/%2e%2e%2f%2e%2e%2fetc%2fpasswd",
            "/api/report/..%2f..%2fetc%2fpasswd.json",
            "/api/runs/%00/files",
        ):
            status, body = _request(port, attack)
            assert status in {400, 404}, attack
            assert "root:" not in body
            assert "etc/passwd" not in body

        status, _body = _request(port, f"/api/runs/{run_id}/files?verdict=nonsense")
        assert status == 400

        status, _body = _request(port, "/health", method="POST")
        assert status == 405
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
