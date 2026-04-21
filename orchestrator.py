#!/usr/bin/env python3
"""
openswarm Orchestrator
======================
Autonomous roadmap task manager. Reads ROADMAP_DEPS.json, resolves the dependency
DAG, spawns opencode worker sessions per task, polls for completion, handles retries
via investigator sessions, and writes a final report.

Usage:
    python3 orchestrator.py                        # init + run Tier 1 scope
    python3 orchestrator.py --scope T-210,T-211    # explicit scope
    python3 orchestrator.py --resume               # resume from existing state.json
    python3 orchestrator.py --resume --scope T-213,T-214,T-215,T-216  # expand scope

The orchestrator writes state.json after every state change — safe to kill and resume.
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ─── Paths ────────────────────────────────────────────────────────────────────

SWARM_DIR = Path(__file__).parent.resolve()

# Workspace root and roadmap path are resolved at startup from CLI args.
# Defaults are set in main() and injected into these module-level names so the
# rest of the code can reference them without passing arguments everywhere.
REPO_ROOT: Path = Path("/home/ubuntu/server/yact")  # overridden by --workspace
DEPS_FILE: Path = (
    REPO_ROOT / "yact-dev-docs" / ".tasks" / "open" / "ROADMAP_DEPS.json"
)  # overridden by --roadmap
STATE_FILE = SWARM_DIR / "state.json"
LOGS_DIR = SWARM_DIR / "logs"
REPORTS_DIR = SWARM_DIR / "reports"
SERVER_REPO: Path = REPO_ROOT / "yact-server"
WEB_REPO: Path = REPO_ROOT / "yact-web"
DOCS_ROOT: Path = REPO_ROOT / "yact-dev-docs"

WORKER_TMPL = SWARM_DIR / "prompts" / "worker-template.md"
INV_TMPL = SWARM_DIR / "prompts" / "investigator.md"

# Default model for all worker sessions.  Overridden by --model CLI arg.
# Individual tasks can override this via the "model" field in ROADMAP_DEPS.json.
DEFAULT_MODEL: str = "github-copilot/claude-sonnet-4.6"
# Model used for investigator sessions (defaults to DEFAULT_MODEL).
INVESTIGATOR_MODEL: str | None = None  # None → resolved to DEFAULT_MODEL at runtime

# ─── Config ───────────────────────────────────────────────────────────────────

MAX_ATTEMPTS = 2
WORKER_TIMEOUT_SEC = 90 * 60  # 90 minutes per worker
INVESTIGATOR_TIMEOUT = 30 * 60  # 30 minutes for investigator
POLL_INTERVAL_SEC = 60  # sentinel poll cadence
DOC_EXCERPT_CHARS = 4000  # max chars per inlined doc

TIER1_SCOPE = ["T-210", "T-211", "T-212"]

# ─── Colour helpers ───────────────────────────────────────────────────────────


def _c(code, text):
    return f"\033[{code}m{text}\033[0m"


def green(t):
    return _c("32", t)


def yellow(t):
    return _c("33", t)


def red(t):
    return _c("31", t)


def bold(t):
    return _c("1", t)


def dim(t):
    return _c("2", t)


# ─── Logging ──────────────────────────────────────────────────────────────────


def log(msg, level="INFO"):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    prefix = {
        "INFO": "·",
        "OK": green("✓"),
        "WARN": yellow("!"),
        "ERROR": red("✗"),
        "HEADER": bold("▶"),
    }
    print(f"{dim(ts)} {prefix.get(level, '·')} {msg}", flush=True)


# ─── File helpers ─────────────────────────────────────────────────────────────


def load_deps() -> list[dict]:
    with open(DEPS_FILE) as f:
        return json.load(f)


def load_state() -> dict | None:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return None


def save_state(state: dict):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE_FILE)


def init_state(scope: list[str], deps: list[dict]) -> dict:
    tasks = {}
    for task in deps:
        if task["id"] in scope:
            tasks[task["id"]] = {
                "status": "pending",
                "attempts": 0,
                "branches": {},
                "worktrees": {},
                "sentinel": None,
                "failure_reason": None,
                "investigator_diagnosis": None,
                "worker_pid": None,
                "started_at": None,
                "finished_at": None,
            }
    return {
        "scope": scope,
        "tasks": tasks,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }


# ─── DAG helpers ──────────────────────────────────────────────────────────────


def build_deps_map(deps: list[dict]) -> dict[str, dict]:
    return {t["id"]: t for t in deps}


def get_ready_tasks(state: dict, deps_map: dict[str, dict]) -> list[str]:
    """Pending tasks whose every blocker is either done or outside this run's scope (pre-shipped)."""
    ready = []
    for task_id, task in state["tasks"].items():
        if task["status"] != "pending":
            continue
        blockers = deps_map[task_id].get("blocked_by", [])
        all_clear = all(
            # blocker is in scope and done
            (state["tasks"].get(b, {}).get("status") == "done")
            # OR blocker is not in scope (treated as pre-shipped / already done)
            or (b not in state["tasks"])
            for b in blockers
        )
        if all_clear:
            ready.append(task_id)
    return ready


