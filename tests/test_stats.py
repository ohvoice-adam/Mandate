"""Tests for StatsService SQL queries — exercises the actual PostgreSQL logic."""
import uuid

from conftest import make_user, make_collector, make_book, make_batch, make_voter, make_signature

from app import db
from app.models import Settings
from app.services import StatsService


# ---------------------------------------------------------------------------
# Progress stats
# ---------------------------------------------------------------------------

def test_progress_stats_empty_db_returns_zeros(app):
    stats = StatsService.get_progress_stats()
    assert stats["entered"] == 0
    assert stats["matched_target"] == 0
    assert stats["unmatched"] == 0
    assert stats["percent_verified"] == 0


def test_progress_stats_counts_matched_and_unmatched(app):
    Settings.set("target_city", "TESTVILLE CITY")
    collector = make_collector()
    book = make_book(collector.id)
    user = make_user()
    batch = make_batch(book.id, collector.id, user.id)

    voter_id = f"OH{uuid.uuid4().hex[:10].upper()}"
    # Matched target-city signature
    make_signature(
        book.id, batch.id,
        matched=True,
        sos_voterid=voter_id,
        registered_city="TESTVILLE CITY",
    )
    # No-match signature (no zip → counts as unmatched in SQL)
    make_signature(book.id, batch.id, matched=False)

    db.session.commit()

    stats = StatsService.get_progress_stats()
    assert stats["entered"] == 2
    assert stats["matched_target"] >= 1
    assert stats["unmatched"] >= 1


def test_progress_stats_deduplication_same_voter_same_batch(app):
    """Same sos_voterid in the same batch should be counted only once."""
    Settings.set("target_city", "TESTVILLE CITY")
    collector = make_collector()
    book = make_book(collector.id)
    user = make_user()
    batch = make_batch(book.id, collector.id, user.id)

    shared_id = f"OH{uuid.uuid4().hex[:10].upper()}"
    # Two signatures for the same voter in the same batch
    make_signature(book.id, batch.id, matched=True, sos_voterid=shared_id,
                   registered_city="TESTVILLE CITY")
    make_signature(book.id, batch.id, matched=True, sos_voterid=shared_id,
                   registered_city="TESTVILLE CITY")
    db.session.commit()

    stats = StatsService.get_progress_stats()
    # The deduplication query should count only 1, not 2
    assert stats["entered"] == 1


# ---------------------------------------------------------------------------
# Enterer stats
# ---------------------------------------------------------------------------

def test_enterer_stats_match_and_unmatched_pct_sum_to_100(app):
    collector = make_collector()
    user = make_user(first_name="Alice", last_name="Enterer",
                     email="alice@test.example")
    book = make_book(collector.id)
    batch = make_batch(book.id, collector.id, user.id)

    voter_id = f"OH{uuid.uuid4().hex[:10].upper()}"
    make_signature(book.id, batch.id, matched=True, sos_voterid=voter_id)
    make_signature(book.id, batch.id, matched=False)
    make_signature(book.id, batch.id, matched=False)
    db.session.commit()

    stats = StatsService.get_enterer_stats()
    assert len(stats) == 1
    row = stats[0]
    assert row["total"] == 3
    assert row["matched"] == 1
    assert row["unmatched"] == 2
    # With rounding, sum should be within 1.0 of 100
    total_pct = row["match_pct"] + row["unmatched_pct"]
    assert abs(total_pct - 100.0) <= 1.0


def test_enterer_stats_empty_db_returns_empty_list(app):
    stats = StatsService.get_enterer_stats()
    assert stats == []


# ---------------------------------------------------------------------------
# Collector stats
# ---------------------------------------------------------------------------

def test_collector_stats_excludes_collectors_with_no_signatures(app):
    """HAVING COUNT > 0 means collectors with no sigs don't appear."""
    make_collector(first_name="Ghost", last_name="Collector")
    db.session.commit()

    stats = StatsService.get_collector_stats()
    assert all(r["total"] > 0 for r in stats)


def test_collector_stats_match_and_unmatched_pct_sum_to_100(app):
    collector = make_collector()
    user = make_user()
    book = make_book(collector.id)
    batch = make_batch(book.id, collector.id, user.id)

    voter_id = f"OH{uuid.uuid4().hex[:10].upper()}"
    make_signature(book.id, batch.id, matched=True, sos_voterid=voter_id)
    make_signature(book.id, batch.id, matched=False)
    db.session.commit()

    stats = StatsService.get_collector_stats()
    assert len(stats) == 1
    row = stats[0]
    assert abs((row["match_pct"] + row["unmatched_pct"]) - 100.0) <= 1.0


def test_collector_stats_cross_book_duplicates(app):
    """A voter appearing in two books should be flagged as a duplicate."""
    collector = make_collector()
    user = make_user()
    book1 = make_book(collector.id, book_number="DUP001")
    book2 = make_book(collector.id, book_number="DUP002")
    batch1 = make_batch(book1.id, collector.id, user.id, book_number="DUP001")
    batch2 = make_batch(book2.id, collector.id, user.id, book_number="DUP002")

    shared_voter_id = f"OH{uuid.uuid4().hex[:10].upper()}"
    make_signature(book1.id, batch1.id, matched=True, sos_voterid=shared_voter_id)
    make_signature(book2.id, batch2.id, matched=True, sos_voterid=shared_voter_id)
    db.session.commit()

    stats = StatsService.get_collector_stats()
    assert len(stats) == 1
    assert stats[0]["duplicates"] > 0
    assert stats[0]["duplicate_pct"] > 0
