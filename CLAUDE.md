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
| `CAMPAIGN1_DOMAIN` | Domain for Caddy TLS — campaign 1 (Docker only) |
| `POSTGRES_PASSWORD` | PostgreSQL password (Docker only) |
| `ADMIN_EMAIL` | Bootstrap admin email (Docker only) |
| `ADMIN_PASSWORD` | Bootstrap admin password (Docker only) |
| `SMTP_HOST` | SMTP server — env-var default, overridden by admin UI settings |
| `SMTP_PORT` | SMTP port (default `587`) |
| `SMTP_USER` | SMTP username |
| `SMTP_FROM_EMAIL` | Sender address |
| `SMTP_PASSWORD` | SMTP password |
| `SMTP_USE_TLS` | `true`/`false` (default `true`) |

### SMTP Priority

`Settings.get_smtp_config()` checks the DB first, then falls back to `SMTP_*` env vars. Set the env vars in `.env` to share one SMTP config across all campaign instances without touching the admin UI on each.

### Multi-Campaign Docker Setup

Multiple campaigns run as isolated Docker stacks sharing a single Caddy instance. See `project-docs/INFRASTRUCTURE.md` and `DEPLOYMENT.md` for the full architecture.

Key files:
- `docker-compose.yml` — campaign 1 (includes Caddy, binds ports 80/443)
- `docker-compose.campaign.yml` — template for campaigns 2+ (no Caddy)
- `Caddyfile` — `(mandate_proxy)` snippet + one site block per campaign
- `scripts/new-campaign.sh` — spin up a new campaign interactively
- `scripts/update-campaigns.sh` — pull + rebuild all running campaigns
- `scripts/remove-campaign.sh` — decommission a campaign

The shared Docker network (`mandate-proxy`) must exist before starting any stack:
```bash
docker network create mandate-proxy
```

---

## Critical Rules

### 0. NEVER Publish Sensitive Data

- NEVER commit passwords, API keys, tokens, or secrets to git/npm/docker
- NEVER commit `.env` files — ALWAYS verify `.env` is in `.gitignore`
- Before ANY commit: verify no secrets are included
- NEVER output secrets in suggestions, logs, or responses

### 1. TypeScript Always

- ALWAYS use TypeScript for new files (strict mode)
- NEVER use `any` unless absolutely necessary and documented why
- When editing JavaScript files, convert to TypeScript first
- Types are specs — they tell you what functions accept and return

### 2. API Versioning

```
CORRECT: /api/v1/users
WRONG:   /api/users
```

Every API endpoint MUST use `/api/v1/` prefix. No exceptions.

### 3. Database Access — StrictDB

StrictDB started as this starter kit's custom database wrapper and evolved into a standalone npm package. Install `strictdb` + your database driver. Use `StrictDB.create()` directly. NEVER import native drivers (`mongodb`, `pg`, `mysql2`, `mssql`, `better-sqlite3`) — StrictDB handles everything.

- NEVER create database connections anywhere except your app's startup/entry point
- NEVER use `mongoose` or any ODM
- StrictDB has built-in sanitization, guardrails, and AI-first discovery
- Backend auto-detected from `STRICTDB_URI` scheme — one API for all databases

| URI Scheme | Backend |
|---|---|
| `mongodb://` `mongodb+srv://` | MongoDB |
| `postgresql://` `postgres://` | PostgreSQL |
| `mysql://` | MySQL |
| `mssql://` | MSSQL |
| `file:` `sqlite:` | SQLite |
| `http://` `https://` | Elasticsearch |

#### Setup

```typescript
import { StrictDB } from 'strictdb';

// Create once at app startup, share the instance
const db = await StrictDB.create({ uri: process.env.STRICTDB_URI! });
```

```typescript
// CORRECT — use the StrictDB instance
const user = await db.queryOne<User>('users', { email });

// WRONG — NEVER import native drivers
import { MongoClient } from 'mongodb';     // FORBIDDEN
import { Pool } from 'pg';                 // FORBIDDEN
```

#### Reading data

