#!/usr/bin/env bash
# Serve the swarm dashboard on a local HTTP port.
# Usage: ./serve-dashboard.sh [port]   (default: 7700)
# Access: http://ubuntuserver:7700/dashboard.html

PORT="${1:-7700}"
SWARM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo "  swarm dashboard → http://ubuntuserver:${PORT}/dashboard.html"
echo "  Ctrl+C to stop"
echo ""

exec python3 -m http.server "$PORT" --directory "$SWARM_DIR" --bind 0.0.0.0