def all_terminal(state: dict) -> bool:
    return all(t["status"] in ("done", "failed") for t in state["tasks"].values())


# ─── Git helpers ──────────────────────────────────────────────────────────────


def repo_path(repo_name: str) -> Path:
    if repo_name == "server":
        return SERVER_REPO
    if repo_name == "web":
        return WEB_REPO
    raise ValueError(f"Unknown repo: {repo_name}")


def branch_name(task_id: str, slug: str) -> str:
    return f"feat/{task_id.lower()}-{slug}"


def worktree_path(repo_name: str, task_id: str, slug: str) -> Path:
    return repo_path(repo_name) / "features" / f"{task_id.lower()}-{slug}"


def git(args: list[str], cwd: Path, check=True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check,
    )


def create_worktrees(
    task_id: str, task_meta: dict, state: dict, deps_map: dict
) -> tuple[dict, dict]:
    """
    Create git worktrees for a task. Returns (worktrees, branches) dicts keyed by repo name.
    For multi-blocker tasks: branch from the most recently completed same-repo blocker.
    """
    slug = task_meta["slug"]
    repos = task_meta["repos"]
    blocked = task_meta.get("blocked_by", [])
    worktrees: dict[str, str] = {}
    branches: dict[str, str] = {}

    for repo in repos:
        branch = branch_name(task_id, slug)
        wt = worktree_path(repo, task_id, slug)
        rp = repo_path(repo)

        # Pick base: prefer the last done same-repo blocker branch, else main
        same_repo_done_blockers = [
            b
            for b in blocked
            if b in state["tasks"]
            and state["tasks"][b]["status"] == "done"
            and repo in deps_map[b]["repos"]
        ]
        base_branch = (
            state["tasks"][same_repo_done_blockers[-1]]["branches"][repo]
            if same_repo_done_blockers
            else "main"
        )

        if wt.exists():
            log(f"  worktree already exists: {wt} (resume)", "WARN")
        else:
            result = git(
                ["worktree", "add", "-b", branch, str(wt), base_branch],
                cwd=rp,
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"git worktree add failed for {task_id}/{repo}:\n{result.stderr}"
                )
            log(f"  created worktree {wt} (base: {base_branch})", "OK")

        worktrees[repo] = str(wt)
        branches[repo] = branch

    return worktrees, branches


def push_branch(repo_name: str, branch: str, wt_path: str) -> bool:
    result = git(["push", "-u", "origin", branch], cwd=Path(wt_path), check=False)
    if result.returncode != 0:
        log(f"  push failed for {branch}: {result.stderr.strip()}", "WARN")
        return False
    log(f"  pushed {branch}", "OK")
    return True


# ─── Prompt building ──────────────────────────────────────────────────────────


def load_doc_excerpt(rel_path: str) -> str:
    doc = DOCS_ROOT / rel_path
    if not doc.exists():
        return f"<!-- {rel_path} not found -->"
    text = doc.read_text(errors="replace")
    if len(text) > DOC_EXCERPT_CHARS:
        text = (
            text[:DOC_EXCERPT_CHARS]
            + f"\n\n… (truncated — full doc at yact-dev-docs/{rel_path})"
        )
    return text


