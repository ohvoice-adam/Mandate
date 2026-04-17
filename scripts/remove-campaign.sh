#!/usr/bin/env bash
# remove-campaign.sh — Cleanly remove a Mandate campaign instance from this server.
#
# What this script does:
#   1. Lists running mandate_web_* campaigns and prompts you to pick one
#   2. Optionally exports a database backup before touching anything
#   3. Removes the site block from the Caddyfile and reloads Caddy
#   4. Stops and removes the campaign's Docker containers
#   5. Optionally deletes the Docker volumes (PostgreSQL data + uploads)
#   6. Optionally deletes the campaign directory on disk
#
# Campaign 1 (the stack that runs Caddy) cannot be removed with this script.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CAMPAIGN1_DIR="$(dirname "$SCRIPT_DIR")"
CADDYFILE="$CAMPAIGN1_DIR/Caddyfile"

red()    { printf '\033[31m%s\033[0m\n' "$*"; }
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
bold()   { printf '\033[1m%s\033[0m\n' "$*"; }
dim()    { printf '\033[2m%s\033[0m\n' "$*"; }

die() { red "Error: $*"; exit 1; }

confirm() {
  local prompt="$1"
  local reply
  read -rp "$prompt [y/N] " reply
  [[ "$reply" =~ ^[Yy]$ ]]
}

# ── Preflight checks ──────────────────────────────────────────────────────────
[[ -f "$CADDYFILE" ]] || die "Caddyfile not found at $CADDYFILE. Run this script from inside the campaign 1 repo."
command -v docker &>/dev/null || die "docker is not installed or not in PATH."

if ! docker compose -f "$CAMPAIGN1_DIR/docker-compose.yml" ps caddy 2>/dev/null | grep -q "running\|Up"; then
  die "Caddy is not running. Campaign 1 must be up to reload Caddy after removal."
fi

# ── List campaigns ────────────────────────────────────────────────────────────
bold "=== Mandate — Remove Campaign ==="
echo

mapfile -t CONTAINERS < <(
  docker ps -a --format '{{.Names}}' | grep '^mandate_web_' | sort -V
)

