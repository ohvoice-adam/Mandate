# Mandate — Architecture Overview

## What the App Does

Mandate is a Flask web app for managing petition signature campaigns. Collectors gather physical petition books from registered voters; data enterers type each signature into the system; the app verifies each signer against an imported voter file and tracks totals toward a signature goal.

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

### Core Entities

**Voter** — imported from the county voter file CSV. Used only for lookup during signature entry. Has GIN trigram indexes on `residential_address1` and `last_name` for fuzzy search.

**Signature** — one row per petition entry. Stores a copy of the voter's address at time of entry (immutable audit trail). `matched=True` means the signer was identified against a voter record; `matched=False` with an address means address-only; `matched=False` with no address means no match.

**Book → Batch → Signature** — the physical hierarchy. A Book is a petition booklet assigned to a Collector. A Batch is one data-entry session (one enterer opening one book). Signatures belong to both a Book and a Batch.

**Settings** — every row is a `(key, value)` string pair. `Settings.get(key, default)` and `Settings.set(key, value)` are the primary interface. Higher-level helpers (`get_branding_config()`, `get_smtp_config()`, etc.) group related keys into dicts.

---

## Authentication & Authorization (`app/models/user.py`)

- **Flask-Login** manages session state. `@login_required` redirects unauthenticated users to `/auth/login`.
- **`@login_manager.user_loader`** — called on every request; fetches the User by the ID stored in the signed session cookie.
- **Three roles**: `ENTERER`, `ORGANIZER`, `ADMIN`. Access control uses two decorators applied to route functions:
  - `@admin_required` — admin only
  - `@organizer_required` — organizer or admin

### Invite / Password Reset Flow

Both use **itsdangerous `URLSafeTimedSerializer`** tokens signed with `SECRET_KEY`:

- Payload contains `{"id": user_id, "ph": last_8_chars_of_hash}`.
- The `ph` fingerprint invalidates the token once the password changes — single-use without a DB table.
- Different `salt=` values prevent a reset token from being used on the invite endpoint.
- `_external=True` in `url_for()` generates full `https://host/path` URLs for email links.

---

## Signature Entry Flow

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

---

## Voter Import (`app/services/voter_import.py`)

Imports run in a **background thread** so the HTTP request returns immediately.

1. Route uploads CSV/ZIP → `VoterImportService.handle_upload()` saves file, creates a `VoterImport` row (status: `PENDING`), calls `start_import()`.
2. Thread runs `_run_import()` inside `with app.app_context()` — required because threads have no Flask request context.
3. Progress is written to `voter_imports.processed_rows` every 1,000 rows.
4. UI polls `GET /imports/<id>/status` (JSON) via HTMX to update the progress bar.
5. **Cancellation**: web request sets `cancel_requested=True` in DB *and* flips the in-memory `_running_imports[id]["cancel"]` flag; the thread checks both on each batch boundary.
6. **Rollback**: completed imports within 24 hours can be reversed. The service stores the pre-import max voter ID and re-deletes rows added since then.

---

## Background Jobs (`app/services/scheduler.py`)

Uses **APScheduler** `BackgroundScheduler` (daemon thread, same process).

Every job receives the real `app` object (not the `current_app` proxy) and pushes `app.app_context()` for DB access.

| Job | Trigger | What it does |
|---|---|---|
| `scheduled_backup` | Cron from Settings (`hourly`/`daily`/`weekly`) | `pg_dump` → SFTP upload |
| `backup_digest_daily` | Daily at 08:00 UTC | Sends batched backup-success email |
| `backup_digest_weekly` | Sunday at 08:00 UTC | Sends weekly digest |

A **PostgreSQL advisory lock** (`pg_try_advisory_xact_lock`) prevents duplicate digest emails when all Gunicorn workers fire the same job simultaneously. The lock is transaction-scoped — released automatically on commit.

---

## Backup (`app/services/backup.py`)

1. Runs `pg_dump` as a subprocess, writing to a temp file.
2. Uploads via **paramiko SFTP** using an SSH private key stored (base64-encoded) in the `settings` table.
3. On success/failure, writes `backup_last_run` and `backup_last_status` to Settings and optionally queues a digest email.

The `download_backup` route in `routes/settings.py` also runs `pg_dump` locally and streams the result to the browser using `send_file()` + `after_this_request()` for temp-file cleanup.

---

## PDF Generation (`app/services/pdf_print.py`)

Uses **PyMuPDF** (`fitz`) to stamp serial numbers onto uploaded PDF templates.

1. Admin uploads a cover PDF and a petition-page PDF (stored base64-encoded in `settings`).
2. `generate_petition_pdf(cover_bytes, petition_bytes, start, end)` iterates the serial range, stamps each number onto the cover, and concatenates cover + petition pages.
3. The result is stored as a `PetitionPrintJob` row (`pdf_content` column, base64-encoded text).
4. Download route reads the row, decodes, and streams with `Response(bytes, mimetype="application/pdf")`.

Generation is synchronous. Maximum 500 books per run.

---

## Branding & Theming (`app/services/branding.py`, `app/__init__.py`)

All branding is database-driven and applied at request time:

1. The **context processor** reads `Settings.get_branding_config()` on every request.
2. `build_palette(primary_hex, accent_hex)` generates an 11-shade Tailwind color scale for each color by fixing hue/saturation and interpolating lightness.
3. `base.html` injects the palette into the Tailwind config via `{{ branding_palette | tojson }}`, allowing Tailwind's `navy-*` and `accent-*` classes to use the DB-stored colors.
4. When a logo is uploaded, `extract_colors_from_image()` uses **colorthief** to extract the dominant color and auto-populates the primary color.

---

## HTMX Pattern

Most interactive UI updates use **HTMX** rather than full page reloads:

- Forms post to Flask routes that return **HTML fragments** (templates named `_something.html`).
- HTMX swaps the fragment into the DOM.
- JSON polling (import progress, connection tests) uses `hx-get` + `jsonify()` responses.
- CSRF tokens are sent via the `X-CSRFToken` request header (configured in `app/__init__.py`).

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
