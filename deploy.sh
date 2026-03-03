#!/usr/bin/env bash
# Full first-time deployment: brings up all services (db, web, caddy).
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -f .env ]; then
    echo "Error: .env file not found. Copy .env.example and fill in the values." >&2
    exit 1
fi

echo "==> Pulling latest code..."
git pull

echo "==> Building web image..."
export GIT_DESCRIBE
GIT_DESCRIBE=$(git describe --tags --long --match 'v*' 2>/dev/null || echo "")
docker compose build web

echo "==> Starting all services..."
docker compose up -d

echo "==> Done. All services are up."
