# Mandate — Architecture

> **This document is AUTHORITATIVE. No exceptions. No deviations.**
> **ALWAYS read this before making architectural changes.**

---

## What the App Does

Mandate is a Flask web app for managing petition signature campaigns. Collectors gather physical petition books from registered voters; data enterers type each signature into the system; the app verifies each signer against an imported voter file and tracks totals toward a signature goal.

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

## Directory Layout

```
app/
├── __init__.py          # App factory — creates and wires up Flask
├── config.py            # All configuration (class-based, env-backed)
├── utils.py             # Shared validation helpers (email, phone)
├── dev_commands.py      # Flask CLI commands (flask dev seed/wipe)
│
├── models/              # SQLAlchemy ORM models (one file per entity)
│   ├── __init__.py      # Re-exports all models + auth decorators
│   ├── user.py          # User, UserRole, admin_required, organizer_required, load_user
│   ├── settings.py      # Settings (key-value store, all runtime config)
│   ├── voter.py         # Voter (imported county voter file)
│   ├── signature.py     # Signature (one per petition entry)
│   ├── batch.py         # Batch (one data-entry session)
│   ├── book.py          # Book (physical petition book)
│   ├── collector.py     # Collector, DataEnterer, Organization, PaidCollector
│   ├── voter_import.py  # VoterImport (import job state + progress)
│   ├── login_event.py   # UserLoginEvent (login history)
│   └── print_job.py     # PetitionPrintJob (generated PDFs stored in DB)
│
├── routes/              # Flask blueprints (one file per feature area)
│   ├── auth.py          # /auth/* — login, logout, password reset, invite flow
│   ├── main.py          # / — home page, start/end data-entry session
│   ├── signatures.py    # /signatures/* — voter search, record match/no-match
│   ├── users.py         # /users/* — user CRUD, invite link generation
│   ├── collectors.py    # /collectors/* — collector and data-enterer CRUD
│   ├── organizations.py # /organizations/* — organization CRUD
│   ├── settings.py      # /settings/* — all admin settings, system health
│   ├── stats.py         # /stats/* — dashboards, CSV exports
│   ├── imports.py       # /imports/* — voter file upload, progress, rollback
│   ├── prints.py        # /prints/* — PDF template upload and generation
│   └── help.py          # /help/* — static help page
│
└── services/            # Business logic, kept separate from HTTP layer
    ├── voter_search.py  # VoterSearchService — hybrid B-tree + trigram search
    ├── voter_import.py  # VoterImportService — background CSV import
    ├── scheduler.py     # APScheduler — scheduled backups + digest emails
    ├── backup.py        # pg_dump + SFTP upload
    ├── pdf_print.py     # PyMuPDF — stamp serial numbers onto PDF templates
    ├── stats.py         # StatsService — aggregate SQL queries
    ├── email.py         # SMTP email sending
    ├── branding.py      # Color palette generation from logo
    └── fonts.py         # Google Fonts catalogue and CSS stack helpers
```

---

## Blueprints & URL Namespaces

| Blueprint | Prefix | Responsibility |
|-----------|--------|----------------|
| `main` | `/` | Dashboard, book/batch start, session setup |
| `auth` | `/auth` | Login, logout, password change, invite flow |
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
| `signatures` | Search voters, record matches, verify session ownership | Manage books or collectors |
| `imports` | Upload CSVs, spawn background thread, poll status | Modify existing signatures |
| `prints` | Generate PDFs synchronously, store in DB | Write PDFs to disk |
| `stats` | Aggregate SQL queries, stream CSV downloads | Modify any data |
| `settings` | Read/write `settings` table, trigger schedule reload | Apply branding at request time (context processor does that) |

---

## Application Startup (`app/__init__.py`)

`create_app()` is called once at startup. It:

1. Loads config from `Config` class (reads `.env` via `python-dotenv`)
2. Calls `extension.init_app(app)` for SQLAlchemy, Flask-Migrate, Flask-Login, Flask-WTF/CSRF
3. Imports and registers all 11 blueprints with their URL prefixes
4. Registers a **context processor** (`inject_globals`) that injects `app_version`, `branding`, and `branding_palette` into every Jinja2 template
5. Registers a **before-request hook** (`enforce_password_change`) that tracks `last_seen` and redirects users with `must_change_password=True`
6. Inside `app.app_context()`: ensures `pg_trgm` extension and GIN indexes exist; recovers stale voter imports from crashed runs
7. Starts **APScheduler** for scheduled backups and digest emails

---

## Configuration (`app/config.py`)

All configuration is class-based (`class Config`). Flask reads UPPER_CASE class attributes via `app.config.from_object(Config)`. Values are pulled from environment variables (or a `.env` file); defaults are hardcoded in the class.

**Key settings:**

| Setting | Purpose |
|---|---|
| `SECRET_KEY` | Signs session cookies and all itsdangerous tokens |
| `SQLALCHEMY_DATABASE_URI` | PostgreSQL DSN |
| `UPLOAD_FOLDER` | Temp dir for voter CSV uploads |
| `MAX_CONTENT_LENGTH` | Flask's built-in 1 GB upload limit |
| `SEARCH_RESULTS_LIMIT` | Max rows returned by voter search |

