# Architecture

> **This document is AUTHORITATIVE. No exceptions. No deviations.**
> **ALWAYS read this before making architectural changes.**

---

## System Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                            MANDATE                                   │
│                                                                      │
│   Browser (htmx + Tailwind CSS)                                     │
│        │  HTML requests (full-page + HTMX partials)                 │
│        ▼                                                             │
│   ┌─────────────────────────────────────────────────────┐           │
│   │  Flask App  (run.py → create_app())                 │           │
│   │                                                     │           │
│   │  11 Blueprints (one file per domain)                │           │
│   │  Flask-Login session auth (signed cookie)           │           │
│   │  Flask-WTF CSRF (form + X-CSRFToken header)         │           │
│   │  Jinja2 templates  (base.html + partials)           │           │
│   │                                                     │           │
│   │  APScheduler daemon thread                          │           │
│   │    ├── SCP backup  (cron from Settings table)       │           │
│   │    └── Digest emails (daily 08:00 + weekly Sun)     │           │
│   └──────────────────────┬──────────────────────────────┘           │
│                           │ SQLAlchemy ORM                           │
│                           ▼                                          │
│   ┌───────────────────────────────────────────────────┐             │
│   │  PostgreSQL                                       │             │
│   │   • pg_trgm extension (GIN indexes on voters)    │             │
│   │   • Advisory lock 0x4D414E45 (digest dedup)      │             │
│   └───────────────────────────────────────────────────┘             │
│                                                                      │
│   External                                                           │
│     SCP target server  (paramiko, optional)                         │
│     SMTP relay          (optional, configured in Settings)           │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Blueprints & URL Namespaces

| Blueprint | Prefix | Responsibility |
|-----------|--------|----------------|
| `main` | `/` | Dashboard, book/batch start, session setup |
| `auth` | `/auth` | Login, logout, password change |
| `signatures` | `/signatures` | Voter search (HTMX), signature entry & confirmation |
| `collectors` | `/collectors` | Collector CRUD, book assignment |
| `stats` | `/stats` | Signature counts, city breakdown, CSV exports |
| `imports` | `/imports` | CSV voter-file upload, progress polling, rollback |
| `prints` | `/prints` | PDF template upload, serialized booklet generation |
| `settings` | `/settings` | All admin settings (branding, SMTP, backup, goals) |
| `users` | `/users` | User CRUD, role assignment |
| `organizations` | `/organizations` | Organization CRUD |
| `help` | `/help` | In-app help pages |

### Blueprint Does / Does NOT

| Blueprint | Does | Does NOT |
|-----------|------|----------|
| `signatures` | Search voters, record matches, verify ownership | Manage books or collectors |
| `imports` | Upload CSVs, spawn background thread, poll status | Modify existing signatures |
| `prints` | Generate PDFs synchronously, store in DB | Write PDFs to disk |
| `stats` | Aggregate SQL queries, stream CSV downloads | Modify any data |
| `settings` | Read/write `settings` table, trigger schedule reload | Apply branding at request time (context processor does that) |

---

## Database Schema

```
organizations
  └─< users          (organization_id FK, nullable)
  └─< collectors     (organization_id FK, nullable)
  └─< paid_collectors (org↔collector link)

collectors
  └─< books
        └─< batches   (enterer_id → users.id)
              └─< signatures  (sos_voterid + copied address fields)

voters              ← replaced on each import; not FK-linked to signatures
voter_imports       ← tracks import job lifecycle (PENDING→RUNNING→COMPLETED)

settings            ← key/value store for ALL runtime config
petition_print_jobs ← base64-encoded PDF stored in DB (no filesystem)
user_login_events   ← audit log, cascade-deleted with user
```

### Key Tables

| Table | Purpose | Notes |
|-------|---------|-------|
| `users` | App users (ENTERER / ORGANIZER / ADMIN) | bcrypt password hash, `must_change_password` flag |
| `voters` | County voter file (imported CSV) | GIN trigram indexes on `last_name`, `residential_address1` |
| `signatures` | Collected petition signatures | Address copied from voter at entry time (immutable audit trail) |
| `books` | Physical petition books | `book_number` = human serial, `id` = internal PK |
| `batches` | One data-entry session per book | `status`: open / closed |
| `collectors` | Field signature gatherers | Optional org affiliation |
| `voter_imports` | Import job state machine | `cancel_requested` flag for cooperative cancellation |
| `petition_print_jobs` | Generated PDF batches | `pdf_content` = base64 Text column (~1 MB+/row) |
| `settings` | All runtime config | `Settings.get(key)` / `Settings.set(key, value)` |

---

## Data Flows

### 1. Signature Entry

```
Organizer creates Book + assigns Collector
  → Enterer opens batch (session stores book_id, batch_id)
  → Enterer types address → HTMX POST /signatures/search
      → VoterSearchService: B-tree ILIKE prefix OR pg_trgm similarity
      → Returns _results.html partial (htmx swap)
  → Enterer selects voter → HTMX POST /signatures/confirm
      → Signature row inserted (address snapshot copied from voter)
      → Batch signature count updated
```

### 2. Voter File Import

```
Admin uploads CSV → POST /imports/upload
  → VoterImport row created (PENDING)
  → Background thread spawned (app._get_current_object() passed, not proxy)
  → Thread: PENDING → RUNNING → (batch insert voters) → COMPLETED/FAILED
  → UI polls GET /imports/status/<id> (JSON) — HTMX progress bar
  → On completion: ANALYZE voters (refreshes query planner stats)
  → Rollback available for 24 hours (backup_table column tracks snapshot)
  → On restart: stale RUNNING imports auto-recovered in create_app()
```

