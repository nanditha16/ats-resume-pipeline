#!/bin/bash

set -e

echo "🚀 Setting up MarkItDown environment..."

if ! docker info >/dev/null 2>&1; then
  echo "❌ Docker is not running. Please start Docker Desktop."
  exit 1
fi

# Create the git-ignored folders docker-compose bind-mounts (./input, ./output) and the rest of the skeleton.
bash "$(cd "$(dirname "$0")" && pwd)/init_dirs.sh" --quiet

# Build and start container.
# --force-recreate ensures volume-mount changes in docker-compose.yml (e.g.
# the hf-cache mount) actually take effect. Without this flag, `up -d` can
# leave an already-running container as-is even after the compose file
# changes, silently skipping new volume mounts.
docker compose up -d --build --force-recreate

echo "✅ Docker container is running"

# Ensure scripts are executable
chmod +x scripts/*.sh

echo "✅ Setup complete"
echo ""
echo "👉 Next steps:"
echo "  Add files to ./input/{pdf,docx,html,image,other}/"
echo "  Run: ./run.sh"
