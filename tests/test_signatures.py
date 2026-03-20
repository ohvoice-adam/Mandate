"""Tests for signature recording: match, address-only, no-match, undo, ownership."""
from conftest import make_user, make_collector, make_book, make_batch, make_voter, make_signature, login

from app import db


def _setup_session(client, user, book, batch):
    """Helper: log in and inject session vars for signature entry."""
    login(client, user)
    with client.session_transaction() as sess:
        sess["book_id"] = book.id
        sess["batch_id"] = batch.id
        sess["book_number"] = book.book_number


# ---------------------------------------------------------------------------
# Entry page — requires active session
# ---------------------------------------------------------------------------

def test_entry_requires_session(client, app):
    user = make_user()
    db.session.commit()
    login(client, user)
    resp = client.get("/signatures/", follow_redirects=False)
    assert resp.status_code == 302


# ---------------------------------------------------------------------------
# record-match
# ---------------------------------------------------------------------------

def test_record_match_creates_signature(client, app):
    user = make_user()
    collector = make_collector()
    book = make_book(collector.id, book_number="BK001")
    batch = make_batch(book.id, collector.id, user.id, book_number="BK001")
    voter = make_voter()
    db.session.commit()
    _setup_session(client, user, book, batch)

    resp = client.post(
        "/signatures/record-match",
        data={"voter_id": voter.id},
    )
    assert resp.status_code == 200

    db.session.expire_all()
    from app.models import Signature
    sig = Signature.query.filter_by(batch_id=batch.id).first()
    assert sig is not None
    assert sig.matched is True
    assert sig.sos_voterid == voter.sos_voterid


def test_record_match_response_contains_person_match(client, app):
    user = make_user()
    collector = make_collector()
    book = make_book(collector.id)
    batch = make_batch(book.id, collector.id, user.id)
    voter = make_voter()
    db.session.commit()
    _setup_session(client, user, book, batch)

    resp = client.post("/signatures/record-match", data={"voter_id": voter.id})
    assert b"Person Match" in resp.data


def test_record_match_no_session_returns_400(client, app):
    user = make_user()
    db.session.commit()
    login(client, user)
    # No session vars set → ownership check fails
    resp = client.post("/signatures/record-match", data={"voter_id": "1"})
    assert resp.status_code == 400


def test_record_match_wrong_owner_returns_400(client, app):
    """Batch owned by a different user → 400."""
    owner = make_user(email="owner@test.example")
    attacker = make_user(email="attacker@test.example")
    collector = make_collector()
    book = make_book(collector.id)
    # Batch belongs to owner, not attacker
    batch = make_batch(book.id, collector.id, owner.id)
    voter = make_voter()
    db.session.commit()

    # Log in as attacker but inject the owner's batch_id
    login(client, attacker)
    with client.session_transaction() as sess:
        sess["book_id"] = book.id
        sess["batch_id"] = batch.id
        sess["book_number"] = book.book_number

    resp = client.post("/signatures/record-match", data={"voter_id": voter.id})
    assert resp.status_code == 400


def test_record_match_dupe_in_batch_flagged(client, app):
    """Recording the same voter twice in one batch shows a duplicate warning."""
    user = make_user()
    collector = make_collector()
    book = make_book(collector.id)
    batch = make_batch(book.id, collector.id, user.id)
    voter = make_voter()
    db.session.commit()
    _setup_session(client, user, book, batch)

    # First entry
    client.post("/signatures/record-match", data={"voter_id": voter.id})
    # Second entry — duplicate
    resp = client.post("/signatures/record-match", data={"voter_id": voter.id})
    assert b"Duplicate" in resp.data or b"duplicate" in resp.data


# ---------------------------------------------------------------------------
# record-address-only
# ---------------------------------------------------------------------------

def test_record_address_only_sets_matched_false(client, app):
    user = make_user()
    collector = make_collector()
    book = make_book(collector.id)
    batch = make_batch(book.id, collector.id, user.id)
    voter = make_voter()
    db.session.commit()
    _setup_session(client, user, book, batch)

    resp = client.post("/signatures/record-address-only", data={"voter_id": voter.id})
    assert resp.status_code == 200

    db.session.expire_all()
    from app.models import Signature
    sig = Signature.query.filter_by(batch_id=batch.id).first()
    assert sig is not None
    assert sig.matched is False
    assert sig.sos_voterid == voter.sos_voterid  # voter data is still recorded


def test_record_address_only_response_contains_address_only(client, app):
    user = make_user()
    collector = make_collector()
    book = make_book(collector.id)
    batch = make_batch(book.id, collector.id, user.id)
    voter = make_voter()
    db.session.commit()
    _setup_session(client, user, book, batch)

    resp = client.post("/signatures/record-address-only", data={"voter_id": voter.id})
    assert b"Address Only" in resp.data


# ---------------------------------------------------------------------------
# record-no-match
# ---------------------------------------------------------------------------

def test_record_no_match_creates_signature_without_voter(client, app):
    user = make_user()
    collector = make_collector()
    book = make_book(collector.id)
    batch = make_batch(book.id, collector.id, user.id)
    db.session.commit()
    _setup_session(client, user, book, batch)

    resp = client.post("/signatures/record-no-match")
    assert resp.status_code == 200

    db.session.expire_all()
    from app.models import Signature
    sig = Signature.query.filter_by(batch_id=batch.id).first()
    assert sig is not None
    assert sig.matched is False
    assert sig.sos_voterid is None


def test_record_no_match_response_contains_no_match(client, app):
    user = make_user()
    collector = make_collector()
    book = make_book(collector.id)
    batch = make_batch(book.id, collector.id, user.id)
    db.session.commit()
    _setup_session(client, user, book, batch)

    resp = client.post("/signatures/record-no-match")
    assert b"No Match" in resp.data


# ---------------------------------------------------------------------------
# undo-last
# ---------------------------------------------------------------------------

def test_undo_last_removes_most_recent_signature(client, app):
    user = make_user()
    collector = make_collector()
    book = make_book(collector.id)
    batch = make_batch(book.id, collector.id, user.id)
    voter = make_voter()
    sig = make_signature(book.id, batch.id, matched=True, sos_voterid=voter.sos_voterid)
    db.session.commit()
    _setup_session(client, user, book, batch)

    resp = client.post("/signatures/undo-last")
    assert resp.status_code == 200
    assert b"Last entry removed" in resp.data

    db.session.expire_all()
    from app.models import Signature
    assert Signature.query.filter_by(batch_id=batch.id).count() == 0


def test_undo_last_empty_batch_returns_nothing_to_undo(client, app):
    user = make_user()
    collector = make_collector()
    book = make_book(collector.id)
    batch = make_batch(book.id, collector.id, user.id)
    db.session.commit()
    _setup_session(client, user, book, batch)

    resp = client.post("/signatures/undo-last")
    assert resp.status_code == 200
    assert b"Nothing to undo" in resp.data


def test_undo_last_wrong_owner_returns_400(client, app):
    owner = make_user(email="owner2@test.example")
    attacker = make_user(email="attacker2@test.example")
    collector = make_collector()
    book = make_book(collector.id)
    batch = make_batch(book.id, collector.id, owner.id)
    db.session.commit()

    login(client, attacker)
    with client.session_transaction() as sess:
        sess["book_id"] = book.id
        sess["batch_id"] = batch.id
        sess["book_number"] = book.book_number

    resp = client.post("/signatures/undo-last")
    assert resp.status_code == 400
