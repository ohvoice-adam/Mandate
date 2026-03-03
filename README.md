# Mandate

A Flask web application for validating Ohio election petition signatures, with a focus on municipal election petitions. Data entry staff can quickly verify petition signatures against an Ohio Secretary of State voter file database using fast address-based search.

## Features

- **Fast Address Search**: PostgreSQL full-text search with trigram matching for real-time voter lookups
- **Signature Verification**: Three-way matching (Person Match, Address Only, No Match)
- **Session Management**: Track data entry by book, batch, and collector
- **Statistics Dashboard**: Real-time progress tracking and performance metrics
- **Light/Dark Theme**: Automatic OS detection with manual toggle
- **Responsive Design**: Works on desktop and mobile devices

## Tech Stack

- **Backend**: Flask 3.0, SQLAlchemy 2.0
- **Database**: PostgreSQL with pg_trgm extension
- **Frontend**: Jinja2 templates, HTMX, Tailwind CSS
- **Authentication**: Flask-Login

## Prerequisites

- Python 3.10+
- PostgreSQL 12+ with pg_trgm extension
- Ohio Secretary of State voter file data (CSV format)

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/ohvoice-adam/Mandate.git
cd Mandate
```

### 2. Create virtual environment

```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or
venv\Scripts\activate  # Windows
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment

```bash
cp .env.example .env
```

Edit `.env` with your settings:

```env
DATABASE_URL=postgresql://user:password@localhost:5432/mandate
SECRET_KEY=your-secret-key-here
FLASK_ENV=development
FLASK_DEBUG=1
```

**Note**: If your password contains special characters, URL-encode them:
- `@` → `%40`
- `:` → `%3A`
- `/` → `%2F`

### 5. Create the database

```bash
# Connect to PostgreSQL
sudo -u postgres createdb mandate

# Enable pg_trgm extension
sudo -u postgres psql -d mandate -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"
```

### 6. Initialize the application

```bash
python run.py
```

This creates the database tables on first run. Stop the server (Ctrl+C) after tables are created.

### 7. Import voter data

The voter file should be a CSV with these columns:
- `SOS_VOTERID`, `COUNTY_ID`
- `FIRST_NAME`, `MIDDLE_NAME`, `LAST_NAME`
- `RESIDENTIAL_ADDRESS1`, `RESIDENTIAL_ADDRESS2`
- `RESIDENTIAL_CITY`, `RESIDENTIAL_STATE`, `RESIDENTIAL_ZIP`
- `CITY` (registered city, may differ from residential)
- `DATE_OF_BIRTH`, `REGISTRATION_DATE`
- `PRECINCT_CODE`, `PRECINCT_NAME`, `WARD`

Import the data:

```bash
python scripts/import_voters.py path/to/voter_file.csv
```

### 8. Create search indexes

```bash
python scripts/create_indexes.py
```

### 9. Run the application

```bash
python run.py
```

Open http://localhost:5000 in your browser.

## Usage

### Getting Started

1. **Register an account** at `/auth/register`
2. **Add collectors** at `/collectors` (people who collected signatures)
3. **Start a session** on the home page:
   - Enter the petition book number
   - Select the collector
   - Click "Start Session"

### Verifying Signatures

1. Enter the signer's address in the search box
2. Results appear as you type (real-time search)
3. For each signature, click one of:
   - **Person Match** - Name and address match a voter record
   - **Address Only** - Address matches but name doesn't
   - **No Match** - No matching voter found

The search clears automatically after each entry.

### Viewing Statistics

Navigate to `/stats` to see:
- Total signatures entered
- Verification rates
- Columbus vs. non-Columbus breakdown
- Per-enterer and per-organization performance

## Project Structure

```
petition-qc/
├── app/
│   ├── __init__.py          # Flask app factory
│   ├── config.py            # Configuration
│   ├── models/              # SQLAlchemy models
│   │   ├── user.py          # User authentication
│   │   ├── voter.py         # Voter file records
│   │   ├── signature.py     # Verified signatures
│   │   ├── book.py          # Petition books
│   │   ├── batch.py         # Data entry batches
│   │   └── collector.py     # Collectors & organizations
│   ├── routes/              # Flask blueprints
│   │   ├── auth.py          # Login/logout/register
│   │   ├── main.py          # Home, session management
│   │   ├── signatures.py    # Search & verification
│   │   ├── collectors.py    # Collector CRUD
│   │   └── stats.py         # Statistics dashboards
│   ├── services/            # Business logic
│   │   ├── voter_search.py  # PostgreSQL FTS search
│   │   └── stats.py         # Statistics queries
│   └── templates/           # Jinja2 templates
├── scripts/
│   ├── import_voters.py     # Voter file import
│   └── create_indexes.py    # Database index creation
├── requirements.txt
├── run.py                   # Application entry point
└── .env.example             # Environment template
```

