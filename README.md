# Mandate

A Flask web application for validating Ohio election petition signatures against a Secretary of State voter file. Data entry staff verify petition signatures in real time, and organizers track progress, quality, and collector performance.

## Features

### Signature Entry & Verification
- **Fast address search** — PostgreSQL prefix + trigram (`pg_trgm`) matching, sub-100ms on 500K+ records
- **Three-way matching** — Person Match, Address Only, No Match
- **Keyboard shortcuts** — full keyboard workflow: `↓/↑` navigate results, `Enter`/`m` person match, `a` address-only, `n` no-match, `u` undo last entry, `Esc` clear
- **Undo last entry** — removes the most recent signature in the current session
- **Duplicate detection** — warns when the same voter appears in the same batch (red) or a different book (yellow)

### Session & Book Management
- **Batch tracking** — each entry session is tracked as a `Batch` with `open`/`complete` status
- **Open batch warning** — alerts when starting a new session for a book with an unfinished batch
- **Date validation** — check-in date is validated to be ≥ check-out date

### Statistics & Reporting
- **Progress dashboard** — unique verified signatures vs. goal, with progress bar
- **Collector quality** — per-collector match rate, unmatched rate, and cross-book duplicate rate
- **Enterer performance** — per-enterer totals, match %, unmatched %
- **Organization stats** — breakdown by collecting organization
- **Books view** — per-book validity rates, sortable by book number, entry time, or last activity
- **CSV exports** — download matched signatures or cross-book duplicates, filterable by date range and collector

### Administration
- **User management** — three roles: Data Enterer, Organizer, Admin; accounts can be deactivated
- **Voter file import** — web-based CSV import with progress tracking, county-duplicate warning, and 24-hour rollback window
- **Branding** — org name, logo upload with auto-extracted color palette, custom fonts (headline + body), white-label mode
- **SMTP email** — configurable email for password resets and backup notifications
- **SCP backup** — scheduled database backups via SFTP with per-schedule notifications
- **Settings export/import** — download/restore all non-sensitive settings as JSON
- **System health dashboard** — active users with last signature timestamp, DB stats, voter counts, 24h activity chart, backup status
- **PDF petition printing** — stamp serial numbers onto uploaded cover/petition PDF templates; serial range overlap protection

## Tech Stack

- **Backend**: Flask 3.0, SQLAlchemy 2.0, Flask-Migrate (Alembic), APScheduler
- **Database**: PostgreSQL with `pg_trgm` extension
- **Frontend**: Jinja2, HTMX, Tailwind CSS (CDN, runtime-configured palette)
- **Auth**: Flask-Login
- **PDF**: PyMuPDF (fitz)
- **Deployment**: Docker + Caddy (TLS)

## Quick Start (Docker)

```bash
cp .env.example .env
# Edit .env: set CAMPAIGN1_DOMAIN, POSTGRES_PASSWORD, SECRET_KEY, ADMIN_EMAIL

docker network create mandate-proxy
docker compose up -d
```

On first start the entrypoint creates the database schema and seeds the admin user. On subsequent starts it runs `flask db upgrade` automatically.

Open `https://your-domain` and sign in with your `ADMIN_EMAIL`.

See [DEPLOYMENT.md](DEPLOYMENT.md) for full deployment instructions, including running multiple campaigns on the same server.

## Development Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Set DATABASE_URL, SECRET_KEY

flask db upgrade
python run.py
```

Default dev credentials after seeding: `organizer@dev.example` / `enterer@dev.example`, password: `devpassword`

```bash
flask dev seed [--voters 500] [--books 25]   # populate fake data
flask dev wipe [--yes]                        # remove fake data
```

## Environment Variables

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Flask session signing |
| `DATABASE_URL` | PostgreSQL DSN (default: `postgresql://localhost:5432/mandate`) |
| `UPLOAD_FOLDER` | Temp upload dir (default: `/tmp/petition-qc-uploads`) |
| `ADMIN_EMAIL` | Seeded admin account email (Docker only) |
| `ADMIN_PASSWORD` | Seeded admin account password (Docker only) |
| `POSTGRES_PASSWORD` | PostgreSQL password (Docker only) |
| `CAMPAIGN1_DOMAIN` | Public domain for Caddy TLS — campaign 1 (Docker only) |
| `SMTP_HOST` | SMTP server hostname (optional — can also be set via admin UI) |
| `SMTP_PORT` | SMTP port, default `587` |
| `SMTP_USER` | SMTP username |
| `SMTP_FROM_EMAIL` | Sender address |
| `SMTP_PASSWORD` | SMTP password |
| `SMTP_USE_TLS` | `true` or `false`, default `true` |

