import csv
import io
from datetime import date

from flask import Blueprint, render_template, request, Response
from flask_login import login_required
from sqlalchemy import text

from app import db
from app.models import Settings
from app.services import StatsService

bp = Blueprint("stats", __name__)


@bp.route("/")
@login_required
def index():
    """Main statistics dashboard."""
    progress = StatsService.get_progress_stats()
    signature_goal = Settings.get_signature_goal()
    return render_template("stats/index.html", progress=progress, signature_goal=signature_goal)


@bp.route("/enterers")
@login_required
def enterers():
    """Data enterer performance statistics."""
    enterer_stats = StatsService.get_enterer_stats()
    return render_template("stats/enterers.html", stats=enterer_stats)


@bp.route("/organizations")
@login_required
def organizations():
    """Organization performance statistics."""
    org_stats = StatsService.get_organization_stats()
    return render_template("stats/organizations.html", stats=org_stats)


@bp.route("/export-matched.csv")
@login_required
def export_matched_csv():
    """Download matched signatures as a CSV including sos_voterid and voter names."""
    rows = db.session.execute(text("""
        SELECT
            s.sos_voterid,
            v.first_name,
            v.last_name,
            s.residential_address1,
            s.residential_address2,
            s.residential_city,
            s.residential_state,
            s.residential_zip,
            s.registered_city,
            b.book_number,
            c.first_name  AS collector_first,
            c.last_name   AS collector_last,
            s.created_at
        FROM (
            -- Mirror the stats deduplication: one row per (sos_voterid, batch_id),
            -- preferring matched=TRUE then lowest id, so the CSV row count matches
            -- the stats page totals.
            SELECT DISTINCT ON (sos_voterid, batch_id) *
            FROM signatures
            WHERE sos_voterid IS NOT NULL AND sos_voterid <> ''
            ORDER BY sos_voterid, batch_id, matched DESC, id
        ) s
        LEFT JOIN voters     v ON v.sos_voterid = s.sos_voterid
        LEFT JOIN books      b ON b.id = s.book_id
        LEFT JOIN collectors c ON c.id = b.collector_id
        WHERE s.matched = TRUE
        ORDER BY b.book_number, s.id
    """)).fetchall()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "sos_voterid",
        "first_name",
        "last_name",
        "full_address",
        "address1",
        "address2",
        "city",
        "state",
        "zip",
        "registered_city",
        "book_number",
        "collector",
        "date_entered",
    ])
    for r in rows:
        collector = " ".join(filter(None, [r.collector_first, r.collector_last]))
        street = " ".join(filter(None, [r.residential_address1, r.residential_address2]))
        city_state_zip = ", ".join(filter(None, [
            r.residential_city,
            " ".join(filter(None, [r.residential_state, r.residential_zip])),
        ]))
        full_address = ", ".join(filter(None, [street, city_state_zip]))
        writer.writerow([
            r.sos_voterid or "",
            r.first_name or "",
            r.last_name or "",
            full_address,
            r.residential_address1 or "",
            r.residential_address2 or "",
            r.residential_city or "",
            r.residential_state or "",
            r.residential_zip or "",
            r.registered_city or "",
            r.book_number or "",
            collector,
            r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
        ])

    filename = f"matched-signatures-{date.today()}.csv"
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@bp.route("/books")
@login_required
def books():
    """Per-book signature counts and validity rates."""
    sort = request.args.get("sort", "book_number")
    if sort not in ("book_number", "entry_time"):
        sort = "book_number"

    direction = request.args.get("dir", "desc")
    if direction not in ("asc", "desc"):
        direction = "desc"

    book_stats = StatsService.get_book_stats(sort=sort, direction=direction)
    return render_template(
        "stats/books.html",
        books=book_stats,
        sort=sort,
        direction=direction,
    )
