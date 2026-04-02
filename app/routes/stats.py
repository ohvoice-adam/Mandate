"""
Statistics and export routes — dashboards, CSV exports, and book-level reports.

SQLAlchemy / Flask concepts used here:
- **text(sql)**: wraps a raw SQL string so SQLAlchemy passes it to the driver
  unchanged.  Named bind parameters (e.g. ``:city_pattern``) are escaped by
  psycopg2, which prevents SQL injection even though the surrounding query
  structure is built with f-strings.
- **db.session.execute(text(...), params)**: runs a raw SQL query and returns
  a ``CursorResult``; rows are ``Row`` objects with attribute access
  (``r.sos_voterid``, ``r.first_name``, etc.).
- **Response(csv_string, mimetype="text/csv", headers=...)**: used instead of
  ``render_template`` to return a file download rather than an HTML page.
  ``Content-Disposition: attachment`` tells the browser to save the file.
"""

import csv
import io
from datetime import date

from flask import Blueprint, render_template, request, Response
from flask_login import login_required
from sqlalchemy import bindparam, text

from app import db
from app.models import Settings
from app.models.collector import Collector
from app.models.user import organizer_required
from app.services import StatsService


def _export_filters():
    """Parse and validate common export filter params. Returns (filters_sql, params, label)."""
    date_from    = request.args.get("date_from", "").strip()
    date_to      = request.args.get("date_to", "").strip()
    collector_id = request.args.get("collector_id", "").strip()

    conditions = []
    params = {}
    label_parts = []

    if date_from:
        try:
            date.fromisoformat(date_from)
            conditions.append("s.created_at::date >= :date_from")
            params["date_from"] = date_from
            label_parts.append(f"from-{date_from}")
        except ValueError:
            pass

    if date_to:
        try:
            date.fromisoformat(date_to)
            conditions.append("s.created_at::date <= :date_to")
            params["date_to"] = date_to
            label_parts.append(f"to-{date_to}")
        except ValueError:
            pass

    if collector_id:
        try:
            params["collector_id"] = int(collector_id)
            conditions.append("c.id = :collector_id")
            label_parts.append(f"collector-{collector_id}")
        except ValueError:
            pass

    extra_sql = (" AND " + " AND ".join(conditions)) if conditions else ""
    label = ("-" + "-".join(label_parts)) if label_parts else ""
    return extra_sql, params, label

bp = Blueprint("stats", __name__)


@bp.route("/")
@login_required
def index():
    """Main statistics dashboard."""
    progress = StatsService.get_progress_stats()
    signature_goal = Settings.get_signature_goal()
    collectors = Collector.query.order_by(Collector.last_name, Collector.first_name).all()
    return render_template("stats/index.html", progress=progress, signature_goal=signature_goal, collectors=collectors)


@bp.route("/enterers")
@login_required
def enterers():
    """Data enterer performance statistics."""
    enterer_stats = StatsService.get_enterer_stats()
    return render_template("stats/enterers.html", stats=enterer_stats)


@bp.route("/collectors")
@login_required
def collectors():
    """Per-collector quality metrics."""
    collector_stats = StatsService.get_collector_stats()
    return render_template("stats/collectors.html", stats=collector_stats)


@bp.route("/organizations")
@login_required
def organizations():
    """Organization performance statistics."""
    org_stats = StatsService.get_organization_stats()
    return render_template("stats/organizations.html", stats=org_stats)


@bp.route("/export-matched.csv")
@organizer_required
def export_matched_csv():
    """Download matched signatures as a CSV including sos_voterid and voter names."""
    from app.models import Settings
    city_aliases = Settings.get_city_aliases()
    extra_sql, extra_params, label = _export_filters()

    rows = db.session.execute(text(f"""
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
            s.matched,
            (s.registered_city IN :city_aliases) AS columbus_resident,
            b.book_number,
            c.first_name  AS collector_first,
            c.last_name   AS collector_last,
            s.created_at
        FROM (
            SELECT DISTINCT ON (sos_voterid, batch_id) *
            FROM signatures
            WHERE sos_voterid IS NOT NULL AND sos_voterid <> ''
            ORDER BY sos_voterid, batch_id, matched DESC, id
        ) s
        LEFT JOIN voters     v ON v.sos_voterid = s.sos_voterid
        LEFT JOIN books      b ON b.id = s.book_id
        LEFT JOIN collectors c ON c.id = b.collector_id
        WHERE s.matched = TRUE{extra_sql}
        ORDER BY b.book_number, s.id
    """).bindparams(bindparam("city_aliases", expanding=True)), {"city_aliases": city_aliases, **extra_params}).fetchall()

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
        "matched",
        "columbus_resident",
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
            "Y" if r.matched else "N",
            "Y" if r.columbus_resident else "N",
            r.book_number or "",
            collector,
            r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
        ])

    filename = f"matched-signatures-{date.today()}{label}.csv"
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@bp.route("/export-duplicates.csv")
@organizer_required
def export_duplicates_csv():
    """Download voters whose sos_voterid appears in more than one book."""
    extra_sql, extra_params, label = _export_filters()

    rows = db.session.execute(text(f"""
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
            SELECT DISTINCT ON (sos_voterid, batch_id) *
            FROM signatures
            WHERE sos_voterid IS NOT NULL AND sos_voterid <> ''
              AND matched = TRUE
            ORDER BY sos_voterid, batch_id, matched DESC, id
        ) s
        LEFT JOIN voters     v ON v.sos_voterid = s.sos_voterid
        LEFT JOIN books      b ON b.id = s.book_id
        LEFT JOIN collectors c ON c.id = b.collector_id
        WHERE s.sos_voterid IN (
            SELECT sos_voterid
            FROM (
                SELECT DISTINCT ON (sos_voterid, batch_id) sos_voterid, book_id
                FROM signatures
                WHERE sos_voterid IS NOT NULL AND sos_voterid <> ''
                  AND matched = TRUE
                ORDER BY sos_voterid, batch_id, matched DESC, id
            ) deduped
            GROUP BY sos_voterid
            HAVING COUNT(DISTINCT book_id) > 1
        ){extra_sql}
        ORDER BY v.last_name, v.first_name, s.sos_voterid, b.book_number
    """), extra_params).fetchall()

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

    filename = f"duplicate-signatures-{date.today()}{label}.csv"
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
    if sort not in ("book_number", "entry_time", "last_activity"):
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
