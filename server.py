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
import subprocess
import sys
import threading
from pathlib import Path

PORT = int(os.environ.get("OPENSWARM_PORT", 7700))
BASE_DIR = Path(__file__).parent.resolve()
SETTINGS_FILE = BASE_DIR / "settings.json"

# Cache for model list — populated once at startup and reused.
_models_cache: list = []
_models_ready = threading.Event()


# ── API handlers ──────────────────────────────────────────────────────────────


def load_models_cache():
    """Run `opencode models` once at startup and cache the result."""
    global _models_cache
    try:
        result = subprocess.run(
            ["opencode", "models"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        models = [
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip() and not line.startswith("#")
        ]
        _models_cache = models
        print(f"models cached: {len(_models_cache)} entries", flush=True)
    except Exception as e:
        print(f"WARNING: could not load model list: {e}", flush=True)
    finally:
        _models_ready.set()


def api_get_models() -> tuple[int, list]:
    # Wait up to 20s for the background cache to be ready
    _models_ready.wait(timeout=20)
    return 200, _models_cache


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
    threading.Thread(target=load_models_cache, daemon=True).start()
    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"openswarm dashboard → http://0.0.0.0:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down", flush=True)
        sys.exit(0)
