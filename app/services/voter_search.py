from flask import current_app
from sqlalchemy import text

from app import db
from app.models import Voter


def ensure_search_indexes():
    """Create pg_trgm extension and voter search indexes if they don't exist.

    Safe to call at every startup — all statements are idempotent.
    """
    db.session.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    db.session.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_voters_address_btree "
        "ON voters (residential_address1)"
    ))
    db.session.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_voters_address_trgm "
        "ON voters USING GIN (residential_address1 gin_trgm_ops)"
    ))
    db.session.execute(text(
        "CREATE INDEX IF NOT EXISTS idx_voters_name_trgm "
        "ON voters USING GIN (last_name gin_trgm_ops)"
    ))
    db.session.commit()


class VoterSearchService:
    """Service for searching voters using PostgreSQL full-text search with trigrams."""

    @staticmethod
    def search_by_address(address: str, limit: int = None) -> list[Voter]:
        """
        Search voters by residential address using trigram similarity.
        Optimized for real-time search performance.

        Args:
            address: Street address to search for
            limit: Maximum results to return

        Returns:
            List of Voter objects ordered by similarity score
        """
        if not address or len(address.strip()) < 3:
            return []

        if limit is None:
            limit = current_app.config.get("SEARCH_RESULTS_LIMIT", 250)

        address = address.strip().upper()

        # Optimized query: use ILIKE prefix match first (very fast with btree index),
        # fall back to trigram only if needed. This is much faster than pure trigram.
        sql = text("""
            SELECT * FROM (
                -- Fast prefix match first
                SELECT v.*, 1.0 AS score
                FROM voters v
                WHERE v.residential_address1 ILIKE :prefix
                LIMIT :limit
            ) fast
            UNION ALL
            SELECT * FROM (
                -- Trigram similarity for fuzzy matches
                SELECT v.*, similarity(v.residential_address1, :address) AS score
                FROM voters v
                WHERE v.residential_address1 % :address
                  AND v.residential_address1 NOT ILIKE :prefix
                ORDER BY score DESC
                LIMIT :limit
            ) fuzzy
            ORDER BY score DESC
            LIMIT :limit
        """)

        result = db.session.execute(
            sql,
            {"address": address, "prefix": address + "%", "limit": limit}
        )

        # Map directly to Voter objects without additional queries
        voters = []
        for row in result.mappings():
            voter = Voter(
                id=row["id"],
                sos_voterid=row["sos_voterid"],
                county_number=row["county_number"],
                first_name=row["first_name"],
                middle_name=row["middle_name"],
                last_name=row["last_name"],
                residential_address1=row["residential_address1"],
                residential_address2=row["residential_address2"],
                residential_city=row["residential_city"],
                residential_state=row["residential_state"],
                residential_zip=row["residential_zip"],
                city=row["city"],
            )
            voter.search_score = row["score"]
            voters.append(voter)

        return voters

    @staticmethod
    def search_by_name_and_address(
        first_name: str = None,
        last_name: str = None,
        address: str = None,
        limit: int = None
    ) -> list[Voter]:
        """
        Search voters by name and/or address using trigram similarity.

        Args:
            first_name: First name to search
            last_name: Last name to search
            address: Street address to search
            limit: Maximum results to return

        Returns:
            List of Voter objects ordered by combined similarity score
        """
        if limit is None:
            limit = current_app.config.get("SEARCH_RESULTS_LIMIT", 250)

        conditions = []
        params = {"limit": limit}

        score_parts = []

        if address and len(address.strip()) >= 3:
            conditions.append("residential_address1 % :address")
            params["address"] = address.strip()
            score_parts.append("similarity(residential_address1, :address)")

        if last_name and len(last_name.strip()) >= 2:
            conditions.append("last_name % :last_name")
            params["last_name"] = last_name.strip()
            score_parts.append("similarity(last_name, :last_name)")

        if first_name and len(first_name.strip()) >= 2:
            conditions.append("first_name % :first_name")
            params["first_name"] = first_name.strip()
            score_parts.append("similarity(first_name, :first_name)")

        if not conditions:
            return []

        # Average the similarity scores
        score_expr = " + ".join(score_parts)
        score_count = len(score_parts)

        sql = text(f"""
            SELECT *, ({score_expr}) / {score_count} AS score
            FROM voters
            WHERE {" OR ".join(conditions)}
            ORDER BY score DESC
            LIMIT :limit
        """)

        result = db.session.execute(sql, params)

        voters = []
        for row in result:
            voter = db.session.get(Voter, row.id)
            if voter:
                voter.search_score = row.score
                voters.append(voter)

        return voters

    @staticmethod
    def get_by_voter_id(sos_voterid: str) -> Voter | None:
        """Get a voter by their SOS Voter ID."""
        return Voter.query.filter_by(sos_voterid=sos_voterid).first()
