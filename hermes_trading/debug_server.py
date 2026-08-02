"""Small read-only HTTP server exposing bot state for remote debugging.
Runs in a background thread alongside the trading loop; never writes to
state/, only reads it. Stdlib only -- no new dependency for something this
small. Optional DEBUG_TOKEN env var gates everything except /health."""
import hmac
import json
import os
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from hermes_trading import dashboard

STATE_DIR = Path(__file__).parent.parent / "state"

COOKIE_NAME = "hermes_debug"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # a year -- this is a convenience gate, not a bank

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


def _matches(candidate, token: str) -> bool:
    """Constant-time compare so response timing doesn't leak how many
    leading characters of the token were correct."""
    if not isinstance(candidate, str):
        return False
    return hmac.compare_digest(candidate, token)


def _cookie_token(handler):
    raw = handler.headers.get("Cookie")
    if not raw:
        return None
    jar = SimpleCookie()
    try:
        jar.load(raw)
    except Exception:
        return None
    morsel = jar.get(COOKIE_NAME)
    return morsel.value if morsel else None


def _authorized(handler):
    """Returns (authorized, via) where `via` is "open" | "header" | "cookie"
    | "query". "query" is authorized but the caller should redirect to strip
    the token out of the URL -- see do_GET."""
    token = os.getenv("DEBUG_TOKEN")
    if not token:
        return True, "open"  # no token configured -- endpoint is open, by choice

    auth = handler.headers.get("Authorization", "")
    if auth.startswith("Bearer ") and _matches(auth[len("Bearer "):], token):
        return True, "header"

    if _matches(_cookie_token(handler), token):
        return True, "cookie"

    query = parse_qs(urlparse(handler.path).query)
    if _matches(query.get("token", [None])[0], token):
        return True, "query"

    return False, None


class DebugHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # keep the worker's own logs from filling with HTTP access noise

    def _send(self, status: int, body: bytes, content_type="text/plain"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_cookie_redirect(self, path: str, token: str):
        """Set the token as an HttpOnly cookie, then bounce to the same path
        with the query string stripped. This is what makes phone-browser use
        both convenient and safe: you paste ?token=... exactly once, the URL
        in the address bar (and in history from here on) is clean, and every
        later visit authenticates from the cookie with no token in the URL."""
        # Render terminates TLS and forwards the original scheme; only mark the
        # cookie Secure when the request really came in over HTTPS, so local
        # http testing still works.
        https = self.headers.get("X-Forwarded-Proto", "").lower() == "https"
        cookie = (
            f"{COOKIE_NAME}={token}; Path=/; Max-Age={COOKIE_MAX_AGE}; "
            f"HttpOnly; SameSite=Lax"
        )
        if https:
            cookie += "; Secure"

        self.send_response(302)
        self.send_header("Location", path)
        self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/health":
            self._send(200, b"ok")
            return

        authorized, via = _authorized(self)
        if not authorized:
            self._send(401, b"unauthorized")
            return

        if via == "query":
            self._send_cookie_redirect(path, os.environ["DEBUG_TOKEN"])
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