def build_merge_instructions(
    task_id: str, task_meta: dict, state: dict, deps_map: dict, worktrees: dict
) -> str:
    blocked = task_meta.get("blocked_by", [])
    if not blocked:
        return "No blocker branches to merge — base is already `main`."

    lines = []
    for b in blocked:
        if b not in state["tasks"]:
            lines.append(f"- `{b}` — pre-shipped (already in main, nothing to merge)")
            continue
        b_state = state["tasks"][b]
        if b_state["status"] != "done":
            lines.append(
                f"- `{b}` — WARNING: blocker not yet done (status: {b_state['status']})"
            )
            continue
        for repo, branch in b_state["branches"].items():
            if repo in task_meta["repos"]:
                wt = worktrees.get(repo, f"<{repo} worktree>")
                lines.append(
                    f"- In the **{repo}** worktree (`{wt}`): `git merge origin/{branch}`"
                )
    return "\n".join(lines) if lines else "No blocker branches to merge."


def build_worker_prompt(
    task_id: str,
    task_meta: dict,
    worktrees: dict,
    branches: dict,
    state: dict,
    deps_map: dict,
    diagnosis: str | None = None,
) -> str:
    template = WORKER_TMPL.read_text()

    doc_sections = []
    for ref in task_meta.get("doc_refs", []):
        excerpt = load_doc_excerpt(ref)
        doc_sections.append(f"### `yact-dev-docs/{ref}`\n\n{excerpt}")

    merge_instructions = build_merge_instructions(
        task_id, task_meta, state, deps_map, worktrees
    )

    retry_section = ""
    if diagnosis:
        retry_section = (
            "\n\n---\n\n## ⚠ Retry — Investigator Diagnosis\n\n"
            "Your previous attempt failed. An investigator session analysed the worktree "
            "and produced the following diagnosis. Address it specifically before proceeding:\n\n"
            f"{diagnosis}"
        )

    worktrees_fmt = "\n".join(
        f"  - **{repo}**: `{path}`" for repo, path in worktrees.items()
    )
    branches_fmt = "\n".join(
        f"  - **{repo}**: `{branch}`" for repo, branch in branches.items()
    )

    # Build the set of other same-tier task branches the worker should know about
    # (so it can merge them if they share a worktree)
    blocker_branch_lines = []
    for b in task_meta.get("blocked_by", []):
        if b in state["tasks"] and state["tasks"][b]["status"] == "done":
            for repo, br in state["tasks"][b]["branches"].items():
                blocker_branch_lines.append(f"  - `{br}` ({repo})")
    blocker_branches_fmt = "\n".join(blocker_branch_lines) or "  (none)"

    return (
        template.replace("{{TASK_ID}}", task_id)
        .replace("{{TASK_TITLE}}", task_meta["title"])
        .replace("{{TASK_DESCRIPTION}}", task_meta["description"])
        .replace("{{REPOS}}", ", ".join(task_meta["repos"]))
        .replace("{{WORKTREES}}", worktrees_fmt)
        .replace("{{BRANCHES}}", branches_fmt)
        .replace("{{BLOCKER_BRANCHES}}", blocker_branches_fmt)
        .replace("{{MERGE_INSTRUCTIONS}}", merge_instructions)
        .replace("{{DOC_EXCERPTS}}", "\n\n---\n\n".join(doc_sections))
        .replace("{{RETRY_SECTION}}", retry_section)
    )


def build_investigator_prompt(
    task_id: str, worktrees: dict, failure_reason: str, worker_log_tail: str
) -> str:
    template = INV_TMPL.read_text()
    worktrees_fmt = "\n".join(
        f"  - **{repo}**: `{path}`" for repo, path in worktrees.items()
    )
    return (
        template.replace("{{TASK_ID}}", task_id)
        .replace("{{WORKTREES}}", worktrees_fmt)
        .replace(
            "{{FAILURE_REASON}}",
            failure_reason
            or "No .task-failed file written — worker timed out or exited non-zero.",
        )
        .replace("{{WORKER_LOG_TAIL}}", worker_log_tail)
    )