```typescript
// Single document/row lookup
const user = await db.queryOne<User>('users', { email });

// Multiple documents/rows with options
const recentOrders = await db.queryMany<Order>('orders',
  { userId, status: 'active' },
  { sort: { createdAt: -1 }, limit: 20 },
);

// Lookup/join
const userWithOrders = await db.queryWithLookup<UserWithOrders>('users', {
  match: { _id: userId },
  lookup: { from: 'orders', localField: '_id', foreignField: 'userId', as: 'orders' },
  unwind: 'orders',
});

// Count
const total = await db.count('users', { role: 'admin' });
```

#### Writing data

```typescript
// Insert
await db.insertOne('users', { email, name, createdAt: new Date() });
await db.insertMany('events', batchOfEvents);

// Update — use $inc for counters, $set for fields (NEVER read-modify-write)
await db.updateOne('users', { _id: userId }, { $set: { name: 'New Name' } });
await db.updateOne('stats', { date }, { $inc: { pageViews: 1, visitors: 1 } }, true); // upsert

// Batch operations
await db.batch([
  { operation: 'insertOne', collection: 'orders', doc: { item: 'widget', qty: 5 } },
  { operation: 'updateOne', collection: 'inventory', filter: { sku: 'W1' }, update: { $inc: { stock: -5 } } },
]);

// Delete
await db.deleteOne('tokens', { token: expiredToken });
```

#### AI-first discovery

```typescript
// Discover collection schema — call before querying unfamiliar collections
const schema = await db.describe('users');

// Dry-run validation — catches errors before execution
const check = await db.validate('users', { filter: { role: 'admin' }, doc: { email: 'test@test.com' } });

// See the native query under the hood
const plan = await db.explain('users', { filter: { role: 'admin' }, limit: 50 });
```

#### StrictDB-MCP — AI agents should use the `strictdb-mcp` MCP server for database operations. It exposes 14 tools with all guardrails enforced automatically:

```bash
claude mcp add strictdb -- npx -y strictdb-mcp@latest
```

Requires `STRICTDB_URI` in your environment.

#### Schema registration with Zod

```typescript
import { z } from 'zod';

db.registerCollection({
  name: 'users',
  schema: z.object({
    email: z.string().max(255),
    name: z.string(),
    role: z.enum(['admin', 'user', 'mod']),
  }),
  indexes: [{ collection: 'users', fields: { email: 1 }, unique: true }],
});

// Call once at app startup
await db.ensureIndexes();
```

#### Graceful shutdown — MANDATORY for every Node.js entry point

ANY crash or termination signal MUST close database connections before exiting.
NEVER call `process.exit()` without closing connections first.

```typescript
// Termination signals — clean exit
process.on('SIGTERM', () => db.gracefulShutdown(0));
process.on('SIGINT', () => db.gracefulShutdown(0));

// Crashes — close connections, then exit with error code
process.on('uncaughtException', (err) => {
  console.error('Uncaught Exception:', err);
  db.gracefulShutdown(1);
});
process.on('unhandledRejection', (reason) => {
  console.error('Unhandled Rejection:', reason);
  db.gracefulShutdown(1);
});
```

`db.gracefulShutdown()` is idempotent — safe to call from multiple signals.

#### Test queries — `scripts/db-query.ts` (MANDATORY pattern)

**ABSOLUTE RULE: ALL ad-hoc / test / dev database queries go through the db-query system. No exceptions.**

When a developer asks to "look something up in the database", "check a collection", "find a user", or any exploratory query:

1. **Create a query file** in `scripts/queries/<descriptive-name>.ts`
2. **Register it** in `scripts/db-query.ts` query registry
3. **NEVER** create standalone scripts, one-off files, or inline queries in `src/`

```typescript
// scripts/queries/find-expired-sessions.ts
import type { StrictDB } from 'strictdb';

export default {
  name: 'find-expired-sessions',
  description: 'Find sessions that expired in the last 24 hours',
  async run(db: StrictDB, args: string[]): Promise<void> {
    const cutoff = new Date(Date.now() - 24 * 60 * 60 * 1000);
    const sessions = await db.queryMany('sessions',
      { expiresAt: { $lt: cutoff } },
      { sort: { expiresAt: -1 }, limit: 50 },
    );
    console.log(`Found ${sessions.length} expired sessions:`);
    console.log(JSON.stringify(sessions, null, 2));
  },
};
```

Then register in `scripts/db-query.ts`:
```typescript
const queryRegistry = {
  'find-expired-sessions': () => import('./queries/find-expired-sessions.js'),
};
```

