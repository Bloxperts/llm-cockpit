"""Shared fixtures for the cockpit test suite.

The `fake_ollama` fixture spins up a tiny stdlib HTTP server on a random local
port that mimics the two Ollama endpoints we need for UC-08 Slice A:
`GET /api/tags` and `GET /api/ps`. We deliberately avoid pulling a third-party
HTTP test framework — stdlib is enough at this scale and stays dependency-free.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


@dataclass
class FakeOllamaState:
    """Mutable state the fixture returns so tests can adjust the response."""
    models: list[dict] = field(default_factory=list)
    loaded: list[dict] = field(default_factory=list)
    url: str = ""


def _make_handler(state: FakeOllamaState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/api/tags":
                payload = {"models": state.models}
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/api/ps":
                payload = {"models": state.loaded}
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, *args, **kwargs) -> None:  # quiet
            pass

    return Handler


@pytest.fixture
def fake_ollama() -> Iterator[FakeOllamaState]:
    """Run a fake Ollama on `127.0.0.1:<random>`. Yields a state object whose
    `.url` is the base URL (no trailing slash) and whose `.models` / `.loaded`
    can be mutated before the cockpit calls in.
    """
    state = FakeOllamaState(
        models=[
            {
                "name": "gemma3:27b",
                "modified_at": "2026-04-01T00:00:00Z",
                "size": 18000000000,
                "digest": "sha256:gemma3",
            },
            {
                "name": "qwen3-coder:30b",
                "modified_at": "2026-04-02T00:00:00Z",
                "size": 22000000000,
                "digest": "sha256:qwen3coder",
            },
        ]
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(state))
    state.url = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    """Pre-created temp dir for `cockpit-admin --data-dir`."""
    d = tmp_path / "cockpit-data"
    d.mkdir()
    return d
