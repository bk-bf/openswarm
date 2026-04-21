#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# openswarm launcher
# ─────────────────────────────────────────────────────────────────────────────
# Usage (interactive):
#   ./swarm.sh                              # Tier 1 (T-210, T-211, T-212)
#   ./swarm.sh --scope T-210,T-211,T-212   # explicit scope
#   ./swarm.sh --resume                    # resume from state.json
#   ./swarm.sh --resume --scope T-213,T-214,T-215,T-216  # expand to Tier 2+3
#   ./swarm.sh --dry-run                   # preview task graph + prompts
#
# Usage (service mode — args sourced from swarm.env):
#   systemctl start openswarm             # uses SWARM_ARGS from swarm.env
#   systemctl start openswarm-dashboard   # dashboard always-on at :7700
#
# Run as the `agent` user from the metarepo root or the swarm/ directory.
# Do NOT run as ubuntu (not needed — orchestrator.py only spawns opencode sessions
# and runs git commands; it never calls start-features.sh or writes .env files).
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SWARM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Workspace can be overridden via env var; defaults to yact metarepo
WORKSPACE="${OPENSWARM_WORKSPACE:-/home/ubuntu/server/yact}"

# ── Python check ──────────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
  echo "ERROR: python3 not found in PATH" >&2
  exit 1
fi

PYTHON_VER=$(python3 -c 'import sys; print(sys.version_info >= (3, 11))')
if [[ "$PYTHON_VER" != "True" ]]; then
  echo "ERROR: python3.11+ required (found $(python3 --version))" >&2
  exit 1
fi

# ── opencode check ────────────────────────────────────────────────────────────
if ! command -v opencode &>/dev/null; then
  echo "ERROR: opencode not found in PATH" >&2
  exit 1
fi

# ── Deps check ────────────────────────────────────────────────────────────────
DEPS_FILE="$WORKSPACE/yact-dev-docs/.tasks/open/ROADMAP_DEPS.json"
if [[ ! -f "$DEPS_FILE" ]]; then
  echo "ERROR: ROADMAP_DEPS.json not found at $DEPS_FILE" >&2
  echo "       Set OPENSWARM_WORKSPACE or pass --roadmap to orchestrator.py" >&2
  exit 1
fi

# ── State file banner ────────────────────────────────────────────────────────
STATE_FILE="$SWARM_DIR/state.json"
if [[ -f "$STATE_FILE" ]] && [[ "$*" != *"--resume"* ]] && [[ "$*" != *"--dry-run"* ]]; then
  echo ""
  echo "  state.json already exists at:"
  echo "  $STATE_FILE"
  echo ""
  echo "  To resume an interrupted run:  ./swarm.sh --resume"
  echo "  To start fresh:                rm $STATE_FILE && ./swarm.sh"
  echo ""
  exit 1
fi

# ── Log header ────────────────────────────────────────────────────────────────
echo ""
  echo "  ┌─────────────────────────────────────┐"
  echo "  │  openswarm Orchestrator             │"
  echo "  │  logs  → logs/                      │"
  echo "  │  state → state.json                 │"
  echo "  │  press Ctrl+C to stop gracefully    │"
  echo "  └─────────────────────────────────────┘"
echo ""

# ── Launch ────────────────────────────────────────────────────────────────────
exec python3 "$SWARM_DIR/orchestrator.py" --workspace "$WORKSPACE" "$@"
