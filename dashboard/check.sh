#!/usr/bin/env bash
# check.sh — svelte-check → knip → vitest
# Run from dashboard/ directory.
set -euo pipefail

echo "=== svelte-check ==="
pnpm check

echo "=== knip ==="
pnpm knip

echo "=== vitest ==="
# --passWithNoTests: Phase 1 has no test files yet; this will start failing
# as soon as test files are added without covering them.
pnpm test -- --passWithNoTests

echo "=== all checks passed ==="
