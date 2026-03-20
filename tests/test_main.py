"""Tests for session management: start/end session, check-book."""
import json

from conftest import make_user, make_collector, make_book, make_batch, login

from app import db


# ---------------------------------------------------------------------------
# Unauthenticated access
# ---------------------------------------------------------------------------

def test_index_requires_login(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert "/auth/login" in resp.location


# ---------------------------------------------------------------------------
# start-session
# ---------------------------------------------------------------------------

def test_start_session_creates_book_and_batch(client, app):
    user = make_user()
    collector = make_collector()
    db.session.commit()
    login(client, user)

    resp = client.post(
        "/start-session",
        data={
            "book_number": "BK2001",
            "collector_id": collector.id,
            "date_out": "2026-01-01",
            "date_back": "2026-01-15",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302

    db.session.expire_all()
    from app.models import Book, Batch
    book = Book.query.filter_by(book_number="BK2001").first()
    assert book is not None
    batch = Batch.query.filter_by(book_id=book.id).first()
    assert batch is not None
    assert batch.enterer_id == user.id


def test_start_session_reuses_existing_book(client, app):
    """Starting a session for an existing book number reuses the Book row."""
    user = make_user()
    collector = make_collector()
    db.session.commit()
    login(client, user)

    # First session
    client.post(
        "/start-session",
        data={"book_number": "BK3001", "collector_id": collector.id},
    )
    client.post("/end-session")

    # Second session, same book number
    client.post(
        "/start-session",
        data={"book_number": "BK3001", "collector_id": collector.id},
    )

    db.session.expire_all()
    from app.models import Book, Batch
    assert Book.query.filter_by(book_number="BK3001").count() == 1
    assert Batch.query.filter_by(book_number="BK3001").count() == 2


def test_start_session_sets_session_vars(client, app):
    user = make_user()
    collector = make_collector()
    db.session.commit()
    login(client, user)

    client.post(
        "/start-session",
        data={"book_number": "BK4001", "collector_id": collector.id},
    )

    with client.session_transaction() as sess:
        assert sess.get("book_number") == "BK4001"
        assert sess.get("book_id") is not None
        assert sess.get("batch_id") is not None


def test_start_session_date_validation(client, app):
    """date_back < date_out should flash an error and not create a book."""
    user = make_user()
    collector = make_collector()
    db.session.commit()
    login(client, user)

    resp = client.post(
        "/start-session",
        data={
            "book_number": "BK5001",
            "collector_id": collector.id,
            "date_out": "2026-03-15",
            "date_back": "2026-03-01",  # before date_out
        },
        follow_redirects=True,
    )
    assert b"Date In cannot be before Date Out" in resp.data

    db.session.expire_all()
    from app.models import Book
    assert Book.query.filter_by(book_number="BK5001").count() == 0


def test_start_session_missing_book_number_flashes_error(client, app):
    user = make_user()
    collector = make_collector()
    db.session.commit()
    login(client, user)

    resp = client.post(
        "/start-session",
        data={"book_number": "", "collector_id": collector.id},
        follow_redirects=True,
    )
    assert b"Please enter book number" in resp.data


# ---------------------------------------------------------------------------
# end-session
# ---------------------------------------------------------------------------

def test_end_session_marks_batch_complete(client, app):
    user = make_user()
    collector = make_collector()
    book = make_book(collector.id, book_number="BK6001")
    batch = make_batch(book.id, collector.id, user.id, book_number="BK6001")
    db.session.commit()
    login(client, user)

    # Set session vars manually
    with client.session_transaction() as sess:
        sess["book_id"] = book.id
        sess["batch_id"] = batch.id
        sess["book_number"] = "BK6001"

    client.post("/end-session")

    db.session.expire_all()
    from app.models import Batch
    updated = db.session.get(Batch, batch.id)
    assert updated.status == "complete"


def test_end_session_clears_session_vars(client, app):
    user = make_user()
    collector = make_collector()
    book = make_book(collector.id, book_number="BK7001")
    batch = make_batch(book.id, collector.id, user.id, book_number="BK7001")
    db.session.commit()
    login(client, user)

    with client.session_transaction() as sess:
        sess["book_id"] = book.id
        sess["batch_id"] = batch.id
        sess["book_number"] = "BK7001"

    client.post("/end-session")

    with client.session_transaction() as sess:
        assert "book_id" not in sess
        assert "batch_id" not in sess
        assert "book_number" not in sess


# ---------------------------------------------------------------------------
# check-book
# ---------------------------------------------------------------------------

def test_check_book_exists(client, app):
    user = make_user()
    collector = make_collector()
    book = make_book(collector.id, book_number="BK8001")
    db.session.commit()
    login(client, user)

    resp = client.post("/check-book", data={"book_number": "BK8001"})
    data = json.loads(resp.data)
    assert data["exists"] is True
    assert data["book_number"] == "BK8001"


def test_check_book_not_exists(client, app):
    user = make_user()
    db.session.commit()
    login(client, user)

    resp = client.post("/check-book", data={"book_number": "NOPE9999"})
    data = json.loads(resp.data)
    assert data["exists"] is False


def test_check_book_empty_string_returns_not_exists(client, app):
    user = make_user()
    db.session.commit()
    login(client, user)

    resp = client.post("/check-book", data={"book_number": ""})
    data = json.loads(resp.data)
    assert data["exists"] is False
