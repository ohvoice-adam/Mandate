# Infrastructure

> **Always check this file before making infrastructure or deployment decisions.**

---

## Stack

| Component | Technology | Notes |
|-----------|------------|-------|
| Web app | Flask 3.0 / Gunicorn | `app:create_app()` factory |
| Database | PostgreSQL 18 | `pg_trgm` extension required |
| Reverse proxy / TLS | Caddy 2 | Auto HTTPS via Let's Encrypt |
| Container runtime | Docker + Compose v2 | |
| Scheduler | APScheduler (in-process) | Backup jobs, digest emails |

---

## Docker Architecture

### Single Campaign

```
Internet
   │ :80/:443
 Caddy (caddy:2-alpine)
   │ :8000
 Flask/Gunicorn (web)
   │ :5432
 PostgreSQL 18 (db)
```

All three services run in the same `docker compose` project. Caddy provisions TLS automatically via ACME HTTP-01 challenge.

### Multiple Campaigns

```
Internet
   │ :80/:443
 Caddy ─── mandate-proxy (external Docker network) ───┬─── mandate_web_1:8000
                                                        ├─── mandate_web_2:8000
                                                        └─── mandate_web_N:8000

Each mandate_web_N talks to its own db via an isolated internal network.
```

Campaign 1's compose file contains Caddy and binds ports 80/443. Campaigns 2+ use `docker-compose.campaign.yml` (no Caddy, no port bindings). All web containers join `mandate-proxy` so Caddy can route to them by container name.

---

## Environment Variables

### Campaign 1 (`.env`)

| Variable | Required | Purpose |
|----------|----------|---------|
| `CAMPAIGN1_DOMAIN` | Yes | Domain for Caddy TLS — campaign 1 |
| `POSTGRES_PASSWORD` | Yes | PostgreSQL password |
| `SECRET_KEY` | Yes | Flask session signing |
| `ADMIN_EMAIL` | No | Bootstrap admin account (default: `admin@example.com`) |
| `ADMIN_PASSWORD` | No | Bootstrap admin password (default: `changeme`) |
| `SMTP_HOST` | No | SMTP server — shared default for all campaigns |
| `SMTP_PORT` | No | SMTP port (default: `587`) |
| `SMTP_USER` | No | SMTP username |
| `SMTP_FROM_EMAIL` | No | Sender address |
| `SMTP_PASSWORD` | No | SMTP password |
| `SMTP_USE_TLS` | No | `true` / `false` (default: `true`) |

### Additional Campaigns (`.env.campaign.example`)

| Variable | Required | Purpose |
|----------|----------|---------|
| `CAMPAIGN_CONTAINER_NAME` | Yes | e.g. `mandate_web_2` — must match Caddyfile entry |
| `POSTGRES_PASSWORD` | Yes | Must be unique per campaign |
| `SECRET_KEY` | Yes | Must be unique per campaign |
| `ADMIN_EMAIL` | No | Bootstrap admin for this campaign |
| `ADMIN_PASSWORD` | No | Bootstrap admin password |
| `SMTP_*` | No | Per-campaign SMTP override (inherits from campaign 1 if unset) |

---

## Volumes

| Volume | Content | Lives in |
|--------|---------|----------|
| `postgres_data` | PostgreSQL data directory | Campaign compose project |
| `uploads` | Temporary file uploads | Campaign compose project |
| `caddy_data` | TLS certificates, ACME state | Campaign 1 compose project |
| `caddy_config` | Caddy runtime config | Campaign 1 compose project |

Volumes are Docker named volumes, prefixed with the compose project name (e.g. `mandate_postgres_data`, `mandate-campaign2_postgres_data`). They persist across `docker compose down` — only `docker compose down -v` removes them.

---

## Caddyfile

The Caddyfile (`/Caddyfile` in repo, mounted at `/etc/caddy/Caddyfile`) has three parts:

1. **Global block** — `read_body 10m` timeout for large voter file uploads
2. **`(mandate_proxy)` snippet** — shared proxy config (1 GB body limit, timeouts, flush) imported by every campaign site block
3. **Site blocks** — one per campaign, each importing the snippet and naming its web container

Campaign 1's domain uses an env var (`{$CAMPAIGN1_DOMAIN}`); additional campaign domains are hardcoded directly by `new-campaign.sh` (avoids needing a Caddy container restart to pick up new env vars).

> **Inode drift:** Docker bind mounts track inodes, not filenames. If the Caddyfile is replaced rather than edited in-place (`git pull`, editor temp-file rename), `caddy reload` reads stale content. `new-campaign.sh` detects this and runs `docker compose restart caddy` automatically.

---

## Campaign Management Scripts

| Script | Purpose |
|--------|---------|
| `scripts/new-campaign.sh` | Interactive setup for a new campaign — clones repo, generates secrets, updates Caddyfile, starts stack |
| `scripts/update-campaigns.sh` | Pulls latest code and rebuilds `web` container for all running campaigns |
| `scripts/remove-campaign.sh` | Removes a campaign — Caddyfile cleanup, optional DB export, container/volume/directory removal |

All scripts are self-documenting — run without arguments for usage.

---

## Deployment

### Initial Setup

```bash
git clone <repo> mandate && cd mandate
cp .env.example .env && nano .env
docker network create mandate-proxy
docker compose up -d
```

### Update Campaign 1

```bash
git pull
docker compose up -d --no-deps web
```

### Update All Campaigns

```bash
bash scripts/update-campaigns.sh
```

### Rollback

Mandate uses database migrations (Alembic). There is no automated rollback — to revert:

1. `docker compose down`
2. `git checkout <previous-tag>`
3. Restore database from backup if migration was destructive
4. `docker compose up -d`

---

## Monitoring

| What to watch | How |
|---------------|-----|
| App logs | `docker compose logs -f web` |
| Caddy / TLS | `docker compose logs -f caddy` |
| Database | `docker compose exec db psql -U petition -d mandate` |
| All campaigns | `docker ps \| grep mandate_web` |
| Network | `docker network inspect mandate-proxy` |

---

## Changelog

| Date | Change |
|------|--------|
| 2026-04-19 | Rewrote from placeholder — reflects Docker/Caddy/multi-campaign infrastructure |
| 2026-04-17 | Multi-campaign Docker/Caddy setup introduced |