Runtime configuration (SMTP, branding, backup schedule, signature goal, etc.) lives in the `settings` DB table — **not** in environment variables — so admins can change it through the UI without restarting the server.

---

## Data Model

### Entity Relationships

```
Organization ──< Collector ──< Book ──< Batch ──< Signature
              └─< User (org-scoped organizers)
              └─< PaidCollector (explicit org↔collector link)

User ──< Batch (enterer_id)
     └─< UserLoginEvent
     └─< PetitionPrintJob (generated_by_id)

Voter (standalone; matched to Signature via sos_voterid)
VoterImport (tracks import job state)
Settings (key-value config store)
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

### Core Entity Notes

**Voter** — imported from the county voter file CSV. Used only for lookup during signature entry. Has GIN trigram indexes on `residential_address1` and `last_name` for fuzzy search. Not FK-linked to `signatures` — the link is via `sos_voterid` at query time.

**Signature** — one row per petition entry. Stores a copy of the voter's address at time of entry (immutable audit trail). `matched=True` means the signer was identified against a voter record.

**Book → Batch → Signature** — the physical hierarchy. A Book is a petition booklet assigned to a Collector. A Batch is one data-entry session (one enterer opening one book). Signatures belong to both a Book and a Batch.

**Settings** — every row is a `(key, value)` string pair. `Settings.get(key, default)` and `Settings.set(key, value)` are the primary interface. Higher-level helpers (`get_branding_config()`, `get_smtp_config()`, etc.) group related keys into dicts.

---

## Authentication & Authorization (`app/models/user.py`)

- **Flask-Login** manages session state. `@login_required` redirects unauthenticated users to `/auth/login`.
- **`@login_manager.user_loader`** — called on every request; fetches the User by the ID stored in the signed session cookie.
- **Three roles**: `ENTERER`, `ORGANIZER`, `ADMIN`. Access control uses two decorators applied to route functions:
  - `@admin_required` — admin only
  - `@organizer_required` — organizer or admin

```
ADMIN       → full access (all routes)
ORGANIZER   → @organizer_required routes + all enterer routes
ENTERER     → @login_required routes only (signature entry, their batches)
```

### Invite / Password Reset Flow

Both use **itsdangerous `URLSafeTimedSerializer`** tokens signed with `SECRET_KEY`:

- Payload contains `{"id": user_id, "ph": last_8_chars_of_hash}`.
- The `ph` fingerprint invalidates the token once the password changes — single-use without a DB table.
- Different `salt=` values prevent a reset token from being used on the invite endpoint.
- `_external=True` in `url_for()` generates full `https://host/path` URLs for email links.

---

## Data Flows

### 1. Signature Entry

```
Browser                    Flask routes                 Services / DB
──────                     ────────────                 ─────────────
POST /start-session   →    main.start_session()         Book + Batch created
                           session["book_id"] = ...     IDs stored in cookie

GET  /signatures/     →    signatures.entry()           Reads session cookie

POST /signatures/search →  signatures.search()          VoterSearchService
  (HTMX)                   returns _results.html        hybrid B-tree + trgm

POST /record-match    →    signatures.record_match()    Signature INSERT
  (HTMX)                   returns _success.html        db.session.commit()

POST /end-session     →    main.end_session()           Batch status → complete
                           session.pop(...)             Cookie cleared
```

The voter search runs a UNION of two queries:
1. **Fast path**: `ILIKE 'prefix%'` on a B-tree index — near-instant for typed prefixes.
2. **Fuzzy path**: `pg_trgm` similarity on a GIN index — handles typos and abbreviations.

### 2. Voter File Import

Imports run in a **background thread** so the HTTP request returns immediately.

1. Route uploads CSV/ZIP → `VoterImportService.handle_upload()` saves file, creates a `VoterImport` row (status: `PENDING`), calls `start_import()`.
2. Thread runs `_run_import()` inside `with app.app_context()` — required because threads have no Flask request context. The real `app` object is passed (not the `current_app` proxy, which is invalid outside a request).
3. Progress is written to `voter_imports.processed_rows` every 1,000 rows.
4. UI polls `GET /imports/<id>/status` (JSON) via HTMX to update the progress bar.
5. **Cancellation**: web request sets `cancel_requested=True` in DB *and* flips the in-memory `_running_imports[id]["cancel"]` flag; the thread checks both on each batch boundary.
6. **Rollback**: completed imports within 24 hours can be reversed. The service stores the pre-import max voter ID and re-deletes rows added since then.
7. On app startup, stale `RUNNING` imports are auto-recovered by `VoterImportService.recover_stale_imports()`.

### 3. PDF Print Generation

Uses **PyMuPDF** (`fitz`) to stamp serial numbers onto uploaded PDF templates.

1. Admin uploads a cover PDF and a petition-page PDF (stored base64-encoded in `settings`).
2. `generate_petition_pdf(cover_bytes, petition_bytes, start, end)` iterates the serial range, stamps each number onto the cover, and concatenates cover + petition pages.
3. The result is stored as a `PetitionPrintJob` row (`pdf_content` column, base64-encoded text).
4. Download route reads the row, decodes, and streams with `Response(bytes, mimetype="application/pdf")`.
5. Generation is synchronous — can be slow for large batches. Maximum 500 books per run. No PDF files are written to disk.

