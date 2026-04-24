"""
openswarm dashboard server
Serves static files from the openswarm directory and provides a small JSON API:

  GET  /api/models    — list all available opencode models
  GET  /api/settings  — read settings.json (returns {} if not found)
  POST /api/settings  — write settings.json (body must be JSON)

  POST /api/orchestrator/run    — trigger an immediate poll cycle (manual mode)
  POST /api/orchestrator/status — receive a status summary from an orchestrator session

  GET  /api/hermes/sessions     — alias for /api/projects (dashboard compat)
  POST /api/hermes/sessions     — alias for POST /api/projects

  GET  /api/oc/sessions
       — list opencode sessions from the local DB, ordered by time_updated desc.
         Optional query param: ?dirs=<comma-separated-paths> to filter by directory.

  GET  /api/oc/session/<sessionId>/messages
       — messages + parts for a session, assembled from the local DB.
"""

import http.server
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PORT = int(os.environ.get("OPENSWARM_PORT", 7700))
OPENCODE_API = os.environ.get("OPENCODE_API", "http://localhost:4097")
BASE_DIR = Path(__file__).parent.resolve()
NEW_DASHBOARD = (
    "--new-dashboard" in sys.argv
)  # serve dashboard/build/ instead of dashboard.html
SETTINGS_FILE = BASE_DIR / "settings.json"
OPENCODE_DB = Path(
    os.environ.get(
        "OPENCODE_DB",
        Path.home() / ".local/share/opencode/opencode.db",
    )
)
POLL_TASKS_PY = BASE_DIR / "poll_tasks.py"
SESSIONS_FILE = BASE_DIR / "sessions.json"  # legacy name; kept for migration check
PROJECTS_FILE = BASE_DIR / "projects.json"
CARDS_FILE = BASE_DIR / "cards.json"
ORCH_SESSION_FILE = (
    BASE_DIR / "orch-session.id"
)  # stores the dedicated headless orch session ID

# Last status summary posted by an orchestrator session (in-memory; reset on restart)
_last_orch_status: str = ""


# ── opencode REST API helpers ─────────────────────────────────────────────────

import http.client as _http_client


def _oc_api(
    method: str, path: str, body=None, timeout: int = 30, directory: str | None = None
) -> tuple[int, object]:
    """Call the opencode REST API at localhost:4097 and return (status, data)."""
    try:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if data else {}
        if directory:
            headers["x-opencode-directory"] = directory
        conn = _http_client.HTTPConnection("localhost", 4097, timeout=timeout)
        conn.request(method, path, body=data, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        return resp.status, json.loads(raw) if raw else {}
    except Exception as e:
        return 502, {"error": str(e)}


def _oc_proxy(
    method: str,
    oc_path: str,
    handler,
    body: bytes | None = None,
    timeout: int | None = 60,
) -> None:
    """Transparently proxy a request to opencode at localhost:4097.

    Streams the response (headers + body) back through *handler* with no
    JSON parsing or transformation — byte-for-byte passthrough.
    """
    try:
        req_headers: dict[str, str] = {}
        ct = handler.headers.get("Content-Type", "")
        if ct:
            req_headers["Content-Type"] = ct

        conn = _http_client.HTTPConnection("localhost", 4097, timeout=timeout)
        conn.request(method, oc_path, body=body or b"", headers=req_headers)
        resp = conn.getresponse()

        # Forward status + minimal headers
        handler.send_response(resp.status)
        for hdr in ("Content-Type", "Cache-Control", "Connection"):
            val = resp.getheader(hdr)
            if val:
                handler.send_header(hdr, val)
        handler.send_header("Access-Control-Allow-Origin", "*")
        # Only send Content-Length if present and response has a body
        cl = resp.getheader("Content-Length")
        if cl:
            handler.send_header("Content-Length", cl)
        # SSE: tell nginx not to buffer
        if resp.getheader("Content-Type", "").startswith("text/event-stream"):
            handler.send_header("X-Accel-Buffering", "no")
        handler.end_headers()

        # Stream body back
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            handler.wfile.write(chunk)
            handler.wfile.flush()
        conn.close()
    except Exception as e:
        try:
            handler._send_json(502, {"error": str(e)})
        except Exception:
            pass


# ── opencode DB helpers ───────────────────────────────────────────────────────


def _db_connect():
    """Open a read-only connection to the opencode SQLite DB."""
    uri = f"file:{OPENCODE_DB}?mode=ro"
    return sqlite3.connect(uri, uri=True, check_same_thread=False)


def _db_connect_rw():
    """Open a read-write connection to the opencode SQLite DB."""
    return sqlite3.connect(str(OPENCODE_DB), check_same_thread=False)


# ── Projects (per-directory orchestrator sessions) ────────────────────────────


def _projects_load() -> list[dict]:
    """Load projects.json; migrate from sessions.json if needed."""
    if not PROJECTS_FILE.exists() and SESSIONS_FILE.exists():
        # One-time migration: copy sessions.json → projects.json
        try:
            import shutil

            shutil.copy2(SESSIONS_FILE, PROJECTS_FILE)
        except Exception as e:
            print(f"WARNING: migration sessions→projects failed: {e}", flush=True)
    if PROJECTS_FILE.exists():
        try:
            return json.loads(PROJECTS_FILE.read_text())
        except Exception:
            pass
    return []


def _projects_save(projects: list[dict]) -> None:
    tmp = PROJECTS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(projects, indent=2))
    tmp.replace(PROJECTS_FILE)


