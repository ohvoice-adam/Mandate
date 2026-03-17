# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This App Does

Mandate is a Flask petition signature management app for tracking, verifying, and printing petition books. It manages voter data, collector assignments, data-entry batches, and generates serialized petition PDFs.

## Development Commands

```bash
# Run in development mode
python run.py

# Database migrations
flask db migrate -m "description"
flask db upgrade

# Seed fake data (dev mode only)
flask dev seed [--voters 500] [--books 25]
flask dev wipe [--yes]
```

Default dev credentials: `organizer@dev.example` / `enterer@dev.example`, password: `devpassword`

## Architecture

### App Factory

`app/__init__.py` is the entry point. `create_app()`:
- Registers 11 blueprints (see `routes/`)
- Injects `app_version`, `branding`, `branding_palette` into every template via a context processor
- Registers a before-request hook that forces a password change if `user.must_change_password` is set
- Starts APScheduler for scheduled backups
- Recovers stale voter imports from crashes

### Settings System

All runtime configuration lives in the `settings` DB table (key-value store), not environment variables. `app/models/settings.py` provides `Settings.get(key, default)` and `Settings.set(key, value)`, plus higher-level grouped helpers like `get_branding_config()`, `get_backup_config()`, `get_smtp_config()`.

### Roles & Authorization

Three roles: `ENTERER`, `ORGANIZER`, `ADMIN`. Access control is done via `@admin_required` and `@organizer_required` decorators imported from `app/models/__init__.py`. Both are defined in `app/models/user.py`.

### Branding & Theming

Branding is fully database-driven. The context processor in `app/__init__.py` reads branding settings on every request and passes `branding` and `branding_palette` dicts to all templates. `base.html` injects the Tailwind config from `branding_palette` via `tojson`. Colors are auto-extracted from uploaded logos via `app/services/branding.py` (colorthief + HSL interpolation).

- Default primary: `#0c3e6b` (navy), accent: `#f56708` (orange)
- Tailwind uses custom color names `navy-*` and `accent-*` configured at runtime

### Voter Search

`app/services/voter_search.py` uses a hybrid approach: fast `ILIKE 'prefix%'` first, then trigram similarity (`pg_trgm`) as a fallback. The `pg_trgm` extension and GIN indexes are created automatically at app startup via `ensure_search_indexes()`, and `ANALYZE voters` runs after each successful import to keep query planner statistics fresh.

### PDF Generation

`app/services/pdf_print.py` uses PyMuPDF (fitz) to stamp serial numbers onto uploaded cover/petition PDF templates. PDFs are stored base64-encoded in the `petition_print_jobs` table (no filesystem). Generation is synchronous and can be slow for large batches (max 500 books).

### Background Jobs

`app/services/scheduler.py` uses APScheduler. Jobs: scheduled SCP backup (cron from Settings) and daily/weekly digest emails. Uses a PostgreSQL advisory lock (`0x4D414E45`) to prevent duplicate runs across workers. `apply_schedule(app)` is called whenever backup settings change.

### Voter Import

`app/services/voter_import.py` handles CSV imports with progress tracking and a 24-hour rollback window. Import state is tracked in the `voter_imports` table (`PENDING → RUNNING → COMPLETED/FAILED`). On app startup, stale `RUNNING` imports are auto-recovered.

## Environment Variables

See `.env.example`. Key vars:

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Flask session signing |
| `DATABASE_URL` | PostgreSQL DSN (default: `postgresql://localhost:5432/mandate`) |
| `UPLOAD_FOLDER` | Temp upload dir (default: `/tmp/petition-qc-uploads`) |
