<!-- LOC cap: 200 (created: 2026-04-21) -->

# BUGS

## BUG-001 — Settings panel shows "failed to load models"

**Status:** Unresolved

The model dropdown in the settings panel never populates. It shows "loading models…" briefly then falls back to "failed to load models" with a retry button. Retrying produces the same result.

### Symptom

`GET /api/models` returns an empty array `[]` or the request fails, causing `loadSettingsData()` in `dashboard.html` to hit the error branch.

### Solutions attempted (none worked)

1. **`opencode models` subprocess** — original approach. Spawned `opencode models` as a subprocess at server startup, cached the output, served it from `/api/models`. The subprocess would OOM-kill the service (Node.js ~250MB inside a 256MB MemoryMax container) or time out.

2. **Raised `MemoryMax` to 768M** — increased the systemd service memory limit to give the subprocess room. The service survived but `/api/models` still returned empty or timed out inconsistently.

3. **Background thread with `threading.Event`** — moved the subprocess call to a daemon thread at startup, `/api/models` blocked up to 20s waiting for `_models_ready` event. Did not fix the empty result.

4. **Replaced subprocess with opencode HTTP API** — rewrote `/api/models` to call `http://localhost:4097/provider` using `urllib.request`, filter to connected providers, and return `provider/model` strings. Verified manually that the endpoint returns 232 models. Service restart pending at time of writing — not confirmed working in the dashboard yet.

---

## BUG-002 — Orchestrator panel "session" tab shows wrong session

**Status:** Resolved (2026-04-21)

Resolved as part of the Hermes Gateway redesign. The orchestrator panel "session" tab was renamed to **"hermes"** and now fetches the latest Hermes cron run output from `GET /api/hermes/last-run` (which reads `~/.hermes/cron/output/<job_id>/*.md`). The old OpenCode session lookup was removed. The Python `orchestrator.py` loop was replaced by `poll_tasks.py` (a single-tick script driven by a Hermes cron job named `"openswarm-poll"` running every 2 minutes).

