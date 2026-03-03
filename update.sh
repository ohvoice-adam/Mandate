#!/usr/bin/env bash
# Update the web service only (no downtime to db or caddy).
set -euo pipefail

cd "$(dirname "$0")"

echo "==> Pulling latest code..."
git pull

echo "==> Building web image..."
export GIT_DESCRIBE
GIT_DESCRIBE=$(git describe --tags --long --match 'v*' 2>/dev/null || echo "")
docker compose build web

echo "==> Restarting web service..."
docker compose up -d --no-deps web

echo "==> Done. Web service updated."