SMTP env vars act as defaults for all campaign instances. Settings configured via the admin UI take precedence.

## Application Settings (in-app)

Most runtime configuration is stored in the `settings` DB table and managed via **Settings** (admin only). SMTP can alternatively be pre-configured via env vars (see above).

| Setting | Description |
|---|---|
| Target city | City name used for voter eligibility filtering |
| Signature goal | Target unique signature count for the progress bar |
| Branding | Org name, logo, colors, fonts |
| Backup | SFTP host/credentials/schedule for DB backups |
| SMTP | Email server for password resets and backup notifications |

## Project Structure

```
app/
├── __init__.py              # App factory, context processor, before_request hooks
├── models/
│   ├── user.py              # User, UserRole, decorators (admin_required, organizer_required)
│   ├── voter.py             # Voter file records
│   ├── signature.py         # Petition signatures
│   ├── book.py              # Petition books
│   ├── batch.py             # Data entry sessions (status: open/complete)
│   ├── collector.py         # Collectors, Organizations, PaidCollectors
│   ├── settings.py          # Key-value settings store
│   ├── voter_import.py      # Import job tracking
│   └── print_job.py         # PDF print job records
├── routes/
│   ├── auth.py              # Login, logout, password change
│   ├── main.py              # Home, session start/end, book check
│   ├── signatures.py        # Search, record-match, undo
│   ├── collectors.py        # Collector CRUD
│   ├── organizations.py     # Organization CRUD
│   ├── users.py             # User management
│   ├── stats.py             # Statistics & CSV export
│   ├── settings.py          # App settings, backup, SMTP, branding, health
│   ├── imports.py           # Voter file import & rollback
│   ├── prints.py            # PDF petition printing
│   └── help.py              # Help page
├── services/
│   ├── voter_search.py      # Hybrid prefix+trigram search
│   ├── stats.py             # Statistics queries (StatsService)
│   ├── branding.py          # Color palette extraction from logo
│   ├── fonts.py             # Font list and CSS stack helpers
│   ├── pdf_print.py         # PyMuPDF serial number stamping
│   ├── voter_import.py      # CSV import with progress + rollback
│   ├── backup.py            # SFTP backup execution
│   ├── scheduler.py         # APScheduler job management
│   └── email.py             # SMTP email sending
└── templates/
    ├── base.html            # Layout, nav, Tailwind runtime config
    ├── main/                # Home / session management
    ├── signatures/          # Entry workflow, HTMX partials
    ├── stats/               # Dashboard, collectors, enterers, books, organizations
    ├── settings/            # Settings, branding, backup, system health
    └── ...
```

## Key Routes

| Route | Access | Description |
|---|---|---|
| `/` | All | Home — start/end entry sessions |
| `/signatures/entry` | All | Signature entry workflow |
| `/signatures/undo-last` | All | Remove last signature (POST) |
| `/stats/` | All | Progress dashboard |
| `/stats/collectors` | All | Collector quality metrics |
| `/stats/enterers` | All | Enterer performance |
| `/stats/books` | All | Per-book stats |
| `/stats/export-matched.csv` | Organizer+ | Download matched signatures CSV |
| `/stats/export-duplicates.csv` | Organizer+ | Download cross-book duplicates CSV |
| `/users/` | Organizer+ | User management |
| `/imports/` | Admin | Voter file import |
| `/prints/` | Admin | PDF petition printing |
| `/settings/` | Admin | Application settings |
| `/settings/system-health` | Admin | System health dashboard |
| `/settings/export-config` | Admin | Download settings JSON |
| `/settings/import-config` | Admin | Restore settings from JSON (POST) |

## Database Schema

| Table | Purpose |
|---|---|
| `users` | Application users; roles: enterer / organizer / admin |
| `voters` | Voter file records (imported from SOS CSV) |
| `signatures` | Verified petition signatures |
| `books` | Petition books |
| `batches` | Data entry sessions; `status`: open / complete |
| `collectors` | Signature collectors |
| `organizations` | Organizations managing collectors |
| `settings` | Key-value application configuration |
| `voter_imports` | Import job state and progress tracking |
| `petition_print_jobs` | PDF print jobs with serial ranges |

## License

MIT License
