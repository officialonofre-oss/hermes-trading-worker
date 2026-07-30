"""Small read-only HTTP server exposing bot state for remote debugging.
Runs in a background thread alongside the trading loop; never writes to
state/, only reads it. Stdlib only -- no new dependency for something this
small. Optional DEBUG_TOKEN env var gates everything except /health."""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from hermes_trading import dashboard

STATE_DIR = Path(__file__).parent.parent / "state"

# Whitelisted by name on purpose -- never accept an arbitrary filesystem
# path from the request, so there's no path-traversal surface.
JSONL_FILES = {
    "trades": STATE_DIR / "trades.jsonl",
    "hypotheses": STATE_DIR / "hypotheses.jsonl",
    "backtests": STATE_DIR / "backtests.jsonl",
}
JSON_FILES = {
    "heartbeat": STATE_DIR / "heartbeat.json",
}


def _authorized(handler) -> bool:
    token = os.getenv("DEBUG_TOKEN")
    if not token:
        return True  # no token configured -- endpoint is open, by choice
    auth = handler.headers.get("Authorization", "")
    if auth == f"Bearer {token}":
        return True
    query = parse_qs(urlparse(handler.path).query)
    return query.get("token", [None])[0] == token


class DebugHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # keep the worker's own logs from filling with HTTP access noise

    def _send(self, status: int, body: bytes, content_type="text/plain"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/health":
            self._send(200, b"ok")
            return

        if not _authorized(self):
            self._send(401, b"unauthorized")
            return

        if path == "/dashboard":
            html = dashboard.render(dashboard.build_data())
            self._send(200, html.encode(), "text/html")
            return

        if path.startswith("/state/"):
            name = path.removeprefix("/state/")
            if name in JSONL_FILES:
                file_path = JSONL_FILES[name]
                if not file_path.exists():
                    self._send(200, b"[]", "application/json")
                    return
                lines = [json.loads(line) for line in file_path.read_text().splitlines() if line.strip()]
                self._send(200, json.dumps(lines).encode(), "application/json")
                return
            if name in JSON_FILES:
                file_path = JSON_FILES[name]
                body = file_path.read_bytes() if file_path.exists() else b"{}"
                self._send(200, body, "application/json")
                return
            self._send(404, b"not found")
            return

        self._send(404, b"not found")


def start_background(port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("0.0.0.0", port), DebugHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
