"""
openswarm dashboard server
Serves static files from the openswarm directory and provides a small JSON API:

  GET  /api/models    — list all available opencode models
  GET  /api/settings  — read settings.json (returns {} if not found)
  POST /api/settings  — write settings.json (body must be JSON)
"""

import http.server
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

PORT = int(os.environ.get("OPENSWARM_PORT", 7700))
OPENCODE_API = os.environ.get("OPENCODE_API", "http://localhost:4097")
BASE_DIR = Path(__file__).parent.resolve()
SETTINGS_FILE = BASE_DIR / "settings.json"


# ── API handlers ──────────────────────────────────────────────────────────────


def api_get_models() -> tuple[int, list]:
    """Fetch models from the opencode server API (/provider) and return only
    those belonging to connected (authenticated) providers."""
    try:
        url = f"{OPENCODE_API}/provider"
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
        connected = set(data.get("connected", []))
        models = []
        for p in data.get("all", []):
            if p.get("id") not in connected:
                continue
            for m in p.get("models", []):
                mid = m.get("id") if isinstance(m, dict) else str(m)
                if mid:
                    models.append(f"{p['id']}/{mid}")
        return 200, models
    except Exception as e:
        print(f"WARNING: could not fetch models from opencode API: {e}", flush=True)
        return 200, []


def api_get_settings() -> tuple[int, dict]:
    if SETTINGS_FILE.exists():
        try:
            return 200, json.loads(SETTINGS_FILE.read_text())
        except Exception:
            pass
    return 200, {}


def api_post_settings(body: bytes) -> tuple[int, dict]:
    try:
        data = json.loads(body)
        SETTINGS_FILE.write_text(json.dumps(data, indent=2))
        return 200, {"ok": True}
    except Exception as e:
        return 400, {"error": str(e)}


# ── Request handler ────────────────────────────────────────────────────────────


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def log_message(self, fmt, *args):
        # Suppress per-request noise; keep errors
        if args and len(args) >= 2 and str(args[1]).startswith(("4", "5")):
            super().log_message(fmt, *args)

    def _send_json(self, status: int, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if self.path == "/api/models":
            status, data = api_get_models()
            self._send_json(status, data)
        elif self.path == "/api/settings":
            status, data = api_get_settings()
            self._send_json(status, data)
        else:
            super().do_GET()

    def do_POST(self):
        if self.path == "/api/settings":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            status, data = api_post_settings(body)
            self._send_json(status, data)
        else:
            self.send_error(404)


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"openswarm dashboard → http://0.0.0.0:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down", flush=True)
        sys.exit(0)