# ─── Worker launch & management ───────────────────────────────────────────────


def launch_worker(
    task_id: str, task_meta: dict, prompt: str, worktrees: dict
) -> subprocess.Popen:
    """Spawn `opencode run` in the primary worktree as a background process."""
    LOGS_DIR.mkdir(exist_ok=True)

    # Always write prompt to disk for debugging / resumability
    prompt_file = LOGS_DIR / f"{task_id}-worker-prompt.txt"
    prompt_file.write_text(prompt)

    # For multi-repo tasks, run opencode from the workspace root so the worker
    # can access all worktrees without hitting external_directory permission blocks.
    # For single-repo tasks, use the primary worktree directly.
    if len(task_meta["repos"]) > 1:
        run_dir = str(REPO_ROOT)
    else:
        primary_repo = task_meta["repos"][0]
        run_dir = worktrees[primary_repo]
    log_file = LOGS_DIR / f"{task_id}-worker.log"

    log_handle = open(log_file, "w")
    # Resolve model: per-task override wins, then global default.
    model = task_meta.get("model") or DEFAULT_MODEL
    log(f"  model: {model}")
    # Pass prompt as a direct positional argument — Python subprocess passes it
    # verbatim (no shell interpolation). ARG_MAX on Linux is ~2MB, safe for prompts.
    proc = subprocess.Popen(
        ["opencode", "run", "--dir", run_dir, "--model", model, prompt],
        stdout=log_handle,
        stderr=log_handle,
        start_new_session=True,
    )
    log(f"  launched opencode worker PID {proc.pid} (log: {log_file.name})")
    return proc


def check_sentinel(worktrees: dict) -> tuple[str | None, str | None]:
    """Return ('done'|'failed', message) or (None, None)."""
    for repo, wt in worktrees.items():
        done_f = Path(wt) / ".task-done"
        failed_f = Path(wt) / ".task-failed"
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


