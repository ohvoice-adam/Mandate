#!/usr/bin/env bash
# scripts/dev-db.sh — provision and seed a local dev PostgreSQL container
#
# Usage:
#   ./scripts/dev-db.sh          # start container + migrate + create admin + seed
#   ./scripts/dev-db.sh start    # start container only
#   ./scripts/dev-db.sh migrate  # run flask db upgrade only
#   ./scripts/dev-db.sh admin    # create default admin user only
#   ./scripts/dev-db.sh seed     # run flask dev seed only
#   ./scripts/dev-db.sh reset    # wipe container + re-provision from scratch
#   ./scripts/dev-db.sh stop     # stop the container (data preserved)
#   ./scripts/dev-db.sh destroy  # stop and remove the container + volume

set -euo pipefail

# ── Venv ──────────────────────────────────────────────────────────────────────
# Prefer the project virtualenv's flask over any system/user install.
FLASK_BIN="flask"
if [ -x ".venv/bin/flask" ]; then
  FLASK_BIN=".venv/bin/flask"
fi

# ── Config ────────────────────────────────────────────────────────────────────
CONTAINER_NAME="mandate-dev-db"
POSTGRES_IMAGE="postgres:16-alpine"
POSTGRES_USER="petition"
POSTGRES_DB="mandate"
POSTGRES_PASSWORD="devpassword"
HOST_PORT="5432"
DATABASE_URL="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:${HOST_PORT}/${POSTGRES_DB}"

# ── Helpers ───────────────────────────────────────────────────────────────────
log()  { printf '\e[34m[dev-db]\e[0m %s\n' "$*"; }
ok()   { printf '\e[32m[dev-db]\e[0m %s\n' "$*"; }
warn() { printf '\e[33m[dev-db]\e[0m %s\n' "$*"; }
die()  { printf '\e[31m[dev-db]\e[0m %s\n' "$*" >&2; exit 1; }

require_docker() {
  command -v docker &>/dev/null || die "Docker is not installed or not in PATH."
  docker info &>/dev/null       || die "Docker daemon is not running."
}

container_running() {
  docker inspect -f '{{.State.Running}}' "$CONTAINER_NAME" 2>/dev/null | grep -q true
}

container_exists() {
  docker inspect "$CONTAINER_NAME" &>/dev/null
}

wait_for_postgres() {
  log "Waiting for PostgreSQL to be ready..."
  local retries=30
  until docker exec "$CONTAINER_NAME" pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" -q 2>/dev/null; do
    retries=$((retries - 1))
    [ "$retries" -le 0 ] && die "PostgreSQL did not become ready in time."
    sleep 1
  done
  ok "PostgreSQL is ready."
}

set_env_value() {
  local env_file="$1" key="$2" value="$3"
  if grep -q "^${key}=" "$env_file" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$env_file"
  else
    printf '\n%s=%s\n' "$key" "$value" >> "$env_file"
  fi
}

write_env() {
  local env_file=".env"
  if [ ! -f "$env_file" ]; then
    warn ".env file not found — creating one from .env.example."
    cp .env.example "$env_file"
  fi

  set_env_value "$env_file" "DATABASE_URL" "$DATABASE_URL"
  ok "Set DATABASE_URL in .env"

  # Ensure FLASK_ENV and FLASK_DEBUG are set for dev commands
  if ! grep -q "^FLASK_ENV=" "$env_file" 2>/dev/null; then
    printf 'FLASK_ENV=development\n' >> "$env_file"
    printf 'FLASK_DEBUG=1\n'         >> "$env_file"
    ok "Added FLASK_ENV=development and FLASK_DEBUG=1 to .env"
  fi
}