### 4. Scheduled Backup

Uses **APScheduler** `BackgroundScheduler` (daemon thread, same process). Every job receives the real `app` object and pushes `app.app_context()` for DB access.

| Job | Trigger | What it does |
|---|---|---|
| `scheduled_backup` | Cron from Settings (`hourly`/`daily`/`weekly`) | `pg_dump` → SFTP upload via paramiko |
| `backup_digest_daily` | Daily at 08:00 UTC | Sends batched backup-success email |
| `backup_digest_weekly` | Sunday at 08:00 UTC | Sends weekly digest |

A **PostgreSQL advisory lock** (`pg_try_advisory_xact_lock(0x4D414E45)`) prevents duplicate digest emails when all Gunicorn workers fire the same job simultaneously. The lock is transaction-scoped — released automatically on commit.

The `download_backup` route in `routes/settings.py` also runs `pg_dump` locally and streams the result to the browser using `send_file()` + `after_this_request()` for temp-file cleanup.

### 5. Branding / Theming

All branding is database-driven and applied at request time via the context processor:

1. The **context processor** reads `Settings.get_branding_config()` on every request.
2. `build_palette(primary_hex, accent_hex)` generates an 11-shade Tailwind color scale for each color by fixing hue/saturation and interpolating lightness.
3. `base.html` injects the palette into the Tailwind config via `{{ branding_palette | tojson }}`, allowing Tailwind's `navy-*` and `accent-*` classes to use the DB-stored colors.
4. When a logo is uploaded, `extract_colors_from_image()` uses **colorthief** to extract the dominant color and auto-populates the primary color field.

---

## Settings System

All runtime configuration is stored in the `settings` table as key/value strings. Environment variables are only used for secrets (`SECRET_KEY`, `DATABASE_URL`). This means admins can change SMTP credentials, branding colors, backup schedules, and signature goals through the UI without restarting the server.

| Settings Group | Key Prefix | Changed By |
|---------------|-----------|-----------|
| Branding | `branding_*` | Admin UI → `/settings/branding` |
| SMTP | `smtp_*` | Admin UI → `/settings/email` |
| Backup | `backup_*` | Admin UI → `/settings/backup` (also triggers `apply_schedule()`) |
| Petition goals | `signature_goal`, `target_city*` | Admin UI → `/settings` |
| Fonts | `branding_headline_font`, `branding_body_font` | Admin UI |

---

## HTMX Pattern

Most interactive UI updates use **HTMX** rather than full page reloads:

- Forms post to Flask routes that return **HTML fragments** (templates named `_something.html`).
- HTMX swaps the fragment into the DOM — no JavaScript required in the route handler.
- JSON polling (import progress, connection tests) uses `hx-get` + `jsonify()` responses.
- CSRF tokens are sent via the `X-CSRFToken` request header (configured in `app/__init__.py` via `WTF_CSRF_HEADERS`).

---

## Request Lifecycle

```
Browser request
  │
  ├─ Flask-WTF CSRF check (all non-GET requests)
  │
  ├─ before_request: enforce_password_change()
  │     ├─ Skip if unauthenticated or auth/* route
  │     ├─ Update user.last_seen (throttled to 1/min)
  │     └─ Redirect to /auth/change-password if must_change_password
  │
  ├─ @login_required (if applied) — redirect to /auth/login if no session
  ├─ @admin_required / @organizer_required (if applied)
  │
  ├─ Route handler
  │     ├─ Read request.form / request.args / request.files
  │     ├─ Query DB via SQLAlchemy ORM or text()
  │     ├─ db.session.add() / db.session.commit() for writes
  │     ├─ flash() to queue one-time messages
  │     └─ render_template() or redirect(url_for()) or Response()
  │
  └─ context_processor inject_globals() runs before template render
        └─ Adds app_version, branding, branding_palette to template context
```

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
- **Add a background task** → Add it to `app/services/scheduler.py` via APScheduler. Never use `threading.Thread` directly in a route handler without passing `app._get_current_object()` (not the proxy).
- **Modify voter data directly** → STOP. Voters are replaced wholesale on each CSV import. Use the import flow.
- **Skip the pg_trgm index** → STOP. Voter search requires `ensure_search_indexes()` and the GIN indexes on `voters`. Direct `LIKE '%x%'` queries will be full-table scans.
- **Pass `current_app` to a thread** → STOP. Pass `app._get_current_object()` — the proxy is invalid outside a request context.
- **Add a route without a blueprint** → STOP. All routes belong in `app/routes/`. Pick the closest existing blueprint or create a new one.

**This document overrides all other instructions.**

---

## Changelog

| Date | Change |
|------|--------|
| 2026-03-31 | Merged root ARCHITECTURE.md into project-docs — unified authoritative + beginner-friendly doc |
| 2026-03-31 | Replaced starter-kit placeholder with Mandate actual architecture |