Run: `npx tsx scripts/db-query.ts find-expired-sessions`

**Why this matters:**
- **One instance** — prevents connection exhaustion (the #1 Claude Code database failure)
- **One place to change** — swap databases without touching business logic
- **One place to mock** — testing becomes trivial
- **One place for test queries** — no scripts scattered across the project
- **Discoverable** — `npx tsx scripts/db-query.ts --list` shows all available queries

**FORBIDDEN patterns:**
```typescript
// NEVER do this — creates rogue query files outside the system
// scripts/check-users.ts        ← WRONG
// src/utils/debug-query.ts      ← WRONG
// src/handlers/temp-lookup.ts   ← WRONG

// ALWAYS do this — use the db-query system
// scripts/queries/check-users.ts + register in db-query.ts  ← CORRECT
```

### 4. Testing — Explicit Success Criteria

- ALWAYS define explicit success criteria for E2E tests
- "Page loads" is NOT a success criterion
- Every test MUST verify: URL, visible elements, data displayed
- NEVER write tests without assertions
- Use `/create-e2e <feature>` to create E2E tests with proper structure

```typescript
// CORRECT — explicit success criteria (MINIMUM 3 assertions per test)
await expect(page).toHaveURL('/dashboard');              // 1. URL
await expect(page.locator('h1')).toContainText('Welcome'); // 2. Element visible
await expect(page.locator('[data-testid="user"]')).toContainText('test@example.com'); // 3. Data correct

// WRONG — passes even if broken
await page.goto('/dashboard');
// no assertion!
```

**A test is NOT finished until it has:**
- At least one URL assertion (`toHaveURL`)
- At least one element visibility assertion (`toBeVisible`)
- At least one content/data assertion (`toContainText`, `toHaveValue`)
- Error case coverage (what happens when it fails?)

**E2E test execution — ALWAYS kills test ports first:**
```bash
pnpm test:e2e          # kills ports 4000/4010/4020 → spawns servers → runs Playwright
pnpm test:e2e:headed   # same but with visible browser
pnpm test:e2e:ui       # same but with Playwright UI mode
```

E2E tests run on TEST ports (4000, 4010, 4020) — never dev ports.
`playwright.config.ts` spawns servers automatically via `webServer`.

### 5. NEVER Hardcode Credentials

- ALWAYS use environment variables for secrets
- NEVER put API keys, passwords, or tokens directly in code
- NEVER hardcode connection strings — use STRICTDB_URI from .env

### 6. ALWAYS Ask Before Deploying

- NEVER auto-deploy, even if the fix seems simple
- NEVER assume approval — wait for explicit "yes, deploy"
- ALWAYS ask before deploying to production

### 7. Quality Gates

- No file > 300 lines (split if larger)
- No function > 50 lines (extract helper functions)
- All tests must pass before committing
- TypeScript must compile with no errors (`tsc --noEmit`)

### 8. Parallelize Independent Awaits

- When multiple `await` calls are independent (none depends on another's result), ALWAYS use `Promise.all`
- NEVER await independent operations sequentially — it wastes time
- Before writing sequential awaits, evaluate: does the second call need the first call's result?

```typescript
// CORRECT — independent operations run in parallel
const [users, products, orders] = await Promise.all([
  getUsers(),
  getProducts(),
  getOrders(),
]);

// WRONG — sequential when they don't depend on each other
const users = await getUsers();
const products = await getProducts();  // waits for users unnecessarily
const orders = await getOrders();      // waits for products unnecessarily
```

```typescript
// CORRECT — sequential when there IS a dependency
const user = await getUserById(id);
const orders = await getOrdersByUserId(user.id); // needs user.id
```

### 9. Git Workflow — NEVER Work Directly on Main

**Auto-branch is ON by default.** A hook blocks commits to `main`. To avoid wasted work, **ALWAYS check and branch BEFORE editing any files:**

```bash
# MANDATORY first step — do this BEFORE writing or editing anything:
git branch --show-current
# If on main → create a feature branch IMMEDIATELY:
git checkout -b feat/<task-name>
# NOW start working.
```

**Branch naming conventions:**
- `feat/<name>` — new features
- `fix/<name>` — bug fixes
- `docs/<name>` — documentation changes
- `refactor/<name>` — code refactors
- `chore/<name>` — maintenance tasks
- `test/<name>` — test additions

**Why branch FIRST, not at commit time:**
- The `check-branch.sh` hook blocks `git commit` on `main`
- If you edit 10 files on `main` then try to commit, you'll be blocked and have to branch retroactively
- Branching first costs 1 second. Branching after being blocked wastes time and creates messy history.

- Use `/worktree <branch-name>` when you want a separate directory (parallel sessions)
- If Claude screws up on a feature branch, delete it — main is untouched

```bash
# For parallel sessions (separate directories):
/worktree add-auth                # creates branch + separate working directory

# To disable auto-branching:
# Set auto_branch = false in claude-mastery-project.conf
```

**Before merging any branch back to main:**
1. Review the full diff: `git diff main...HEAD`
2. Ask the user: "Do you want RuleCatch to check for violations on this branch?"
3. Only merge after the user confirms

**Why this matters:**
- Main should always be deployable
- Feature branches are disposable — delete and start over if needed
- `git diff main...HEAD` shows exactly what changed, making review easy
- Auto-branching means zero friction — you don't have to remember
- Worktrees let you run multiple Claude sessions in parallel without conflicts
- RuleCatch catches violations Claude missed — last line of defense before merge

### 10. Docker Push Gate — Local Test Before Push

**Disabled by default.** When enabled (`docker_test_before_push = true` in `claude-mastery-project.conf`), ANY `docker push` is BLOCKED until the image passes local verification:

1. Build the image
2. Run the container locally
3. Wait 5 seconds for startup
4. Verify container is still running (didn't crash/exit)
5. Hit the health endpoint (must return 200)
6. Check logs for fatal errors
7. Clean up test container
8. **Only then** allow `docker push`

If any step fails: STOP, show what failed, and do NOT push.

```bash
# Enable in claude-mastery-project.conf:
docker_test_before_push = true

# Disable (default):
docker_test_before_push = false
```

This gate applies globally — every command or workflow that pushes to Docker Hub must respect it.

---

---

## When Something Seems Wrong

Before jumping to conclusions:

- Missing UI element? → Check feature gates BEFORE assuming bug
- Empty data? → Check if services are running BEFORE assuming broken
- 404 error? → Check service separation BEFORE adding endpoint
- Auth failing? → Check which auth system BEFORE debugging
- Test failing? → Read the error message fully BEFORE changing code

---

---

## Windows Users — Use VS Code in WSL Mode

If you're on Windows, you should be running VS Code in **WSL 2 mode**. Most people don't know this exists and it dramatically changes everything:

- **HMR is 5-10x faster** — file changes don't cross the Windows/Linux boundary
- **Playwright tests run significantly faster** — native Linux browser processes
- **File watching actually works** — `tsx watch`, `next dev`, `nodemon` are all reliable
- **Node.js filesystem operations** avoid the slow NTFS translation layer
- **Claude Code runs faster** — native Linux tools (`grep`, `find`, `git`)

**CRITICAL:** Your project must be on the **WSL filesystem** (`~/projects/`), NOT on `/mnt/c/`. Having WSL but keeping your project on the Windows filesystem gives you the worst of both worlds.

```bash
# Check if you're set up correctly:
pwd
# GOOD: /home/you/projects/my-app
# BAD:  /mnt/c/Users/you/projects/my-app  ← still hitting Windows filesystem

# VS Code: click green "><" icon bottom-left → "Connect to WSL"
```

Run `/setup` to auto-detect your environment and get specific instructions.

---

---

## Project Documentation

| Document | Purpose | When to Read |
|----------|---------|--------------|
| `project-docs/ARCHITECTURE.md` | System overview & data flow | Before architectural changes |
| `project-docs/INFRASTRUCTURE.md` | Deployment details | Before environment changes |
| `project-docs/DECISIONS.md` | Architectural decisions | Before proposing alternatives |

**ALWAYS read relevant docs before making cross-service changes.**

---

---

## Coding Standards

### Imports

```typescript
// CORRECT — explicit, typed
import { getUserById } from './handlers/users.js';
import type { User } from './types/index.js';

// WRONG — barrel imports that pull everything
import * as everything from './index.js';
```

### Error Handling

```typescript
// CORRECT — handle errors explicitly
try {
  const user = await getUserById(id);
  if (!user) throw new NotFoundError('User not found');
  return user;
} catch (err) {
  logger.error('Failed to get user', { id, error: err });
  throw err;
}

// WRONG — swallow errors silently
try {
  return await getUserById(id);
} catch {
  return null; // silent failure
}
```

### Go (Gin / Chi / Echo / Fiber / stdlib)

When working on a Go project (detected by `go.mod` in root or `language = go` in profile):

- **Standard layout:** `cmd/` for entry points, `internal/` for private packages — follow Go conventions
- **Go modules:** Always use `go.mod` / `go.sum` — NEVER use `GOPATH` mode or `dep`
- **golangci-lint:** Run `golangci-lint run` before committing — config in `.golangci.yml`
- **Table-driven tests:** Use `[]struct{ name string; ... }` pattern for multiple test cases
- **context.Context:** Every I/O function accepts `ctx context.Context` as first parameter
- **Interfaces:** Accept interfaces, return structs — define interfaces at the consumer
- **Error handling:** NEVER ignore errors with `_` — always check and wrap with `fmt.Errorf("context: %w", err)`
- **No global mutable state:** Pass dependencies via struct fields, not package-level vars
- **Graceful shutdown:** Handle SIGINT/SIGTERM, close DB connections with `context.WithTimeout`
- **API versioning:** Same rule — all endpoints under `/api/v1/` prefix
- **Quality gates:** Same limits — no file > 300 lines, no function > 50 lines
- **Makefile:** Use `make build`, `make test`, `make lint` — NOT raw `go` commands in scripts

### Python (FastAPI / Django / Flask)

When working on a Python project (detected by `pyproject.toml` in root or `language = python` in profile):

- **Type hints ALWAYS:** Every function MUST have type hints for all parameters AND return type
- **Modern syntax:** Use `str | None` (not `Optional[str]`), `list[str]` (not `List[str]`)
- **Async consistently:** FastAPI handlers must be `async def` for I/O operations
- **pytest only:** NEVER use unittest — use pytest with `@pytest.mark.parametrize` for table-driven tests
- **Virtual environment:** ALWAYS use `.venv/` — NEVER install packages globally
- **Pydantic models:** Use Pydantic `BaseModel` for all request/response schemas
- **Pydantic settings:** Use `pydantic-settings` `BaseSettings` for environment config
- **ruff:** Run `ruff check` before committing — config in `ruff.toml` or `pyproject.toml`
- **API versioning:** Same rule — all endpoints under `/api/v1/` prefix
- **Quality gates:** Same limits — no file > 300 lines, no function > 50 lines
- **Makefile:** Use `make dev`, `make test`, `make lint` — NOT raw Python commands in scripts
- **Graceful shutdown:** Handle SIGINT/SIGTERM, close database connections before exiting

---

---

## Naming — NEVER Rename Mid-Project

Renaming packages, modules, or key variables mid-project causes cascading failures that are extremely hard to catch. If you must rename:

1. Create a checklist of ALL files and references first
2. Use IDE semantic rename (not search-and-replace)
3. Full project search for old name after renaming
4. Check: .md files, .txt files, .env files, comments, strings, paths
5. Start a FRESH Claude session after renaming

---

---

## Plan Mode — Plan First, Code Second

**For any non-trivial task, start in plan mode.** Don't let Claude write code until you've agreed on the plan. Bad plan = bad code. Always.

- Use plan mode for: new features, refactors, architectural changes, multi-file edits
- Skip plan mode for: typo fixes, single-line changes, obvious bugs
- One Claude writes the plan. You review it as the engineer. THEN code.

### Step Naming — MANDATORY

Every step in a plan MUST have a consistent, unique name. This is how the user references steps when requesting changes. Claude forgets to update plans — named steps make it unambiguous.

```
CORRECT — named steps the user can reference:
  Step 1 (Project Setup): Initialize repo with TypeScript
  Step 2 (Database Layer): Set up StrictDB
  Step 3 (Auth System): Implement JWT authentication
  Step 4 (API Routes): Create user endpoints
  Step 5 (Testing): Write E2E tests for auth flow

WRONG — generic steps nobody can reference:
  Step 1: Set things up
  Step 2: Build the backend
  Step 3: Add tests
```

### Modifying a Plan — REPLACE, Don't Append

When the user asks to change something in the plan:

1. **FIND** the exact named step being changed
2. **REPLACE** that step's content entirely with the new approach
3. **Review ALL other steps** for contradictions with the change
4. **Rewrite the full updated plan** so the user can see the complete picture

```
CORRECT:
  User: "Change Step 3 (Auth System) to use session cookies instead of JWT"
  Claude: Replaces Step 3 content, checks Steps 4-5 for JWT references,
          outputs the FULL updated plan with Step 3 rewritten

WRONG:
  User: "Actually use session cookies instead"
  Claude: Appends "Also, use session cookies" at the bottom
          ← Step 3 still says JWT. Now the plan contradicts itself.
```

**Claude will forget to do this.** If you notice the plan has contradictions, tell Claude: "Rewrite the full plan — Step 3 and Step 7 contradict each other."

- If fundamentally changing direction: `/clear` → state requirements fresh

---

---

## Documentation Sync

When updating any feature, keep these locations in sync:

1. `README.md` (repository root)
2. `docs/index.html` (GitHub Pages site)
3. `project-docs/` (relevant documentation)
4. `CLAUDE.md` quick reference table (if adding commands/scripts)
5. `tests/STARTER-KIT-VERIFICATION.md` (if adding hooks/files)
6. Inline code comments
7. Test descriptions

If you update one, update ALL.

### Adding a New Command or Hook — MANDATORY Checklist

When creating a new `.claude/commands/*.md` or `.claude/hooks/*.sh`:

1. **README.md** — Update the command count, project structure tree, and add a description section
2. **docs/index.html** — Update the command count, project structure tree, and add a command card
3. **CLAUDE.md** — Add to the quick reference table (if user-facing)
4. **tests/STARTER-KIT-VERIFICATION.md** — Add verification checklist entry
5. **.claude/settings.json** — Wire up hooks (if adding a hook)

**This is NOT optional.** Every command/hook must appear in all five locations before the commit.

### Command Scope Classification

Every command has a `scope:` field in its YAML frontmatter:

- **`scope: project`** (16 commands) — Work inside any project. Copied to scaffolded projects by `/new-project`, `/convert-project-to-starter-kit`, and `/update-project`.
- **`scope: starter-kit`** (10 commands) — Kit management only. Never copied to scaffolded projects.

**Project commands:** `help`, `review`, `commit`, `progress`, `test-plan`, `architecture`, `security-check`, `optimize-docker`, `create-e2e`, `create-api`, `worktree`, `refactor`, `diagram`, `setup`, `what-is-my-ai-doing`, `show-user-guide`

**Starter-kit commands:** `new-project`, `update-project`, `convert-project-to-starter-kit`, `install-global`, `projects-created`, `remove-project`, `set-project-profile-default`, `add-project-setup`, `quickstart`, `add-feature`

When distributing commands (new-project, convert, update), **always filter by `scope: project`** in the source command's frontmatter. Skills, agents, hooks, and settings.json are copied in full regardless of scope.

---

---

## CLAUDE.md Is Team Memory — The Feedback Loop

Every time Claude makes a mistake, **add a rule to prevent it from happening again.**

This is the single most powerful pattern for improving Claude's behavior over time:

1. Claude makes a mistake (wrong pattern, bad assumption, missed edge case)
2. You fix the mistake
3. You tell Claude: "Update CLAUDE.md so you don't make that mistake again"
4. Claude adds a rule to this file
5. Mistake rates actually drop over time

**This file is checked into git. The whole team benefits from every lesson learned.**

Don't just fix bugs — fix the rules that allowed the bug. Every mistake is a missing rule.

**If RuleCatch is installed:** also add the rule as a custom RuleCatch rule so it's monitored automatically across all future sessions. CLAUDE.md rules are suggestions — RuleCatch enforces them.

---

---

## Workflow Preferences

- Quality over speed — if unsure, ask before executing
- Plan first, code second — use plan mode for non-trivial tasks
- One task, one chat — `/clear` between unrelated tasks
- One task, one branch — use `/worktree` to isolate work from main
- Use `/context` to check token usage when working on large tasks
- When testing: queue observations, fix in batch (not one at a time)
- Research shows 2% misalignment early in a conversation can cause 40% failure rate by end — start fresh when changing direction
