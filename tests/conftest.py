"""
Shared test fixtures and factory helpers for the Mandate test suite.

Database strategy: session-scoped schema creation, function-scoped TRUNCATE
for isolation. All factory functions use db.session.flush() — callers must
call db.session.commit() before making HTTP requests so the test-client's
request context (separate session) can see the data.
"""
import os
import uuid
from datetime import date
from urllib.parse import urlparse, urlunparse

import pytest
from sqlalchemy import text

from app import create_app, db
from app.config import Config


def _test_db_url() -> str:
    """Derive test DB URL: use TEST_DATABASE_URL if set, else replace the
    database name in DATABASE_URL with 'mandate_test'."""
    if "TEST_DATABASE_URL" in os.environ:
        return os.environ["TEST_DATABASE_URL"]
    base = os.environ.get("DATABASE_URL", "postgresql://localhost:5432/mandate")
    parsed = urlparse(base)
    return urlunparse(parsed._replace(path="/mandate_test"))


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = _test_db_url()
    SECRET_KEY = "test-secret-key"


# ---------------------------------------------------------------------------
# Core fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def app():
    """Create app and DB schema once per test session.

    Does NOT push a persistent app context — that's done per-test in
    clean_db so that Flask's g (and Flask-Login's current_user cache)
    is fresh for every test.
    """
    _app = create_app(TestConfig)
    with _app.app_context():
        db.create_all()
    yield _app
    with _app.app_context():
        db.drop_all()


@pytest.fixture(autouse=True)
def clean_db(app):
    """Push a fresh app context per test; truncate all tables after.

    Flask stores g in the app context.  Flask-Login caches current_user
    in g._login_user, so a single persistent app context would cause a
    logged-in user from one test to bleed into the next.  Pushing a new
    context per test gives each test a clean g and db.session.
    """
    ctx = app.app_context()
    ctx.push()
    yield
    db.session.rollback()
    table_names = ", ".join(f'"{t.name}"' for t in reversed(db.metadata.sorted_tables))
    db.session.execute(text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"))
    db.session.commit()
    db.session.remove()
    ctx.pop()


@pytest.fixture
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# Factory helpers  (call db.session.commit() after these before HTTP requests)
# ---------------------------------------------------------------------------

def make_user(role="enterer", email=None, password="password123",
              must_change=False, active=True,
              first_name="Test", last_name="User"):
    from app.models import User
    user = User(
        email=email or f"user_{uuid.uuid4().hex[:8]}@test.example",
        first_name=first_name,
        last_name=last_name,
        role=role,
        must_change_password=must_change,
    )
    user.set_password(password)
    user.is_active = active
    db.session.add(user)
    db.session.flush()
    return user


def make_collector(first_name="Jane", last_name="Collector"):
    from app.models import Collector
    c = Collector(first_name=first_name, last_name=last_name)
    db.session.add(c)
    db.session.flush()
    return c


def make_book(collector_id, book_number=None, date_out=None, date_back=None):
    from app.models import Book
    book = Book(
        book_number=book_number or f"B{uuid.uuid4().hex[:6].upper()}",
        collector_id=collector_id,
        date_out=date_out or date.today(),
        date_back=date_back or date.today(),
    )
    db.session.add(book)
    db.session.flush()
    return book


def make_batch(book_id, collector_id, enterer_id, book_number="BK001", status="open"):
    from app.models import Batch
    batch = Batch(
        book_id=book_id,
        book_number=book_number,
        collector_id=collector_id,
        enterer_id=enterer_id,
        enterer_first="Test",
        enterer_last="Enterer",
        enterer_email="enterer@test.example",
        date_entered=date.today(),
        status=status,
    )
    db.session.add(batch)
    db.session.flush()
    return batch


def make_voter(sos_voterid=None, city="COLUMBUS CITY",
               first_name="John", last_name="Voter",
               residential_zip="43215"):
    from app.models import Voter
    voter = Voter(
        sos_voterid=sos_voterid or f"OH{uuid.uuid4().hex[:10].upper()}",
        county_number="25",
        first_name=first_name,
        last_name=last_name,
        residential_address1="123 Main St",
        residential_city="Columbus",
        residential_state="OH",
        residential_zip=residential_zip,
        city=city,
    )
    db.session.add(voter)
    db.session.flush()
    return voter


def make_signature(book_id, batch_id, matched=True, sos_voterid=None,
                   registered_city=None, residential_zip=None):
    from app.models import Signature
    sig = Signature(
        book_id=book_id,
        batch_id=batch_id,
        matched=matched,
        sos_voterid=sos_voterid,
        residential_zip=residential_zip,
        registered_city=registered_city,
    )
    if sos_voterid and residential_zip is None:
        sig.residential_zip = "43215"
        sig.residential_address1 = "123 Main St"
        sig.residential_city = "Columbus"
        sig.residential_state = "OH"
    db.session.add(sig)
    db.session.flush()
    return sig


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

def login(client, user, password="password123"):
    """POST to /auth/login and return the response (no redirect follow)."""
    return client.post(
        "/auth/login",
        data={"email": user.email, "password": password},
        follow_redirects=False,
    )
