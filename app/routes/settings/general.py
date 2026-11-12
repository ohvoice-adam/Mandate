"""Main settings page: target city, signature goal, PDF deletion toggle."""

from typing import Any

from flask import flash, redirect, render_template, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import login_required
from sqlalchemy import text

from app import db
from app.models import Settings, admin_required
from app.routes.settings import bp
from app.services import backup as backup_service
from app.services import email as email_service


@bp.route("/", methods=["GET", "POST"])
@login_required
@admin_required
def index() -> ResponseReturnValue:
    """Application settings page."""
    if request.method == "POST":
        error = _save_general_form()
        if error:
            flash(error, "error")
        else:
            flash("Settings saved", "success")
        return redirect(url_for("settings.index"))

    return render_template("settings/index.html", **_index_context())


def _save_general_form() -> str | None:
    """Persist the general settings form; return an error message or None.

    On an invalid signature goal nothing after the target city is saved,
    matching the original behaviour.
    """
    target_city = request.form.get("target_city")
    if target_city:
        Settings.set("target_city", target_city.upper())

    signature_goal = request.form.get("signature_goal", "").strip()
    if signature_goal:
        try:
            goal = int(signature_goal)
        except ValueError:
            return "Signature goal must be a number"
        if goal >= 0:
            Settings.set_signature_goal(goal)

    allow_pdf_deletion = "true" if request.form.get("allow_pdf_deletion") else "false"
    Settings.set("allow_pdf_deletion", allow_pdf_deletion)
    return None


def _index_context() -> dict[str, Any]:
    """Collect every template variable the settings page needs."""
    from app.services.fonts import BODY_FONTS, HEADLINE_FONTS

    return {
        "current_city": Settings.get_target_city(),
        "cities": get_distinct_cities(),
        "signature_goal": Settings.get_signature_goal(),
        "allow_pdf_deletion": Settings.get("allow_pdf_deletion", "false") == "true",
        "backup_config": Settings.get_backup_config(),
        "backup_configured": backup_service.is_configured(),
        "smtp_config": Settings.get_smtp_config(),
        "smtp_configured": email_service.is_configured(),
        "notify_config": Settings.get_backup_notify_config(),
        "site_url": Settings.get("site_url", ""),
        "branding_config": Settings.get_branding_config(),
        "branding_fonts": Settings.get_branding_fonts(),
        "headline_font_options": [(name, name) for name, _, _ in HEADLINE_FONTS],
        "body_font_options": [(name, name) for name, _, _ in BODY_FONTS],
        "font_specs": {name: spec for name, spec, _ in HEADLINE_FONTS + BODY_FONTS},
    }


def get_distinct_cities() -> list[dict]:
    """Get distinct cities from the voter file, grouping county-file naming variants.

    Some counties append " City" or "-City" to municipality names (e.g. "Columbus City",
    "Grove City-City"). This function merges those alias forms into a single entry using
    the bare name as the canonical value, and combines their voter counts.
    """
    from app.models.settings import _CITY_SUFFIXES

    rows = db.session.execute(text("""
        SELECT upper(city) AS city_upper, COUNT(*) AS count
        FROM voters
        WHERE city IS NOT NULL AND city != ''
        GROUP BY upper(city)
        ORDER BY count DESC
    """)).fetchall()

    city_count = {row.city_upper: row.count for row in rows}
    all_cities = set(city_count.keys())

    # Map each city string to its canonical (bare) form.
    # A city is treated as a variant of another when stripping one suffix
    # yields a name that also exists in the voter data.
    canonical_of: dict[str, str] = {}
    for city in all_cities:
        for suffix in _CITY_SUFFIXES:
            if city.endswith(suffix):
                base = city[: -len(suffix)]
                if base in all_cities:
                    canonical_of[city] = base
                    break
        if city not in canonical_of:
            canonical_of[city] = city

    # Sum counts under each canonical name
    groups: dict[str, int] = {}
    for city, count in city_count.items():
        canonical = canonical_of[city]
        groups[canonical] = groups.get(canonical, 0) + count

    return sorted(
        [
            {"value": canonical, "label": canonical.title(), "count": count}
            for canonical, count in groups.items()
        ],
        key=lambda x: -x["count"],
    )
