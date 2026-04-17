#!/usr/bin/env bash
# new-campaign.sh — Stand up a new Mandate campaign instance on this server.
#
# What this script does:
#   1. Prompts for campaign slug, domain, admin email, and deploy directory
#   2. Ensures the mandate-proxy Docker network exists
#   3. Clones the repo into the campaign directory
#   4. Generates unique POSTGRES_PASSWORD and SECRET_KEY
#   5. Writes the campaign's .env file
#   6. Adds a site block to the Caddyfile and reloads Caddy (zero downtime)
#   7. Starts the new campaign stack
#
# Prerequisites:
#   - Campaign 1 (with Caddy) is already running: docker compose up -d
#   - The mandate-proxy Docker network exists (this script creates it if not)
#   - You have write access to the target deploy directory
#   - Git, Docker, and Python 3 are available

set -euo pipefail

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CAMPAIGN1_DIR="$(dirname "$SCRIPT_DIR")"
CADDYFILE="$CAMPAIGN1_DIR/Caddyfile"
CAMPAIGN1_ENV="$CAMPAIGN1_DIR/.env"

# ── Helpers ───────────────────────────────────────────────────────────────────
red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
bold()  { printf '\033[1m%s\033[0m\n' "$*"; }

die() { red "Error: $*"; exit 1; }

generate_secret() {
  python3 -c "import secrets; print(secrets.token_hex(32))"
}

# Scan the Caddyfile for existing mandate_web_N entries and return the next number.
next_campaign_number() {
  local max=1
  while IFS= read -r line; do
    if [[ "$line" =~ mandate_web_([0-9]+) ]]; then
      local n="${BASH_REMATCH[1]}"
      (( n > max )) && max=$n
    fi
  done < "$CADDYFILE"
  echo $(( max + 1 ))
}

# ── Preflight checks ──────────────────────────────────────────────────────────
[[ -f "$CADDYFILE" ]]     || die "Caddyfile not found at $CADDYFILE. Run this script from inside the campaign 1 repo."
[[ -f "$CAMPAIGN1_ENV" ]] || die ".env not found at $CAMPAIGN1_ENV. Campaign 1 must be configured before adding more campaigns."
command -v docker  &>/dev/null || die "docker is not installed or not in PATH."
command -v git     &>/dev/null || die "git is not installed or not in PATH."
command -v python3 &>/dev/null || die "python3 is not installed or not in PATH."

# Caddy must be running — we reload it at the end
if ! docker compose -f "$CAMPAIGN1_DIR/docker-compose.yml" ps caddy 2>/dev/null | grep -q "running\|Up"; then
  die "Caddy is not running. Start campaign 1 first: docker compose up -d"
fi

# ── Gather inputs ─────────────────────────────────────────────────────────────
bold "=== Mandate — New Campaign Setup ==="
echo

read -rp "Campaign slug (lowercase, hyphens OK — used for directory and compose project name): " CAMPAIGN_SLUG
[[ -n "$CAMPAIGN_SLUG" ]] || die "Campaign slug cannot be empty."
[[ "$CAMPAIGN_SLUG" =~ ^[a-z0-9-]+$ ]] || die "Slug must be lowercase letters, numbers, and hyphens only."

read -rp "Domain (e.g. campaign2.example.com): " CAMPAIGN_DOMAIN
[[ -n "$CAMPAIGN_DOMAIN" ]] || die "Domain cannot be empty."

read -rp "Admin email for this campaign: " ADMIN_EMAIL
[[ -n "$ADMIN_EMAIL" ]] || die "Admin email cannot be empty."

DEFAULT_DIR="/opt/mandate-${CAMPAIGN_SLUG}"
read -rp "Deploy directory [${DEFAULT_DIR}]: " CAMPAIGN_DIR
CAMPAIGN_DIR="${CAMPAIGN_DIR:-$DEFAULT_DIR}"

# ── Derived values ────────────────────────────────────────────────────────────
CAMPAIGN_NUM=$(next_campaign_number)
CONTAINER_NAME="mandate_web_${CAMPAIGN_NUM}"
COMPOSE_PROJECT="mandate-${CAMPAIGN_SLUG}"

echo
bold "Summary"
echo "  Slug            : $CAMPAIGN_SLUG"
echo "  Domain          : $CAMPAIGN_DOMAIN"
echo "  Admin email     : $ADMIN_EMAIL"
echo "  Deploy directory: $CAMPAIGN_DIR"
echo "  Container name  : $CONTAINER_NAME"
echo "  Compose project : $COMPOSE_PROJECT"
echo

# Validate nothing already exists
[[ -e "$CAMPAIGN_DIR" ]] && die "Directory $CAMPAIGN_DIR already exists. Choose a different path or remove it first."
if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
  die "Container $CONTAINER_NAME already exists. Is campaign $CAMPAIGN_NUM already running?"
