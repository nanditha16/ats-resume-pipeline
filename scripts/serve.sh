#!/bin/bash
#
# serve.sh — Start the ATS Pipeline web UI inside the Docker container.
#
# Flask runs INSIDE the markitdown container (same Python env as all other
# scripts). The container exposes port 5001 to the host via docker-compose.
#
# Usage:
#   ./scripts/serve.sh
#   PORT=8080 ./scripts/serve.sh
#
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PORT="${PORT:-5001}"

cd "$PROJECT_DIR"

# Ensure container is running
if ! docker exec markitdown echo "ok" >/dev/null 2>&1; then
  echo "Container not running — starting it..."
  bash "$SCRIPT_DIR/init_dirs.sh" --quiet
  docker compose up -d
  sleep 3
fi

# Ensure Flask is installed inside the container
docker exec markitdown pip install flask --quiet 2>/dev/null || true

echo ""
echo "╔══════════════════════════════════════╗"
echo "║  ATS Pipeline — local web UI         ║"
echo "║  http://localhost:${PORT}               ║"
echo "║  Ctrl+C to stop                      ║"
echo "╚══════════════════════════════════════╝"
echo ""
echo "  Running inside Docker container 'markitdown'"
echo "  Project root mounted at /app"
echo ""

# Open browser after 2s (non-blocking)
(sleep 2 && open "http://localhost:${PORT}" 2>/dev/null || \
  xdg-open "http://localhost:${PORT}" 2>/dev/null || true) &

# Run Flask inside the container
# --host 0.0.0.0 required so the container accepts connections from the host
docker exec -it markitdown \
  python3 /app/web/app.py --host 0.0.0.0 --port "$PORT"