def api_get_sessions() -> tuple[int, object]:
    return 200, _projects_load()


def api_post_sessions(body: bytes) -> tuple[int, object]:
    """Create a new orchestrator project entry for a given directory."""
    import uuid
    from datetime import datetime, timezone

    try:
        data = json.loads(body)
    except Exception:
        return 400, {"error": "invalid JSON"}
    directory = (data.get("dir") or "").strip()
    if not directory:
        return 400, {"error": "dir required"}
    directory = str(Path(directory).resolve())
    if not Path(directory).is_dir():
        return 400, {"error": f"not a directory: {directory}"}
    label = data.get("label") or Path(directory).name

    sessions = _projects_load()
    # Deduplicate by directory
    if any(s["dir"] == directory for s in sessions):
        return 409, {"error": "session already exists for this directory"}

    session_id = uuid.uuid4().hex[:8]
    session: dict = {
        "id": session_id,
        "dir": directory,
        "label": label,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    sessions.append(session)
    _projects_save(sessions)
    return 200, session


def api_delete_session(session_id: str) -> tuple[int, object]:
    sessions = _projects_load()
    target = next((s for s in sessions if s["id"] == session_id), None)
    if not target:
        return 404, {"error": "session not found"}
    sessions = [s for s in sessions if s["id"] != session_id]
    _projects_save(sessions)
    return 200, {"ok": True}


def api_session_run(session_id: str) -> tuple[int, object]:
    """Spawn an opencode orchestrator session in the session's directory."""
    sessions = _projects_load()
    target = next((s for s in sessions if s["id"] == session_id), None)
    if not target:
        return 404, {"error": "session not found"}
    directory = target["dir"]
    try:
        settings_data: dict = {}
        if SETTINGS_FILE.exists():
            try:
                settings_data = json.loads(SETTINGS_FILE.read_text())
            except Exception:
                pass
        model = settings_data.get("model") or "github-copilot/claude-sonnet-4.6"
        logs_dir = BASE_DIR / "logs"
        logs_dir.mkdir(exist_ok=True)
        log_handle = open(logs_dir / f"session-{session_id}.log", "w")
        oc_workspace = Path(directory) / ".orch-session"
        oc_workspace.mkdir(exist_ok=True)
        subprocess.Popen(
            [
                "opencode",
                "run",
                "--dir",
                str(oc_workspace),
                "--model",
                model,
                "--agent",
                "orchestrator",
            ],
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        return 200, {"ok": True, "oc_workspace": str(oc_workspace)}
    except Exception as e:
        return 502, {"error": str(e)}


# ── OC session resolution ──────────────────────────────────────────────────────


def _oc_session_for_dir(directory: str) -> str | None:
    """Return the newest OC session ID for *directory*, or None."""
    path = "/session?directory=" + urllib.parse.quote(directory, safe="")
    status, data = _oc_api("GET", path, timeout=5)
    if status != 200 or not isinstance(data, list) or not data:
        return None
    # data is ordered by time_updated desc from the server
    return data[0].get("id")


# ── Tasks (per-project task queue) ────────────────────────────────────────────


def _tasks_file_for_project(project_id: str) -> Path | None:
    """Return the tasks.json Path for a project, or None if project not found."""
    projects = _projects_load()
    p = next((proj for proj in projects if proj["id"] == project_id), None)
    if not p:
        return None
    return Path(p["dir"]) / ".openswarm" / "tasks" / "tasks.json"


def api_get_tasks(project_id: str) -> tuple[int, object]:
    tf = _tasks_file_for_project(project_id)
    if tf is None:
        return 404, {"error": f"project {project_id} not found"}
    if not tf.exists():
        return 200, {"tasks": []}
    try:
        return 200, json.loads(tf.read_text())
    except Exception as e:
        return 502, {"error": str(e)}


def api_post_tasks(project_id: str, body: bytes) -> tuple[int, object]:
    tf = _tasks_file_for_project(project_id)
    if tf is None:
        return 404, {"error": f"project {project_id} not found"}
    try:
        data = json.loads(body)
    except Exception:
        return 400, {"error": "invalid JSON"}
    tf.parent.mkdir(parents=True, exist_ok=True)
    tmp = tf.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(tf)
    return 200, {"ok": True}


# ── Cards ──────────────────────────────────────────────────────────────────────


def _cards_load() -> dict:
    if CARDS_FILE.exists():
        try:
            return json.loads(CARDS_FILE.read_text())
        except Exception:
            pass
    return {"active": [], "history": []}


def _cards_save(cards: dict) -> None:
    tmp = CARDS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cards, indent=2))
    tmp.replace(CARDS_FILE)


