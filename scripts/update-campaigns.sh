#!/usr/bin/env bash
# update-campaigns.sh — Pull latest code and rebuild all running campaign stacks.
#
# Finds every campaign by looking for containers named mandate_web_* and reads
# their working directory from the Docker Compose label — no registry needed.
#
# Usage:
#   scripts/update-campaigns.sh            # update all campaigns
#   scripts/update-campaigns.sh --dry-run  # show what would be updated

set -euo pipefail

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
dim()   { printf '\033[2m%s\033[0m\n' "$*"; }

FAILED=()

update_campaign() {
  local container="$1"
  local work_dir compose_file project_name

  # Docker Compose sets this label on every container it manages
  work_dir=$(docker inspect "$container" \
    --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' 2>/dev/null)
  project_name=$(docker inspect "$container" \
    --format '{{index .Config.Labels "com.docker.compose.project"}}' 2>/dev/null)

  if [[ -z "$work_dir" || ! -d "$work_dir" ]]; then
    red "  [$container] Cannot find working directory — skipping"
    FAILED+=("$container")
    return
  fi

  # Campaign 1 uses docker-compose.yml; campaigns 2+ use docker-compose.campaign.yml
  if [[ -f "$work_dir/docker-compose.campaign.yml" && "$container" != "mandate_web_1" ]]; then
    compose_file="$work_dir/docker-compose.campaign.yml"
  else
    compose_file="$work_dir/docker-compose.yml"
  fi

  echo
  bold "[$container]"
  dim  "  directory : $work_dir"
  dim  "  compose   : $(basename "$compose_file")"
  dim  "  project   : $project_name"

  if $DRY_RUN; then
    dim "  (dry run — no changes made)"
    return
  fi

  # Pull latest code
  echo "  Pulling latest code..."
  if ! git -C "$work_dir" pull --ff-only 2>&1 | sed 's/^/  /'; then
    red "  git pull failed — skipping rebuild to avoid deploying a broken state"
    FAILED+=("$container")
    return
  fi

  # Rebuild and restart (--build forces image rebuild; --no-deps avoids restarting db)
  echo "  Rebuilding and restarting web container..."
  if ! docker compose -f "$compose_file" -p "$project_name" \
      up -d --build --no-deps web 2>&1 | sed 's/^/  /'; then
    red "  docker compose up failed"
    FAILED+=("$container")
    return
  fi

  green "  Done"
}

# ── Main ──────────────────────────────────────────────────────────────────────
bold "=== Mandate — Update All Campaigns ==="
$DRY_RUN && echo "(dry run)"

# Find all mandate_web_* containers (running or stopped)
mapfile -t CONTAINERS < <(
  docker ps -a --format '{{.Names}}' | grep '^mandate_web_' | sort -V
)

if [[ ${#CONTAINERS[@]} -eq 0 ]]; then
  red "No mandate_web_* containers found. Are any campaigns running?"
  exit 1
fi

echo "Found ${#CONTAINERS[@]} campaign(s): ${CONTAINERS[*]}"

for container in "${CONTAINERS[@]}"; do
  update_campaign "$container"
done

# ── Summary ───────────────────────────────────────────────────────────────────
echo
if [[ ${#FAILED[@]} -eq 0 ]]; then
  green "=== All campaigns updated successfully ==="
else
  red "=== Completed with errors ==="
  red "Failed: ${FAILED[*]}"
  echo "Check the output above for details. Successful campaigns are already running the new version."
  exit 1
fi