## Database Schema

### Core Tables

| Table | Purpose |
|-------|---------|
| `users` | Application users (data entry staff) |
| `voters` | Voter file records for search |
| `signatures` | Verified petition signatures |
| `books` | Petition books |
| `batches` | Data entry sessions |
| `collectors` | Signature collectors |
| `organizations` | Organizations managing collectors |

### Key Relationships

```
collectors -> books -> batches -> signatures
                          ^
                          |
users (enterer) ----------+
```

## Search Performance

The application uses a hybrid search strategy:

1. **Prefix Match** (fast): `ILIKE 'address%'` with B-tree index
2. **Trigram Similarity** (fuzzy): `pg_trgm` GIN index for typo tolerance

This provides sub-100ms response times on 500K+ voter records.

### Indexes

```sql
-- B-tree for prefix search
CREATE INDEX idx_voters_address_btree ON voters (residential_address1);

-- Trigram for fuzzy search
CREATE INDEX idx_voters_address_trgm ON voters
  USING GIN (residential_address1 gin_trgm_ops);
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string | Required |
| `SECRET_KEY` | Flask session secret | Required |
| `FLASK_ENV` | Environment (development/production) | development |
| `FLASK_DEBUG` | Debug mode | 0 |

### Application Settings

In `app/config.py`:

| Setting | Description | Default |
|---------|-------------|---------|
| `SEARCH_RESULTS_LIMIT` | Max search results | 100 |
| `SEARCH_SIMILARITY_THRESHOLD` | Trigram match threshold | 0.2 |

## API Endpoints

### Authentication

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/auth/login` | GET, POST | User login |
| `/auth/logout` | GET | User logout |
| `/auth/register` | GET, POST | User registration |

### Signatures

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/signatures/` | GET | Entry page |
| `/signatures/search` | POST | Search voters (HTMX) |
| `/signatures/record-match` | POST | Record person match |
| `/signatures/record-address-only` | POST | Record address-only match |
| `/signatures/record-no-match` | POST | Record no match |

### Collectors

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/collectors/` | GET | List collectors |
| `/collectors/new` | GET, POST | Add collector |
| `/collectors/<id>/edit` | GET, POST | Edit collector |

### Statistics

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/stats/` | GET | Progress dashboard |
| `/stats/enterers` | GET | Enterer performance |
| `/stats/organizations` | GET | Organization performance |

## Development

### Running in Development

```bash
export FLASK_DEBUG=1
python run.py
```

### Database Migrations

Using Flask-Migrate:

```bash
flask db init      # First time only
flask db migrate   # Generate migration
flask db upgrade   # Apply migration
```

### Dummy Data

When running with `FLASK_DEBUG=1`, two CLI commands are available to populate and clear development data without needing a real voter file.

**Generate dummy data:**

```bash
flask dev seed
```

Creates:
- 2 organizations
- 6 collectors (linked to organizations)
- 2 dev users (`organizer@dev.example` / `enterer@dev.example`, password: `devpassword`)
- 500 fake Columbus-area voters (identified by `SEED-` prefix on their voter ID)
- 25 petition books with 1–2 batches each and roughly 250 signatures (~72% matched)

Options:

```bash
flask dev seed --voters 1000 --books 50   # custom counts
```

**Wipe dummy data:**

```bash
flask dev wipe
```

Removes all signatures, books, batches, collectors, organizations, non-admin users, and `SEED-` prefixed voters. Admin users and application settings are preserved. If a real voter file has been imported, those records are also left untouched — only `SEED-` voters are deleted.

```bash
flask dev wipe --yes   # skip confirmation prompt
```

## Production Deployment

### Gunicorn

```bash
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:8000 "app:create_app()"
```

### Environment

Set for production:

```env
FLASK_ENV=production
FLASK_DEBUG=0
SECRET_KEY=<strong-random-key>
```

## License

MIT License

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request