def _oc_session_exists(session_id: str) -> bool:
    """Return True if *session_id* is still live in the opencode server."""
    status, data = _oc_api(
        "GET",
        f"/session/{urllib.parse.quote(session_id, safe='')}",
        timeout=5,
    )
    return status == 200 and isinstance(data, dict) and bool(data.get("id"))


def api_get_cards() -> tuple[int, object]:
    cards = _cards_load()
    dirty = False
    # Augment each active card with its resolved OC session ID.
    # Once found, pin the session_id into the card so it survives directory
    # collisions and multiple sessions accumulating in the same directory.
    for card in cards.get("active", []):
        pinned = card.get("session_id")
        if pinned:
            # Verify the pinned session is still alive; only re-query if not.
            if _oc_session_exists(pinned):
                continue  # already has a valid pinned ID — nothing to do
            # Pinned session gone — fall through to re-query
            card.pop("session_id", None)
            dirty = True
        # No pinned session yet (or it disappeared) — query by directory
        resolved = _oc_session_for_dir(card["dir"])
        if resolved:
            card["session_id"] = resolved
            dirty = True
    if dirty:
        _cards_save(cards)
    return 200, cards


def api_post_cards(body: bytes) -> tuple[int, object]:
    """Register a new card (manual spawn or orchestrator spawn)."""
    import uuid
    from datetime import datetime, timezone

    try:
        data = json.loads(body)
    except Exception:
        return 400, {"error": "invalid JSON"}
    label = (data.get("label") or "").strip()
    directory = (data.get("dir") or "").strip()
    if not label or not directory:
        return 400, {"error": "label and dir required"}
    directory = str(Path(directory).resolve())

    cards = _cards_load()
    # Deduplicate by dir in active cards
    if any(c["dir"] == directory for c in cards.get("active", [])):
        return 409, {"error": "card already exists for this directory"}

    card: dict = {
        "id": uuid.uuid4().hex[:8],
        "label": label,
        "dir": directory,
        "worktree": data.get("worktree") or None,
        "project_id": data.get("project_id") or None,
        "task_id": data.get("task_id") or None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    # Optionally spawn an OC session if prompt is provided
    prompt = (data.get("prompt") or "").strip()
    model = (data.get("model") or "").strip()
    title = label
    if prompt:
        try:
            Path(directory).mkdir(parents=True, exist_ok=True)
            cmd = [
                "opencode",
                "run",
                "--dir",
                directory,
                "--title",
                title,
                "-c",
                prompt,
            ]
            if model:
                cmd += ["-m", model]
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            print(
                f"WARNING: opencode run failed for card {card['id']}: {e}", flush=True
            )
    else:
        # Spawn without initial prompt (user will type in the card)
        try:
            Path(directory).mkdir(parents=True, exist_ok=True)
            cmd = ["opencode", "run", "--dir", directory, "--title", title]
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            print(
                f"WARNING: opencode run failed for card {card['id']}: {e}", flush=True
            )

    cards.setdefault("active", []).append(card)
    _cards_save(cards)
    return 200, card


def api_delete_card(card_id: str) -> tuple[int, object]:
    """Move a card from active to history."""
    from datetime import datetime, timezone

    cards = _cards_load()
    active = cards.get("active", [])
    target = next((c for c in active if c["id"] == card_id), None)
    if not target:
        return 404, {"error": "card not found"}
    active.remove(target)
    target["closed_at"] = datetime.now(timezone.utc).isoformat()
    cards.setdefault("history", []).insert(0, target)
    _cards_save(cards)
    return 200, {"ok": True}


def api_restore_card(card_id: str) -> tuple[int, object]:
    """Move a card from history back to active."""
    cards = _cards_load()
    history = cards.get("history", [])
    target = next((c for c in history if c["id"] == card_id), None)
    if not target:
        return 404, {"error": "card not found in history"}
    history.remove(target)
    target.pop("closed_at", None)
    cards.setdefault("active", []).append(target)
    _cards_save(cards)
    return 200, target


def api_fs_dirs(query: str) -> tuple[int, object]:
    """Return up to 20 directory paths that start with *query* (for autocomplete)."""
    import fnmatch

    try:
        q = query.strip()
        if not q:
            q = "/home/ubuntu/server"
        base = Path(q)
        # If q ends with / or is an existing dir, list its children
        if q.endswith("/") or base.is_dir():
            parent = base
            prefix = ""
        else:
            parent = base.parent
            prefix = base.name
        if not parent.is_dir():
            return 200, []
        results = []
        for child in sorted(parent.iterdir()):
            if not child.is_dir():
                continue
            if child.name.startswith("."):
                continue
            if prefix and not child.name.startswith(prefix):
                continue
            results.append(str(child))
            if len(results) >= 20:
                break
        return 200, results
    except Exception as e:
        return 200, []


def api_fs_worktrees(directory: str) -> tuple[int, object]:
    """Return git worktrees for a directory as [{path, branch, head}]."""
    if not directory:
        return 200, []
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=directory,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return 200, []
        worktrees = []
        current: dict = {}
        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                if current.get("path"):
                    worktrees.append(current)
                current = {"path": line[9:], "branch": None, "head": None}
            elif line.startswith("HEAD "):
                current["head"] = line[5:]
            elif line.startswith("branch "):
                current["branch"] = line[7:].replace("refs/heads/", "")
        if current.get("path"):
            worktrees.append(current)
        return 200, worktrees
    except Exception:
        return 200, []


def api_orchestrator_run() -> tuple[int, object]:
    try:
        settings_data: dict = {}
        if SETTINGS_FILE.exists():
            try:
                settings_data = json.loads(SETTINGS_FILE.read_text())
            except Exception:
                pass
        model = settings_data.get("model") or "github-copilot/claude-sonnet-4.6"
        logs_dir = BASE_DIR / "logs"
        logs_dir.mkdir(exist_ok=True)
        log_handle = open(logs_dir / "orchestrator-session.log", "w")
        prompt = (
            "You are the openswarm orchestrator. "
            "Run one poll cycle by executing:\n"
            "  python3 /home/ubuntu/server/openswarm/poll_tasks.py --force\n"
            "Report what happened, then stand by for further instructions."
        )
        orch_workspace = BASE_DIR / "orch-workspace"
        orch_workspace.mkdir(exist_ok=True)
        subprocess.Popen(
            ["opencode", "run", "--dir", str(orch_workspace), "--model", model, prompt],
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        return 200, {"ok": True}
    except Exception as e:
        return 502, {"error": str(e)}


def api_orchestrator_status(body: bytes) -> tuple[int, object]:
    """Receive a status summary posted by an orchestrator session after each poll cycle."""
    global _last_orch_status
    try:
        data = json.loads(body)
        _last_orch_status = data.get("summary", "")
        return 200, {"ok": True}
    except Exception as e:
        return 400, {"error": str(e)}


def api_stream_session(session_id: str, wfile) -> None:
    """SSE: poll the DB every 400 ms; emit an 'updated' event when new parts appear."""
    POLL_INTERVAL = 0.4
    HEARTBEAT_INTERVAL = 15.0
    MAX_IDLE = 300.0  # close stream after 5 min of no new parts

    last_max_time: int | None = None
    last_heartbeat = time.time()
    last_activity = time.time()

    def write(data: bytes) -> bool:
        try:
            wfile.write(data)
            wfile.flush()
            return True
        except OSError:
            return False

    try:
        while True:
            time.sleep(POLL_INTERVAL)
            now = time.time()

            if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                if not write(b": heartbeat\n\n"):
                    return
                last_heartbeat = now

            if now - last_activity >= MAX_IDLE:
                write(b'data: {"type":"timeout"}\n\n')
                return

            if not OPENCODE_DB.exists():
                continue
            try:
                with _db_connect() as con:
                    row = con.execute(
                        "SELECT MAX(time_created) FROM part WHERE session_id = ?",
                        (session_id,),
                    ).fetchone()
                max_time = row[0] if row else None
            except Exception:
                continue

            if max_time is None:
                continue

            if last_max_time is None:
                last_max_time = max_time
                continue  # baseline set — don't fire on first poll

            if max_time > last_max_time:
                last_max_time = max_time
                last_activity = now
                if not write(b'data: {"type":"updated"}\n\n'):
                    return
    except Exception:
        pass


def api_stream_oc_events(session_id: str, wfile) -> None:
    """SSE: transparent byte-pipe of /global/event from opencode. No parsing."""
    import http.client as _hc

    conn: _hc.HTTPConnection | None = None
    try:
        conn = _hc.HTTPConnection("localhost", 4097, timeout=600)
        conn.request(
            "GET",
            "/global/event",
            headers={"Accept": "text/event-stream", "Cache-Control": "no-cache"},
        )
        resp = conn.getresponse()
        while True:
            chunk = resp.read(4096)
            if not chunk:
                break
            try:
                wfile.write(chunk)
                wfile.flush()
            except OSError:
                break
    except Exception:
        pass
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def api_stream_log(relpath: str, wfile) -> None:
    """SSE: emit 'updated' whenever a log file's mtime or size changes."""
    POLL_INTERVAL = 0.5
    HEARTBEAT_INTERVAL = 15.0

    filepath = (BASE_DIR / relpath).resolve()
    # Safety: must stay inside BASE_DIR/logs/
    if not str(filepath).startswith(str(BASE_DIR / "logs")):
        return

    last_mtime: float | None = None
    last_size: int | None = None
    last_heartbeat = time.time()

    def write(data: bytes) -> bool:
        try:
            wfile.write(data)
            wfile.flush()
            return True
        except OSError:
            return False

    try:
        while True:
            time.sleep(POLL_INTERVAL)
            now = time.time()

            if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                if not write(b": heartbeat\n\n"):
                    return
                last_heartbeat = now

            try:
                st = filepath.stat()
                mtime, size = st.st_mtime, st.st_size
            except FileNotFoundError:
                continue

            if last_mtime is None:
                last_mtime, last_size = mtime, size
                continue  # baseline

            if mtime != last_mtime or size != last_size:
                last_mtime, last_size = mtime, size
                if not write(b'data: {"type":"updated"}\n\n'):
                    return
    except Exception:
        pass


def api_stream_diff(paths: list[str], wfile) -> None:
    """SSE: emit 'updated' whenever any worktree HEAD commit hash changes."""
    POLL_INTERVAL = 2.0
    HEARTBEAT_INTERVAL = 15.0

    def heads() -> dict[str, str]:
        result = {}
        for p in paths:
            try:
                r = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=p,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if r.returncode == 0:
                    result[p] = r.stdout.strip()
            except Exception:
                pass
        return result

    last_heads: dict[str, str] | None = None
    last_heartbeat = time.time()

    def write(data: bytes) -> bool:
        try:
            wfile.write(data)
            wfile.flush()
            return True
        except OSError:
            return False

    try:
        while True:
            time.sleep(POLL_INTERVAL)
            now = time.time()

            if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                if not write(b": heartbeat\n\n"):
                    return
                last_heartbeat = now

            current = heads()
            if last_heads is None:
                last_heads = current
                continue  # baseline

            if current != last_heads:
                last_heads = current
                if not write(b'data: {"type":"updated"}\n\n'):
                    return
    except Exception:
        pass


def api_worktree_diff(paths: list[str]) -> tuple[int, object]:
    """Run `git diff merge-base..HEAD` for each worktree path."""
    results = {}
    for path in paths:
        p = Path(path)
        if not p.is_dir():
            results[path] = {"error": f"not a directory: {path}"}
            continue
        try:
            base = subprocess.run(
                ["git", "merge-base", "HEAD", "origin/main"],
                cwd=path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if base.returncode != 0:
                # fall back to last 10 commits
                merge_base = "HEAD~10"
            else:
                merge_base = base.stdout.strip()
            diff = subprocess.run(
                ["git", "diff", f"{merge_base}..HEAD"],
                cwd=path,
                capture_output=True,
                text=True,
                timeout=15,
            )
            results[path] = {
                "diff": diff.stdout,
                "error": diff.stderr.strip() if diff.returncode != 0 else None,
            }
        except Exception as e:
            results[path] = {"error": str(e)}
    return 200, results


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
        serve_dir = (
            str(BASE_DIR / "dashboard" / "build") if NEW_DASHBOARD else str(BASE_DIR)
        )
        super().__init__(*args, directory=serve_dir, **kwargs)

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
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path == "/api/models":
            status, data = api_get_models()
            self._send_json(status, data)

        elif path == "/api/debug":
            # Diagnostic endpoint — returns state needed for orch panel
            sessions_status, sessions_data = api_get_sessions()
            oc_all_status, oc_all_data = _oc_api("GET", "/session", timeout=5)
            oc_orch_status, oc_orch_data = _oc_api(
                "GET",
                "/session?directory="
                + urllib.parse.quote((BASE_DIR / ".orch-session").as_posix(), safe=""),
                timeout=5,
            )
            self._send_json(
                200,
                {
                    "projects_json": sessions_data,
                    "oc_all_sessions_status": oc_all_status,
                    "oc_all_sessions_count": len(oc_all_data)
                    if isinstance(oc_all_data, list)
                    else oc_all_data,
                    "oc_orch_sessions": oc_orch_data,
                    "oc_orch_status": oc_orch_status,
                },
            )

        elif path == "/api/orch/session":
            # Return the dedicated headless orchestrator session.
            # The session ID is stored in orch-session.id. If absent or the
            # session no longer exists in opencode, return null so the dashboard
            # can offer a "create" button.
            session = None
            if ORCH_SESSION_FILE.exists():
                stored_id = ORCH_SESSION_FILE.read_text().strip()
                if stored_id:
                    status, data = _oc_api(
                        "GET",
                        f"/session/{urllib.parse.quote(stored_id, safe='')}",
                        timeout=5,
                    )
                    if status == 200 and isinstance(data, dict) and data.get("id"):
                        session = data
                    else:
                        # stale — clear the file
                        ORCH_SESSION_FILE.unlink(missing_ok=True)
            self._send_json(200, session)

        elif path == "/api/settings":
            status, data = api_get_settings()
            self._send_json(status, data)

        elif path == "/api/hermes/sessions":
            status, data = api_get_sessions()
            self._send_json(status, data)

        elif path == "/api/projects":
            status, data = api_get_sessions()
            self._send_json(status, data)

        elif path == "/api/cards":
            status, data = api_get_cards()
            self._send_json(status, data)

        elif path.startswith("/api/tasks"):
            project_id = qs.get("project", [""])[0]
            if not project_id:
                self._send_json(400, {"error": "project param required"})
            else:
                status, data = api_get_tasks(project_id)
                self._send_json(status, data)

        elif path == "/api/fs/dirs":
            q = qs.get("q", [""])[0]
            status, data = api_fs_dirs(q)
            self._send_json(status, data)

        elif path == "/api/fs/worktrees":
            d = qs.get("dir", [""])[0].strip()
            status, data = api_fs_worktrees(d)
            self._send_json(status, data)

        elif path.startswith("/api/stream/session/"):
            session_id = urllib.parse.unquote(path[len("/api/stream/session/") :])
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            api_stream_session(session_id, self.wfile)

        elif path == "/api/stream/global/event":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            _oc_proxy("GET", "/global/event", self, timeout=None)

        elif path.startswith("/api/stream/log/"):
            relpath = urllib.parse.unquote(path[len("/api/stream/log/") :])
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            api_stream_log(relpath, self.wfile)

        elif path == "/api/stream/diff":
            paths_param = qs.get("paths", [""])[0]
            paths_list = [p for p in paths_param.split(",") if p]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            api_stream_diff(paths_list, self.wfile)

        elif path == "/api/worktree-diff":
            paths_param = qs.get("paths", [""])[0]
            paths = [p for p in paths_param.split(",") if p]
            if not paths:
                self._send_json(400, {"error": "paths param required"})
            else:
                status, data = api_worktree_diff(paths)
                self._send_json(status, data)

        elif path.startswith("/api/oc/"):
            # Transparent proxy to opencode — strip /api/oc prefix
            oc_path = path[len("/api/oc") :] + (
                "?" + parsed.query if parsed.query else ""
            )
            _oc_proxy("GET", oc_path, self)

        elif path in ("/", "/index.html"):
            if NEW_DASHBOARD:
                # SPA — serve dashboard/build/index.html
                index = BASE_DIR / "dashboard" / "build" / "index.html"
                if index.exists():
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(index.read_bytes())
                else:
                    self.send_error(
                        503, "New dashboard not built — run: cd dashboard && pnpm build"
                    )
            else:
                self.send_response(302)
                self.send_header("Location", "/dashboard.html")
                self.end_headers()

        else:
            super().do_GET()

    def do_POST(self):
        if self.path == "/api/orchestrator/run":
            status, data = api_orchestrator_run()
            self._send_json(status, data)

        elif self.path == "/api/orchestrator/status":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            status, data = api_orchestrator_status(body)
            self._send_json(status, data)

        elif self.path == "/api/orch/session":
            # Create a new dedicated headless orchestrator session and persist its ID.
            # Caller may supply {"directory": "/some/path"} in the body; falls back to BASE_DIR.
            length = int(self.headers.get("Content-Length", 0))
            req_body = {}
            if length:
                try:
                    req_body = json.loads(self.rfile.read(length))
                except Exception:
                    pass
            oc_dir = req_body.get("directory") or BASE_DIR.as_posix()
            status, data = _oc_api(
                "POST",
                "/session",
                body={},
                timeout=10,
                directory=oc_dir,
            )
            if status == 200 and isinstance(data, dict) and data.get("id"):
                ORCH_SESSION_FILE.write_text(data["id"])
                self._send_json(200, data)
            else:
                self._send_json(status, data)

        elif self.path == "/api/settings":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            status, data = api_post_settings(body)
            self._send_json(status, data)

        elif self.path == "/api/hermes/sessions":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            status, data = api_post_sessions(body)
            self._send_json(status, data)

        elif self.path == "/api/projects":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            status, data = api_post_sessions(body)
            self._send_json(status, data)

        elif self.path == "/api/cards":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            status, data = api_post_cards(body)
            self._send_json(status, data)

        elif self.path.startswith("/api/cards/") and self.path.endswith("/restore"):
            card_id = self.path[len("/api/cards/") : -len("/restore")]
            status, data = api_restore_card(card_id)
            self._send_json(status, data)

        elif self.path.startswith("/api/tasks"):
            parsed2 = urllib.parse.urlparse(self.path)
            qs2 = urllib.parse.parse_qs(parsed2.query)
            project_id = qs2.get("project", [""])[0]
            if not project_id:
                self._send_json(400, {"error": "project param required"})
            else:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                status, data = api_post_tasks(project_id, body)
                self._send_json(status, data)

        elif self.path.startswith("/api/hermes/sessions/") and self.path.endswith(
            "/run"
        ):
            session_id = self.path[len("/api/hermes/sessions/") : -len("/run")]
            status, data = api_session_run(session_id)
            self._send_json(status, data)

        elif self.path.startswith("/api/oc/"):
            # Transparent proxy to opencode — strip /api/oc prefix
            parsed = urllib.parse.urlparse(self.path)
            oc_path = parsed.path[len("/api/oc") :] + (
                "?" + parsed.query if parsed.query else ""
            )
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else None
            _oc_proxy("POST", oc_path, self, body)

        else:
            self.send_error(404)

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        parts = path.split("/")
        # DELETE /api/cards/<card_id>
        if len(parts) == 4 and parts[1] == "api" and parts[2] == "cards":
            card_id = urllib.parse.unquote(parts[3])
            status, data = api_delete_card(card_id)
            self._send_json(status, data)
        # DELETE /api/hermes/sessions/<session_id>
        elif (
            len(parts) == 5
            and parts[1] == "api"
            and parts[2] == "hermes"
            and parts[3] == "sessions"
        ):
            sid = urllib.parse.unquote(parts[4])
            status, data = api_delete_session(sid)
            self._send_json(status, data)
        # DELETE /api/orch/session — forget the stored orch session ID
        elif path == "/api/orch/session":
            ORCH_SESSION_FILE.unlink(missing_ok=True)
            self._send_json(200, {"ok": True})
        # Transparent proxy for /api/oc/* DELETE to opencode
        elif path.startswith("/api/oc/"):
            oc_path = path[len("/api/oc") :] + (
                "?" + parsed.query if parsed.query else ""
            )
            _oc_proxy("DELETE", oc_path, self)
        else:
            self.send_error(404)

    def do_PUT(self):
        if self.path.startswith("/api/oc/"):
            parsed = urllib.parse.urlparse(self.path)
            oc_path = parsed.path[len("/api/oc") :] + (
                "?" + parsed.query if parsed.query else ""
            )
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else None
            _oc_proxy("PUT", oc_path, self, body)
        else:
            self.send_error(404)

    def do_PATCH(self):
        if self.path.startswith("/api/oc/"):
            parsed = urllib.parse.urlparse(self.path)
            oc_path = parsed.path[len("/api/oc") :] + (
                "?" + parsed.query if parsed.query else ""
            )
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else None
            _oc_proxy("PATCH", oc_path, self, body)
        else:
            self.send_error(404)


# ── Hot-reload watcher ────────────────────────────────────────────────────────
# Polls watched files every second; if any mtime changes, replaces this process
# with a fresh copy of itself via os.execv. systemd Restart=on-failure keeps
# the service alive through the restart, so no sudo is needed for code changes.

_WATCH_FILES = [
    Path(__file__).resolve(),  # server.py
    *(
        [BASE_DIR / "dashboard" / "build" / "index.html"]
        if NEW_DASHBOARD
        else [BASE_DIR / "dashboard.html"]
    ),
]


def _hot_reload_watcher(server: http.server.HTTPServer) -> None:
    mtimes = {p: p.stat().st_mtime for p in _WATCH_FILES if p.exists()}
    while True:
        time.sleep(1)
        for p in _WATCH_FILES:
            try:
                mtime = p.stat().st_mtime
            except FileNotFoundError:
                continue
            if mtimes.get(p) != mtime:
                print(f"[hot-reload] {p.name} changed — restarting…", flush=True)
                server.shutdown()
                os.execv(sys.executable, [sys.executable] + sys.argv)


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    mode = (
        "new dashboard (dashboard/build/)"
        if NEW_DASHBOARD
        else "legacy dashboard (dashboard.html)"
    )
    print(f"openswarm dashboard [{mode}] → http://0.0.0.0:{PORT}", flush=True)
    watcher = threading.Thread(target=_hot_reload_watcher, args=(server,), daemon=True)
    watcher.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down", flush=True)
        sys.exit(0)