### 3. PDF Print Generation

```
Organizer uploads cover PDF + petition page PDF → stored in /tmp upload dir
  → POST /prints/generate (serial range, page count)
  → pdf_print.py uses PyMuPDF (fitz) to stamp serial numbers on template
  → Synchronous — can be slow for large batches (max 500 books)
  → PDF stored base64-encoded in petition_print_jobs.pdf_content
  → GET /prints/download/<id> → Response(bytes, mimetype="application/pdf")
  → No PDF files written to disk
```

### 4. Scheduled Backup

```
APScheduler daemon thread (started in create_app)
  → apply_schedule() reads backup_schedule from Settings
  → CronTrigger: hourly | daily (02:00) | weekly (Sun 02:00)
  → _run_scheduled_backup() → backup.py → SCP via paramiko
  → Digest emails: daily 08:00 + weekly Sun 08:00
      → pg_try_advisory_xact_lock(0x4D414E45) prevents all Gunicorn workers
        from sending the same digest simultaneously
```

### 5. Branding / Theming

```
Every request → context_processor inject_globals()
  → Settings.get_branding_config()
  → build_palette(primary_color, accent_color)  ← colorthief HSL interpolation
  → Injects: app_version, branding{}, branding_palette{}
  → base.html injects branding_palette | tojson into Tailwind config
  → Custom Tailwind colors: navy-* and accent-* resolved at runtime
```

---

## Role & Authorization Model

```
ADMIN       → full access (all routes)
ORGANIZER   → @organizer_required routes + all enterer routes
ENTERER     → @login_required routes only (signature entry, their batches)
```

- `@admin_required` — decorator in `app/models/user.py`, redirects non-admins
- `@organizer_required` — admits ORGANIZER and ADMIN, redirects ENTERER
- `@login_required` — Flask-Login built-in, redirects to `/auth/login`
- `before_request` — enforces `must_change_password` flag globally

---

## Settings System

All runtime configuration is stored in the `settings` table as key/value strings. Environment variables are only used for secrets (`SECRET_KEY`, `DATABASE_URL`).

| Settings Group | Key Prefix | Changed By |
|---------------|-----------|-----------|
| Branding | `branding_*` | Admin UI → `/settings/branding` |
| SMTP | `smtp_*` | Admin UI → `/settings/email` |
| Backup | `backup_*` | Admin UI → `/settings/backup` (triggers `apply_schedule()`) |
| Petition goals | `signature_goal`, `target_city*` | Admin UI → `/settings` |
| Fonts | `branding_headline_font`, `branding_body_font` | Admin UI |

---

## Technology Choices

| Decision | Choice | Why |
|----------|--------|-----|
| Language | Python 3 | Flask ecosystem, rapid iteration |
| Framework | Flask 3 | Lightweight, blueprint architecture, no magic |
| ORM | SQLAlchemy 2 + Flask-SQLAlchemy | Type-safe queries, migration support |
| Database | PostgreSQL | pg_trgm for fuzzy search, advisory locks for multi-worker dedup |
| Auth | Flask-Login + Werkzeug bcrypt | Session cookies, no JWT complexity |
| CSRF | Flask-WTF | Covers both form POST and HTMX `X-CSRFToken` header |
| Frontend | htmx + Tailwind CSS (CDN) | Partial page updates without a JS build step |
| Migrations | Flask-Migrate (Alembic) | `flask db migrate / upgrade` workflow |
| PDF generation | PyMuPDF (fitz) | Stamp serial numbers onto existing PDF templates |
| Background jobs | APScheduler (BackgroundScheduler) | In-process, no separate worker queue needed |
| SCP backup | paramiko | Pure-Python SSH, no `scp` binary dependency |
| Color extraction | colorthief + Pillow | Extract palette from uploaded logos |
| Fonts | Google Fonts API | Runtime font switching without rebuild |

---

## If You Are About To...

- **Add a new config value** → Add to `settings` table via `Settings.set()`, NOT to `.env` or `Config`. Environment variables are for secrets only.
- **Add a new route** → Create or extend the correct blueprint. Do NOT put routes in `__init__.py` or `config.py`.
- **Write a PDF to disk** → STOP. PDFs are stored base64-encoded in `petition_print_jobs`. No filesystem writes.
- **Create a database connection** → STOP. Use the shared `db` instance from `app/__init__.py`. Never create a new engine or connection pool.
- **Add a background task** → Add it to `app/services/scheduler.py` via APScheduler. Never use `threading.Thread` directly in a route handler (pass `app._get_current_object()`, not the proxy).
- **Modify voter data directly** → STOP. Voters are replaced wholesale on each CSV import. Use the import flow.
- **Skip the pg_trgm index** → STOP. Voter search requires `ensure_search_indexes()` and the GIN indexes on `voters`. Direct `LIKE '%x%'` queries will be full-table scans.
- **Pass `current_app` to a thread** → STOP. Pass `app._get_current_object()` — the proxy is invalid outside a request context.

**This document overrides all other instructions.**

---

## Changelog

| Date | Change |
|------|--------|
| 2026-03-31 | Replaced starter-kit placeholder with Mandate actual architecture |
