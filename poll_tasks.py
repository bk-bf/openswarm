#!/usr/bin/env python3
"""
poll_tasks.py — single-tick orchestration script for the Hermes-based openswarm v2.

Designed to be invoked by the Hermes gateway cron job every 2 minutes.
Each invocation does exactly one orchestration cycle:
  1. Read settings.json — if autonomous=false, only report status and exit.
  2. Check running tasks for sentinel completion.
  3. Launch ready tasks (pending + all deps done).
  4. Write tasks.json atomically after every state change.

Run manually to trigger an immediate cycle:
    python3 /home/ubuntu/server/openswarm/poll_tasks.py --workdir <project-dir>

Exit codes:
    0  — cycle completed (may have done nothing if all tasks are terminal)
    1  — fatal error (missing tasks.json, etc.)
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────

SWARM_DIR = Path(__file__).parent.resolve()
SETTINGS_FILE = SWARM_DIR / "settings.json"

# These are overridden by --workdir
PROJECT_DIR = None  # type: Path | None
TASKS_FILE = None  # type: Path | None
LOGS_DIR = None  # type: Path | None


def apply_workdir(workdir: str) -> None:
    global PROJECT_DIR, TASKS_FILE, LOGS_DIR, SETTINGS_FILE

    PROJECT_DIR = Path(workdir).resolve()
    TASKS_FILE = PROJECT_DIR / ".openswarm" / "tasks" / "tasks.json"
    LOGS_DIR = SWARM_DIR / "logs"
    # If the project has its own settings.json, use it; else fall back to central
    local_settings = PROJECT_DIR / "settings.json"
    SETTINGS_FILE = (
        local_settings if local_settings.exists() else SWARM_DIR / "settings.json"
    )


DEFAULT_MODEL = "github-copilot/claude-sonnet-4.6"
WORKER_TIMEOUT_SEC = 90 * 60


# ── Logging ───────────────────────────────────────────────────────────────────


def log(msg: str, level: str = "INFO") -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    icons = {"OK": "✓", "WARN": "!", "ERROR": "✗", "HEADER": "▶"}
    icon = icons.get(level, "·")
    print(f"{ts} {icon} {msg}", flush=True)


# ── Settings ──────────────────────────────────────────────────────────────────


def load_settings() -> dict:
    if SETTINGS_FILE and SETTINGS_FILE.exists():
        try:
            return json.loads(SETTINGS_FILE.read_text())
        except Exception:
            pass
    return {}


# ── tasks.json helpers ────────────────────────────────────────────────────────


def load_tasks() -> list[dict] | None:
    if not TASKS_FILE or not TASKS_FILE.exists():
        return None
    data = json.loads(TASKS_FILE.read_text())
    return data.get("tasks", [])


def save_tasks(tasks: list[dict]) -> None:
    tmp = TASKS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"tasks": tasks}, indent=2))
    tmp.replace(TASKS_FILE)


# ── DAG helpers ───────────────────────────────────────────────────────────────


def get_ready_tasks(tasks: list[dict]) -> list[dict]:
    done_ids = {t["id"] for t in tasks if t["status"] == "done"}
    ready = []
    for t in tasks:
        if t["status"] != "pending":
            continue
        if all(dep in done_ids for dep in t.get("deps", [])):
            ready.append(t)
    return ready


def all_terminal(tasks: list[dict]) -> bool:
    return all(t["status"] in ("done", "failed") for t in tasks)


# ── Git helpers ───────────────────────────────────────────────────────────────


def ensure_worktree(task: dict) -> None:
    """Create the git worktree for *task* if it doesn't already exist."""
    task_dir = Path(task["dir"])
    if task_dir.exists():
        return
    worktree_branch = task.get("worktree")
    if not worktree_branch or not PROJECT_DIR:
        log(
            f"  [{task['id']}] dir {task_dir} missing and no worktree field — skipping creation",
            "WARN",
        )
        return
    log(f"  [{task['id']}] creating worktree {task_dir} (branch: {worktree_branch})")
    result = subprocess.run(
        ["git", "worktree", "add", str(task_dir), worktree_branch],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # Branch may not exist yet — create it from HEAD
        result2 = subprocess.run(
            ["git", "worktree", "add", "-b", worktree_branch, str(task_dir)],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
        )
        if result2.returncode != 0:
            raise RuntimeError(
                f"git worktree add failed for {task['id']}:\n{result.stderr}\n{result2.stderr}"
            )
    log(f"  [{task['id']}] worktree created", "OK")


# ── Sentinel helpers ──────────────────────────────────────────────────────────


def check_sentinel(task: dict) -> tuple[str | None, str | None]:
    task_dir = Path(task["dir"])
    done_f = task_dir / ".task-done"
    failed_f = task_dir / ".task-failed"
    if done_f.exists():
        return "done", done_f.read_text().strip()
    if failed_f.exists():
        return "failed", failed_f.read_text().strip()
    return None, None


def is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


# ── Worker management ─────────────────────────────────────────────────────────


def launch_worker(task: dict, settings: dict) -> subprocess.Popen:
    if LOGS_DIR:
        LOGS_DIR.mkdir(exist_ok=True)
        log_file = LOGS_DIR / f"{task['id']}-worker.log"
        log_handle = open(log_file, "w")
    else:
        log_handle = subprocess.DEVNULL  # type: ignore[assignment]
        log_file = None

    model = task.get("model") or settings.get("model") or DEFAULT_MODEL
    agent = task.get("agent") or settings.get("agent") or "build"
    run_dir = task["dir"]
    label = task.get("label") or task["id"]
    prompt = task.get("prompt") or f"Work on task {task['id']}: {label}"

    log(f"  [{task['id']}] model={model} agent={agent} dir={run_dir}")

    cmd = [
        "opencode",
        "run",
        "--dir",
        run_dir,
        "--title",
        label,
        "--model",
        model,
        "--agent",
        agent,
        "-c",
        prompt,
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=log_handle,
        stderr=log_handle,
        start_new_session=True,
    )
    if log_file:
        log(f"  [{task['id']}] launched PID {proc.pid} (log: {log_file.name})")
    else:
        log(f"  [{task['id']}] launched PID {proc.pid}")
    return proc


# ── Core poll cycle ───────────────────────────────────────────────────────────


def poll_cycle(force: bool = False) -> None:
    """
    One orchestration tick. Called by the Hermes cron job every 2 minutes,
    or manually for an immediate cycle.

    If *force* is True the autonomous check is skipped.
    """
    settings = load_settings()

    autonomous = settings.get("autonomous", True)
    if not autonomous and not force:
        log("autonomous=false — reporting status only, not launching workers")
        tasks = load_tasks()
        if tasks:
            counts = {
                s: sum(1 for t in tasks if t["status"] == s)
                for s in ("pending", "running", "done", "failed")
            }
            log(f"status: {counts}")
        return

    tasks = load_tasks()
    if tasks is None:
        log(f"tasks.json not found: {TASKS_FILE}", "WARN")
        return

    if not tasks:
        log("tasks list is empty — nothing to do")
        return

    if all_terminal(tasks):
        log("all tasks terminal — nothing to do")
        return

    now = datetime.now(timezone.utc).isoformat()

    # ── 1. Check running tasks ────────────────────────────────────────────────
    changed = False
    for task in tasks:
        if task["status"] != "running":
            continue

        sentinel_status, sentinel_msg = check_sentinel(task)
        if sentinel_status:
            if sentinel_status == "done":
                log(f"[{task['id']}] done — {sentinel_msg or '(sentinel)'}", "OK")
                task["status"] = "done"
                task["finished_at"] = now
            else:
                log(f"[{task['id']}] failed — {sentinel_msg or '(sentinel)'}", "ERROR")
                task["status"] = "failed"
                task["failure_reason"] = sentinel_msg or "sentinel .task-failed written"
                task["finished_at"] = now
            changed = True
            continue

        # No sentinel — check if worker is still alive or timed out
        pid = task.get("worker_pid")
        if pid and is_alive(pid):
            started = task.get("started_at")
            if started:
                elapsed = (
                    datetime.now(timezone.utc) - datetime.fromisoformat(started)
                ).total_seconds()
                if elapsed > WORKER_TIMEOUT_SEC:
                    log(
                        f"[{task['id']}] worker PID {pid} timed out after {elapsed:.0f}s",
                        "WARN",
                    )
                    try:
                        os.kill(pid, 9)
                    except ProcessLookupError:
                        pass
                    task["status"] = "failed"
                    task["failure_reason"] = f"timed out after {elapsed:.0f}s"
                    task["finished_at"] = now
                    changed = True
                else:
                    log(f"[{task['id']}] still running ({elapsed:.0f}s elapsed)")
        else:
            log(
                f"[{task['id']}] worker PID {pid} gone, no sentinel written — marking failed",
                "WARN",
            )
            task["status"] = "failed"
            task["failure_reason"] = "worker process exited without writing sentinel"
            task["finished_at"] = now
            changed = True

    if changed:
        save_tasks(tasks)

    # ── 2. Launch ready tasks ─────────────────────────────────────────────────
    for task in get_ready_tasks(tasks):
        log(f"[{task['id']}] launching: {task.get('label', task['id'])}", "HEADER")

        try:
            ensure_worktree(task)
        except RuntimeError as e:
            log(f"[{task['id']}] worktree setup failed: {e}", "ERROR")
            task["status"] = "failed"
            task["failure_reason"] = str(e)
            task["finished_at"] = now
            save_tasks(tasks)
            continue

        try:
            proc = launch_worker(task, settings)
        except Exception as e:
            log(f"[{task['id']}] failed to launch worker: {e}", "ERROR")
            task["status"] = "failed"
            task["failure_reason"] = str(e)
            task["finished_at"] = now
            save_tasks(tasks)
            continue

        task["status"] = "running"
        task["attempts"] = task.get("attempts", 0) + 1
        task["worker_pid"] = proc.pid
        task["started_at"] = now
        save_tasks(tasks)
        log(
            f"[{task['id']}] running (attempt {task['attempts']}/{task.get('max_attempts', 2)})",
            "OK",
        )

    log("poll cycle complete")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="openswarm v2 one-tick orchestrator")
    parser.add_argument(
        "--force", action="store_true", help="run regardless of autonomous setting"
    )
    parser.add_argument(
        "--workdir",
        required=True,
        help="project directory to orchestrate (must contain .openswarm/tasks/tasks.json)",
    )
    args = parser.parse_args()
    apply_workdir(args.workdir)
    try:
        poll_cycle(force=args.force)
    except Exception as e:
        log(f"fatal: {e}", "ERROR")
        import traceback

        traceback.print_exc()
        sys.exit(1)