[[ ${#CONTAINERS[@]} -eq 0 ]] && die "No mandate_web_* containers found."

# Campaign 1 runs Caddy — exclude it
REMOVABLE=()
for c in "${CONTAINERS[@]}"; do
  [[ "$c" == "mandate_web_1" ]] && continue
  REMOVABLE+=("$c")
done

[[ ${#REMOVABLE[@]} -eq 0 ]] && die "No removable campaigns found (mandate_web_1 is campaign 1 and cannot be removed here)."

echo "Running campaigns (campaign 1 excluded):"
echo
for i in "${!REMOVABLE[@]}"; do
  container="${REMOVABLE[$i]}"
  work_dir=$(docker inspect "$container" \
    --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' 2>/dev/null || echo "unknown")
  project=$(docker inspect "$container" \
    --format '{{index .Config.Labels "com.docker.compose.project"}}' 2>/dev/null || echo "unknown")
  status=$(docker inspect "$container" --format '{{.State.Status}}' 2>/dev/null || echo "unknown")
  printf "  [%d] %s  (%s)  %s\n" "$((i+1))" "$container" "$status" "$work_dir"
done

echo
read -rp "Enter number to remove (or q to quit): " CHOICE
[[ "$CHOICE" == "q" || "$CHOICE" == "Q" ]] && { echo "Aborted."; exit 0; }
[[ "$CHOICE" =~ ^[0-9]+$ && "$CHOICE" -ge 1 && "$CHOICE" -le ${#REMOVABLE[@]} ]] \
  || die "Invalid choice."

CONTAINER="${REMOVABLE[$((CHOICE-1))]}"

# ── Gather details for selected campaign ──────────────────────────────────────
WORK_DIR=$(docker inspect "$CONTAINER" \
  --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' 2>/dev/null)
PROJECT=$(docker inspect "$CONTAINER" \
  --format '{{index .Config.Labels "com.docker.compose.project"}}' 2>/dev/null)

[[ -n "$WORK_DIR" ]] || die "Could not determine working directory for $CONTAINER."

if [[ -f "$WORK_DIR/docker-compose.campaign.yml" ]]; then
  COMPOSE_FILE="$WORK_DIR/docker-compose.campaign.yml"
else
  die "docker-compose.campaign.yml not found in $WORK_DIR. Refusing to remove an unrecognised stack."
fi

# Find the domain for this container in the Caddyfile
# The site block immediately precedes the container reference
DOMAIN=$(grep -B5 "import mandate_proxy ${CONTAINER}" "$CADDYFILE" \
  | grep -v '^#' | grep -v 'import' | grep -v '^$' | grep '{' \
  | sed 's/ {//' | tail -1 | xargs || true)

echo
bold "You are about to remove:"
echo "  Container : $CONTAINER"
echo "  Project   : $PROJECT"
echo "  Directory : $WORK_DIR"
[[ -n "$DOMAIN" ]] && echo "  Domain    : $DOMAIN"
echo

confirm "Continue?" || { echo "Aborted."; exit 0; }

# ── Step 1: Optional database backup ─────────────────────────────────────────
echo
if confirm "Export a database backup before removing?"; then
  BACKUP_FILE="${PROJECT}-$(date +%Y%m%d-%H%M%S).sql"
  echo "[ .. ] Dumping database to $BACKUP_FILE..."
  docker compose -f "$COMPOSE_FILE" -p "$PROJECT" \
    exec -T db pg_dump -U petition mandate > "$BACKUP_FILE"
  green "[ ok ] Backup saved: $BACKUP_FILE"
else
  yellow "[ -- ] Skipping database backup"
fi

# ── Step 2: Remove site block from Caddyfile ──────────────────────────────────
echo
echo "[ .. ] Removing site block from Caddyfile..."

# Build a Python one-liner to remove the block — more reliable than sed for
# multi-line blocks with varying whitespace
python3 - "$CADDYFILE" "$CONTAINER" <<'PYEOF'
import sys, re

caddyfile, container = sys.argv[1], sys.argv[2]
with open(caddyfile) as f:
    content = f.read()

# Remove the comment header line and the site block for this container
# Matches: optional comment line, then site-address { ... import mandate_proxy <container> ... }
pattern = (
    r'(?m)'
    r'(?:^#[^\n]*' + re.escape(container) + r'[^\n]*\n)?'   # optional comment line
    r'\n?'
    r'^[^\s#][^\n]*\{\s*\n'                                   # site address {
    r'(?:[^\}]*\n)*?'                                         # body lines
    r'[^\n]*import mandate_proxy ' + re.escape(container) + r'[^\n]*\n'  # import line
    r'(?:[^\}]*\n)*?'                                         # remaining body
    r'\}\n?'                                                  # closing }
)
new_content = re.sub(pattern, '', content)

if new_content == content:
    print(f"WARNING: Could not find site block for {container} in Caddyfile — manual removal may be needed", file=sys.stderr)
    sys.exit(0)

with open(caddyfile, 'w') as f:
    f.write(new_content.rstrip('\n') + '\n')

print(f"Removed site block for {container}")
PYEOF

echo "[ .. ] Reloading Caddy..."
docker compose -f "$CAMPAIGN1_DIR/docker-compose.yml" \
  exec caddy caddy reload --config /etc/caddy/Caddyfile --force
green "[ ok ] Caddy reloaded — domain is no longer routed"

# ── Step 3: Stop containers ───────────────────────────────────────────────────
echo
echo "[ .. ] Stopping campaign containers..."
docker compose -f "$COMPOSE_FILE" -p "$PROJECT" down
green "[ ok ] Containers stopped"

# ── Step 4: Optional volume deletion ─────────────────────────────────────────
echo
yellow "Data volumes contain the PostgreSQL database and any uploaded files."
if confirm "Permanently delete data volumes? (IRREVERSIBLE — skip if unsure)"; then
  echo "[ .. ] Removing volumes..."
  docker compose -f "$COMPOSE_FILE" -p "$PROJECT" down -v 2>/dev/null || true
  green "[ ok ] Volumes deleted"
else
  yellow "[ -- ] Volumes preserved — they will not consume resources but occupy disk space"
  dim   "       To remove later: docker compose -f $COMPOSE_FILE -p $PROJECT down -v"
fi

# ── Step 5: Optional directory deletion ───────────────────────────────────────
echo
if [[ -d "$WORK_DIR" ]]; then
  if confirm "Delete campaign directory $WORK_DIR from disk?"; then
    rm -rf "$WORK_DIR"
    green "[ ok ] Directory removed"
  else
    yellow "[ -- ] Directory preserved at $WORK_DIR"
  fi
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo
green "=== Campaign '$PROJECT' removed ==="
[[ -n "${BACKUP_FILE:-}" ]] && echo "  Backup : $BACKUP_FILE"