def run_investigator(
    task_id: str, task_meta: dict, worktrees: dict, failure_reason: str
) -> str:
    """
    Spawn a synchronous investigator opencode session.
    Returns a diagnosis string.
    """
    log(f"[{task_id}] spawning investigator session…", "WARN")
    LOGS_DIR.mkdir(exist_ok=True)

    log_file = LOGS_DIR / f"{task_id}-worker.log"
    worker_log_tail = ""
    if log_file.exists():
        text = log_file.read_text(errors="replace")
        worker_log_tail = text[-5000:] if len(text) > 5000 else text

    prompt = build_investigator_prompt(
        task_id, worktrees, failure_reason, worker_log_tail
    )

    prompt_file = LOGS_DIR / f"{task_id}-investigator-prompt.txt"
    prompt_file.write_text(prompt)

    output_file = LOGS_DIR / f"{task_id}-investigator-output.txt"
    # The investigator is instructed to write its diagnosis to this path
    output_file.unlink(missing_ok=True)

    if len(task_meta["repos"]) > 1:
        run_dir = str(REPO_ROOT)
    else:
        primary_repo = task_meta["repos"][0]
        run_dir = worktrees[primary_repo]
    inv_log = LOGS_DIR / f"{task_id}-investigator.log"
    inv_model = INVESTIGATOR_MODEL or DEFAULT_MODEL

    try:
        result = subprocess.run(
            ["opencode", "run", "--dir", run_dir, "--model", inv_model, prompt],
            capture_output=False,
            stdout=open(inv_log, "w"),
            stderr=subprocess.STDOUT,
            timeout=INVESTIGATOR_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        log(f"[{task_id}] investigator timed out", "WARN")

    if output_file.exists():
        diagnosis = output_file.read_text().strip()
        log(f"[{task_id}] investigator diagnosis: {diagnosis[:120]}…")
        return diagnosis

    # Fallback: last lines of the investigator's own log
    if inv_log.exists():
        tail = inv_log.read_text(errors="replace")[-2000:]
        return f"(no output file written by investigator — log tail follows)\n{tail}"

    return "Investigator produced no output."


# ─── Core orchestration loop ──────────────────────────────────────────────────


def run_orchestrator(state: dict, deps: list[dict]):
    deps_map = build_deps_map(deps)
    # active_workers: task_id → (Popen, launch_time)
    active: dict[str, tuple[subprocess.Popen, float]] = {}

    log("", "HEADER")
    log(bold("openswarm Orchestrator"), "HEADER")
    log(f"scope: {state['scope']}", "HEADER")
    log(f"state: {STATE_FILE}", "HEADER")
    log("")

    # Graceful shutdown on SIGINT / SIGTERM
    shutdown = [False]

    def _sig(*_):
        shutdown[0] = True
        log("shutdown signal received — finishing current poll then exiting", "WARN")

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    while not all_terminal(state) and not shutdown[0]:
        # ── 1. Launch ready tasks ────────────────────────────────────────────
        for task_id in get_ready_tasks(state, deps_map):
            task_meta = deps_map[task_id]
            task = state["tasks"][task_id]

            log(f"[{task_id}] {bold(task_meta['title'])} — creating worktrees…")
            try:
                worktrees, branches = create_worktrees(
                    task_id, task_meta, state, deps_map
                )
            except RuntimeError as e:
                log(f"[{task_id}] worktree creation failed: {e}", "ERROR")
                task["status"] = "failed"
                task["failure_reason"] = str(e)
                save_state(state)
                continue

            diagnosis = task.get("investigator_diagnosis")
            prompt = build_worker_prompt(
                task_id, task_meta, worktrees, branches, state, deps_map, diagnosis
            )

            proc = launch_worker(task_id, task_meta, prompt, worktrees)
            active[task_id] = (proc, time.time())

            task["status"] = "in_progress"
            task["attempts"] += 1
            task["branches"] = branches
            task["worktrees"] = worktrees
            task["worker_pid"] = proc.pid
            task["started_at"] = datetime.now(timezone.utc).isoformat()
            save_state(state)
            log(
                f"[{task_id}] in_progress (attempt {task['attempts']}/{MAX_ATTEMPTS})",
                "OK",
            )

        # ── 2. Poll in-progress tasks ─────────────────────────────────────────
        time.sleep(POLL_INTERVAL_SEC)

        for task_id in list(active.keys()):
            proc, launch_time = active[task_id]
            task = state["tasks"][task_id]
            task_meta = deps_map[task_id]

            sentinel, message = check_sentinel(task["worktrees"])
            elapsed = time.time() - launch_time
            timed_out = elapsed > WORKER_TIMEOUT_SEC

            # ── 2a. Worker done ───────────────────────────────────────────────
            if sentinel == "done":
                log(f"[{task_id}] sentinel .task-done found: {message[:80]}", "OK")
                task["status"] = "done"
                task["sentinel"] = message
                task["finished_at"] = datetime.now(timezone.utc).isoformat()
                # Push all branches
                for repo, branch in task["branches"].items():
                    push_branch(repo, branch, task["worktrees"][repo])
                del active[task_id]
                save_state(state)
                continue

            # ── 2b. Worker failed ─────────────────────────────────────────────
            if (
                sentinel == "failed"
                or timed_out
                or (proc.poll() is not None and sentinel is None)
            ):
                reason = (
                    message
                    if sentinel == "failed"
                    else (
                        f"timeout after {int(elapsed / 60)}min"
                        if timed_out
                        else f"process exited with code {proc.returncode} — no sentinel written"
                    )
                )
                log(f"[{task_id}] failed: {reason}", "WARN")

                if proc.poll() is None:
                    proc.terminate()

                if task["attempts"] < MAX_ATTEMPTS:
                    # Investigate and retry
                    diagnosis = run_investigator(
                        task_id, task_meta, task["worktrees"], reason
                    )
                    task["investigator_diagnosis"] = diagnosis
                    task["status"] = "pending"  # back to pending for retry
                    task["failure_reason"] = reason
                    # Clean sentinel so the retry doesn't immediately re-trigger
                    for wt in task["worktrees"].values():
                        for sf in [".task-done", ".task-failed"]:
                            fp = Path(wt) / sf
                            fp.unlink(missing_ok=True)
                    del active[task_id]
                    save_state(state)
                    log(
                        f"[{task_id}] queued for retry (attempt {task['attempts'] + 1}/{MAX_ATTEMPTS})"
                    )
                else:
                    task["status"] = "failed"
                    task["failure_reason"] = reason
                    task["finished_at"] = datetime.now(timezone.utc).isoformat()
                    del active[task_id]
                    save_state(state)
                    log(
                        f"[{task_id}] permanently failed after {task['attempts']} attempts",
                        "ERROR",
                    )
                continue

            # ── 2c. Still running ─────────────────────────────────────────────
            log(f"[{task_id}] running ({int(elapsed / 60)}min elapsed…)", "INFO")

    # ── Final summary ─────────────────────────────────────────────────────────
    write_report(state, deps_map)


def write_report(state: dict, deps_map: dict):
    REPORTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = REPORTS_DIR / f"run-report-{ts}.md"

    lines = [
        "# Swarm Run Report",
        f"\nGenerated: {datetime.now(timezone.utc).isoformat()}",
        f"\nScope: {', '.join(state['scope'])}",
        "\n## Results\n",
        "| Task | Title | Status | Attempts | Duration |",
        "|------|-------|--------|----------|----------|",
    ]
    for task_id, task in state["tasks"].items():
        meta = deps_map.get(task_id, {})
        title = meta.get("title", "—")
        status_icon = {
            "done": "✅",
            "failed": "❌",
            "in_progress": "⏳",
            "pending": "⏸",
        }.get(task["status"], "?")
        status = f"{status_icon} {task['status']}"
        attempts = str(task.get("attempts", 0))
        duration = "—"
        if task.get("started_at") and task.get("finished_at"):
            s = datetime.fromisoformat(task["started_at"])
            e = datetime.fromisoformat(task["finished_at"])
            mins = int((e - s).total_seconds() / 60)
            duration = f"{mins}min"
        lines.append(f"| {task_id} | {title} | {status} | {attempts} | {duration} |")

    # Failures section
    failed = [(tid, t) for tid, t in state["tasks"].items() if t["status"] == "failed"]
    if failed:
        lines += ["\n## Failed Tasks\n"]
        for tid, t in failed:
            lines.append(
                f"### {tid}\n\n**Reason:** {t.get('failure_reason', 'unknown')}\n"
            )
            if t.get("investigator_diagnosis"):
                lines.append(
                    f"**Investigator diagnosis:**\n\n{t['investigator_diagnosis']}\n"
                )

    path.write_text("\n".join(lines))
    log(f"report written: {path}", "OK")
    log("")
    log(bold("─── Summary ───"))
    done = sum(1 for t in state["tasks"].values() if t["status"] == "done")
    failed = sum(1 for t in state["tasks"].values() if t["status"] == "failed")
    total = len(state["tasks"])
    log(f"{green(str(done))} done  {red(str(failed))} failed  {total} total")


# ─── CLI entry point ──────────────────────────────────────────────────────────


def main():
    global REPO_ROOT, DEPS_FILE, SERVER_REPO, WEB_REPO, DOCS_ROOT
    global DEFAULT_MODEL, INVESTIGATOR_MODEL

    parser = argparse.ArgumentParser(description="openswarm Orchestrator")
    parser.add_argument(
        "--workspace",
        default="/home/ubuntu/server/yact",
        help="Path to the yact metarepo workspace root (default: /home/ubuntu/server/yact)",
    )
    parser.add_argument(
        "--roadmap",
        default=None,
        help="Path to ROADMAP_DEPS.json (default: <workspace>/yact-dev-docs/.tasks/open/ROADMAP_DEPS.json)",
    )
    parser.add_argument(
        "--scope",
        default=",".join(TIER1_SCOPE),
        help="Comma-separated task IDs to run (default: Tier 1 = T-210,T-211,T-212)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from existing state.json (appends new scope tasks if --scope is wider)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "Default model for all worker sessions, in provider/model format "
            "(e.g. anthropic/claude-sonnet-4-5). "
            "Per-task 'model' field in ROADMAP_DEPS.json overrides this."
        ),
    )
    parser.add_argument(
        "--investigator-model",
        default=None,
        dest="investigator_model",
        help="Model for investigator sessions. Defaults to --model.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the resolved task graph and prompts without launching any workers",
    )
    args = parser.parse_args()

    # Configure workspace-derived paths from CLI args
    REPO_ROOT = Path(args.workspace).resolve()
    DEPS_FILE = (
        Path(args.roadmap).resolve()
        if args.roadmap
        else REPO_ROOT / "yact-dev-docs" / ".tasks" / "open" / "ROADMAP_DEPS.json"
    )
    SERVER_REPO = REPO_ROOT / "yact-server"
    WEB_REPO = REPO_ROOT / "yact-web"
    DOCS_ROOT = REPO_ROOT / "yact-dev-docs"

    if args.model:
        DEFAULT_MODEL = args.model
    INVESTIGATOR_MODEL = args.investigator_model  # None → falls back to DEFAULT_MODEL
    log(f"worker model:      {DEFAULT_MODEL}")

    scope = [s.strip() for s in args.scope.split(",") if s.strip()]
    deps = load_deps()
    deps_map = build_deps_map(deps)

    # Validate scope
    known = {t["id"] for t in deps}
    unknown = [s for s in scope if s not in known]
    if unknown:
        print(
            f"ERROR: unknown task IDs: {unknown}\nKnown: {sorted(known)}",
            file=sys.stderr,
        )
        sys.exit(1)

    # State init or resume
    if args.resume:
        state = load_state()
        if state is None:
            log("No state.json found — initialising fresh", "WARN")
            state = init_state(scope, deps)
        else:
            # Merge new scope tasks without resetting existing ones
            for task_id in scope:
                if task_id not in state["tasks"]:
                    state["tasks"][task_id] = {
                        "status": "pending",
                        "attempts": 0,
                        "branches": {},
                        "worktrees": {},
                        "sentinel": None,
                        "failure_reason": None,
                        "investigator_diagnosis": None,
                        "worker_pid": None,
                        "started_at": None,
                        "finished_at": None,
                    }
            if task_id not in state["scope"]:
                state["scope"].append(task_id)
            log(f"resumed state.json ({len(state['tasks'])} tasks)")
            # Any task left as in_progress from a previous interrupted run has no
            # live process to poll — reset it to pending so it gets re-launched.
            for task_id, task in state["tasks"].items():
                if task["status"] == "in_progress":
                    log(
                        f"  [{task_id}] was in_progress — resetting to pending for re-launch",
                        "WARN",
                    )
                    task["status"] = "pending"
                    task["worker_pid"] = None
    else:
        existing = load_state()
        if existing:
            log(
                "state.json already exists — use --resume to continue or delete it to start fresh",
                "WARN",
            )
            print(
                "\nDelete state.json and run again, or pass --resume.", file=sys.stderr
            )
            sys.exit(1)
        state = init_state(scope, deps)

    save_state(state)

    if args.dry_run:
        log("DRY RUN — task graph:", "HEADER")
        for task_id in scope:
            meta = deps_map[task_id]
            blockers = meta.get("blocked_by", [])
            ready = all(
                state["tasks"].get(b, {}).get("status") == "done"
                or b not in state["tasks"]
                for b in blockers
            )
            print(f"\n  {task_id}: {meta['title']}")
            print(f"    repos:      {meta['repos']}")
            print(f"    blocked_by: {blockers or '—'}")
            print(f"    ready now:  {ready}")
            print(f"    prompt preview:")
            wt_dummy = {r: f"<{r}-worktree>" for r in meta["repos"]}
            br_dummy = {r: branch_name(task_id, meta["slug"]) for r in meta["repos"]}
            prompt = build_worker_prompt(
                task_id, meta, wt_dummy, br_dummy, state, deps_map
            )
            print("    " + prompt[:400].replace("\n", "\n    ") + "…")
        return

    LOGS_DIR.mkdir(exist_ok=True)
    REPORTS_DIR.mkdir(exist_ok=True)
    run_orchestrator(state, deps)


if __name__ == "__main__":
    main()