# ── Commands ──────────────────────────────────────────────────────────────────
cmd_start() {
  require_docker

  if container_running; then
    ok "Container '${CONTAINER_NAME}' is already running."
    return
  fi

  if container_exists; then
    log "Starting existing container '${CONTAINER_NAME}'..."
    docker start "$CONTAINER_NAME"
  else
    log "Pulling ${POSTGRES_IMAGE} and creating container '${CONTAINER_NAME}'..."
    docker run -d \
      --name "$CONTAINER_NAME" \
      -e POSTGRES_USER="$POSTGRES_USER" \
      -e POSTGRES_DB="$POSTGRES_DB" \
      -e POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
      -p "${HOST_PORT}:5432" \
      --restart unless-stopped \
      "$POSTGRES_IMAGE"
    ok "Container created."
  fi

  wait_for_postgres
  write_env
}

cmd_migrate() {
  log "Running database migrations..."
  DATABASE_URL="$DATABASE_URL" \
  FLASK_ENV=development \
  FLASK_DEBUG=1 \
    "$FLASK_BIN" db upgrade
  ok "Migrations applied."
}

cmd_admin() {
  log "Creating default admin user..."
  DATABASE_URL="$DATABASE_URL" \
  FLASK_ENV=development \
  FLASK_DEBUG=1 \
  ADMIN_EMAIL="${ADMIN_EMAIL:-admin@dev.example}" \
  ADMIN_PASSWORD="${ADMIN_PASSWORD:-devpassword}" \
    .venv/bin/python - <<'PYEOF'
import os
from app import create_app, db
from app.models import User
from app.models.user import UserRole

app = create_app()
with app.app_context():
    admin_email    = os.environ["ADMIN_EMAIL"]
    admin_password = os.environ["ADMIN_PASSWORD"]
    if not User.query.filter_by(email=admin_email).first():
        user = User(email=admin_email, first_name="Admin", last_name="User",
                    role=UserRole.ADMIN, must_change_password=False)
        user.set_password(admin_password)
        db.session.add(user)
        db.session.commit()
        print(f"  Admin created: {admin_email} / {admin_password}")
    else:
        print(f"  Admin already exists: {admin_email} — skipped.")
PYEOF
}

cmd_seed() {
  log "Seeding development data..."
  DATABASE_URL="$DATABASE_URL" \
  FLASK_ENV=development \
  FLASK_DEBUG=1 \
    "$FLASK_BIN" dev seed
  ok "Seed complete."
}

cmd_stop() {
  require_docker
  if container_exists; then
    log "Stopping container '${CONTAINER_NAME}'..."
    docker stop "$CONTAINER_NAME"
    ok "Container stopped. Data volume preserved."
  else
    warn "Container '${CONTAINER_NAME}' does not exist."
  fi
}

cmd_destroy() {
  require_docker
  warn "This will remove the container AND all its data."
  read -rp "Type 'yes' to confirm: " confirm
  [ "$confirm" = "yes" ] || { log "Cancelled."; exit 0; }

  docker stop "$CONTAINER_NAME" 2>/dev/null || true
  docker rm   "$CONTAINER_NAME" 2>/dev/null || true
  ok "Container removed."
}

cmd_reset() {
  log "Resetting dev database (destroy + full provision)..."
  require_docker

  docker stop "$CONTAINER_NAME" 2>/dev/null || true
  docker rm   "$CONTAINER_NAME" 2>/dev/null || true

  cmd_start
  cmd_migrate
  cmd_admin
  cmd_seed
}

cmd_all() {
  cmd_start
  cmd_migrate
  cmd_admin
  cmd_seed
}

# ── Dispatch ──────────────────────────────────────────────────────────────────
# Ensure we run from the project root regardless of where the script is called.
cd "$(dirname "$0")/.."

case "${1:-all}" in
  all)     cmd_all     ;;
  start)   cmd_start   ;;
  migrate) cmd_migrate ;;
  admin)   cmd_admin   ;;
  seed)    cmd_seed    ;;
  stop)    cmd_stop    ;;
  destroy) cmd_destroy ;;
  reset)   cmd_reset   ;;
  *)
    die "Unknown command '${1}'. Valid: all | start | migrate | admin | seed | stop | destroy | reset"
    ;;
esac