fi
if grep -q "$CONTAINER_NAME" "$CADDYFILE"; then
  die "$CONTAINER_NAME is already referenced in the Caddyfile."
fi

read -rp "Proceed? [y/N] " CONFIRM
[[ "$CONFIRM" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }
echo

# ── Step 1: mandate-proxy network ─────────────────────────────────────────────
if docker network inspect mandate-proxy &>/dev/null; then
  echo "[ ok ] mandate-proxy network already exists"
else
  echo "[ .. ] Creating mandate-proxy Docker network..."
  docker network create mandate-proxy
  echo "[ ok ] Created mandate-proxy"
fi

# ── Step 2: Clone repo ────────────────────────────────────────────────────────
REPO_URL=$(git -C "$CAMPAIGN1_DIR" remote get-url origin 2>/dev/null || true)
if [[ -z "$REPO_URL" ]]; then
  read -rp "Repo URL (could not detect automatically from git remote): " REPO_URL
  [[ -n "$REPO_URL" ]] || die "Repo URL cannot be empty."
fi

echo "[ .. ] Cloning $REPO_URL into $CAMPAIGN_DIR..."
git clone "$REPO_URL" "$CAMPAIGN_DIR"
echo "[ ok ] Cloned"

# ── Step 3: Generate secrets and write .env ───────────────────────────────────
echo "[ .. ] Generating secrets..."
POSTGRES_PASSWORD=$(generate_secret)
SECRET_KEY=$(generate_secret)

cat > "$CAMPAIGN_DIR/.env" <<EOF
CAMPAIGN_CONTAINER_NAME=${CONTAINER_NAME}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
SECRET_KEY=${SECRET_KEY}
ADMIN_EMAIL=${ADMIN_EMAIL}
ADMIN_PASSWORD=
EOF

echo "[ ok ] Wrote $CAMPAIGN_DIR/.env"

# ── Step 4: Add site block to Caddyfile ───────────────────────────────────────
# Domains are hardcoded (not env vars) so caddy reload works without restarting
# the Caddy container (which would briefly drop all campaigns).
cat >> "$CADDYFILE" <<EOF

# ── Campaign ${CAMPAIGN_NUM} (${CAMPAIGN_SLUG}) ───────────────────────────────
${CAMPAIGN_DOMAIN} {
	import mandate_proxy ${CONTAINER_NAME}
}
EOF

echo "[ ok ] Added site block to Caddyfile"

# ── Step 5: Start new campaign stack ─────────────────────────────────────────
echo "[ .. ] Starting campaign stack (this will build the image)..."
docker compose \
  -f "$CAMPAIGN_DIR/docker-compose.campaign.yml" \
  -p "$COMPOSE_PROJECT" \
  up -d --build

echo "[ ok ] Campaign stack started"

# ── Step 6: Reload Caddy ─────────────────────────────────────────────────────
echo "[ .. ] Reloading Caddy..."
docker compose -f "$CAMPAIGN1_DIR/docker-compose.yml" \
  exec caddy caddy reload --config /etc/caddy/Caddyfile --force
echo "[ ok ] Caddy reloaded"

# ── Step 7: Verify ────────────────────────────────────────────────────────────
echo
echo "[ .. ] Verifying..."

CONTAINER_RUNNING=$(docker ps --filter "name=^${CONTAINER_NAME}$" --format '{{.Status}}' | head -1)
if [[ "$CONTAINER_RUNNING" == *"Up"* ]]; then
  echo "[ ok ] $CONTAINER_NAME is running ($CONTAINER_RUNNING)"
else
  red "[ !! ] $CONTAINER_NAME does not appear to be running. Check: docker logs $CONTAINER_NAME"
fi

CONNECTED=$(docker network inspect mandate-proxy --format '{{range .Containers}}{{.Name}} {{end}}' 2>/dev/null)
if echo "$CONNECTED" | grep -q "$CONTAINER_NAME"; then
  echo "[ ok ] $CONTAINER_NAME is connected to mandate-proxy"
else
  red "[ !! ] $CONTAINER_NAME is NOT connected to mandate-proxy. Check network config."
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo
green "=== Campaign '${CAMPAIGN_SLUG}' is live ==="
echo
echo "  URL         : https://${CAMPAIGN_DOMAIN}"
echo "  Admin login : ${ADMIN_EMAIL}"
echo "  Password    : changeme  (forced change on first login)"
echo "  Compose dir : ${CAMPAIGN_DIR}"
echo "  Logs        : docker logs -f ${CONTAINER_NAME}"
echo "  Stop        : docker compose -f ${CAMPAIGN_DIR}/docker-compose.campaign.yml -p ${COMPOSE_PROJECT} down"
echo
echo "Caddy will provision a TLS certificate for ${CAMPAIGN_DOMAIN} automatically on first request."
echo "This requires the domain's DNS to already point at this server."
