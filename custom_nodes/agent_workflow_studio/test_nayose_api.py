"""Offline contract tests for the Nayose custom-node API client."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

from .nayose_api import NayoseAPI


class Handler(BaseHTTPRequestHandler):
    requests_seen: ClassVar[
        list[tuple[str, str, dict[str, str], dict[str, object] | None]]
    ] = []
    status_reads = 0

    def log_message(self, _format: str, *_args: object) -> None:
        pass

    def _respond(self, status: int, value: dict[str, object]) -> None:
        body = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        body = json.loads(raw) if raw else None
        self.requests_seen.append(("POST", self.path, dict(self.headers), body))
        self._respond(
            202,
            {
                "id": "job-1",
                "stage": "queued",
                "result": None,
                "error": None,
            },
        )

    def do_GET(self) -> None:
        self.requests_seen.append(("GET", self.path, dict(self.headers), None))
        self.status_reads += 1
        self._respond(
            200,
            {
                "id": "job-1",
                "stage": "complete",
                "result": {"generation": "gen_test", "fingerprint": "abc"},
                "error": None,
            },
        )


def test_admin_job_uses_bearer_auth_polls_and_returns_artifact():
    Handler.requests_seen = []
    Handler.status_reads = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = NayoseAPI(
            base_url=f"http://127.0.0.1:{server.server_port}",
            token="server-secret",
            request_timeout=1,
            poll_interval=0,
            job_timeout=1,
        )
        result = client.run_admin_job("generate", {"profile": "smoke", "seed": 42})
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert result == {"generation": "gen_test", "fingerprint": "abc"}
    method, path, headers, body = Handler.requests_seen[0]
    assert method == "POST"
    assert path == "/api/admin/jobs"
    assert headers["Authorization"] == "Bearer server-secret"
    assert body == {"action": "generate", "params": {"profile": "smoke", "seed": 42}}
    assert sum(request[0] == "GET" for request in Handler.requests_seen) == 1


def test_only_generation_and_training_jobs_are_allowed():
    client = NayoseAPI("http://localhost", "token")
    try:
        client.run_admin_job("promote", {"run_id": "run_1"})
    except ValueError as exc:
        assert "unsupported" in str(exc)
    else:
        raise AssertionError("unsupported action should fail before any API request")
