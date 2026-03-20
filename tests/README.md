# Test Suite

73 tests covering auth, session management, signature recording, stats SQL,
and settings export/import.

## One-time setup

```bash
# Create the test database
createdb mandate_test

# Enable the trigram extension (required by the voters table indexes)
psql mandate_test -c 'CREATE EXTENSION IF NOT EXISTS pg_trgm;'
```

The test DB URL defaults to `postgresql://localhost/mandate_test`. Override
with the `TEST_DATABASE_URL` environment variable (the conftest derives it
automatically from `DATABASE_URL` if not set):

```bash
TEST_DATABASE_URL=postgresql://user:pass@host/mandate_test pytest
```

## Running tests

```bash
# All tests
pytest

# Verbose — shows each test name
pytest -v

# Stop on first failure
pytest -x

# A single file or test
pytest tests/test_auth.py
pytest tests/test_auth.py::test_login_open_redirect_blocked
```

## File layout

| File | What it covers |
|---|---|
| `conftest.py` | App fixture, DB isolation, factory helpers, `login()` |
| `test_models.py` | `User.check_password`, `is_admin`, `Settings.get/set` |
| `test_auth.py` | Login, logout, password change, token replay, RBAC |
| `test_main.py` | Start/end session, book reuse, `check-book` JSON endpoint |
| `test_signatures.py` | record-match/address-only/no-match, undo, ownership 400s |
| `test_stats.py` | Progress deduplication SQL, enterer/collector stats math |
| `test_settings.py` | Export sensitive-key exclusion, import round-trip, admin-only |

## How isolation works

**Schema** is created once per `pytest` session (`db.create_all()`) and
dropped at the end.

**Data** is isolated per test: the `clean_db` autouse fixture truncates all
tables with `TRUNCATE … RESTART IDENTITY CASCADE` after every test.

**App context** is pushed fresh for every test (not once for the whole
session). This is the critical detail — Flask stores `g` in the app context,
and Flask-Login caches `current_user` in `g._login_user`. A single persistent
context would let a logged-in user from one test bleed into the next.

## Factory helpers (in `conftest.py`)

All factories flush but do **not** commit. Call `db.session.commit()` before
making HTTP requests — the test client's request runs in a separate connection
and won't see uncommitted rows.

```python
from conftest import make_user, make_collector, make_book, make_batch, make_voter, make_signature, login

def test_something(client, app):
    user = make_user(role="organizer", email="org@test.example")
    collector = make_collector()
    book = make_book(collector.id, book_number="BK001")
    batch = make_batch(book.id, collector.id, user.id)
    voter = make_voter(sos_voterid="OH12345", city="COLUMBUS CITY")
    sig = make_signature(book.id, batch.id, matched=True, sos_voterid=voter.sos_voterid)
    db.session.commit()          # ← required before any client.post/get

    login(client, user)
    resp = client.get("/stats/")
    assert resp.status_code == 200
```

### Factory signatures

```python
make_user(role="enterer", email=None, password="password123",
          must_change=False, active=True, first_name="Test", last_name="User")

make_collector(first_name="Jane", last_name="Collector")

make_book(collector_id, book_number=None, date_out=None, date_back=None)

make_batch(book_id, collector_id, enterer_id, book_number="BK001", status="open")

make_voter(sos_voterid=None, city="COLUMBUS CITY",
           first_name="John", last_name="Voter", residential_zip="43215")

make_signature(book_id, batch_id, matched=True, sos_voterid=None,
               registered_city=None, residential_zip=None)

login(client, user, password="password123")  # POSTs to /auth/login
```

## Setting up a session for signature tests

The signature routes verify that `session["batch_id"]` belongs to
`current_user`. Use `client.session_transaction()` to inject session vars
directly instead of going through the `/start-session` route:

```python
login(client, user)
with client.session_transaction() as sess:
    sess["book_id"] = book.id
    sess["batch_id"] = batch.id
    sess["book_number"] = book.book_number
```

## Adding new tests

1. Import factories from `conftest`: `from conftest import make_user, ...`
2. Use the `app` fixture if you need to call `db.session` directly without
   HTTP (e.g. `test_models.py` style).
3. Use both `client` and `app` if you need to make HTTP requests and then
   inspect DB state.
4. Call `db.session.expire_all()` after a request if you need to re-read
   objects that may have been modified by the route.
