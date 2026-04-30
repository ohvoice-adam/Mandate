"""
Batch management routes — session history and rollback.

Tier 1: Enterers view their own session history and can roll back completed batches.
Tier 2: Organizers/admins view all batches and can roll back any of them.
Tier 3: Every rollback writes a BatchEvent audit record.
"""

from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, flash, abort, request
from flask_login import login_required, current_user

from app import db
from app.models import Batch, BatchEvent, Signature
from app.models import organizer_required

bp = Blueprint("batches", __name__)


@bp.route("/my-sessions")
@login_required
def my_sessions():
    """Enterer's personal session history."""
    batches = (
        Batch.query
        .filter_by(enterer_id=current_user.id)
        .order_by(Batch.created_at.desc())
        .all()
    )
    sig_counts = _sig_counts_for(batches)
    return render_template("batches/my_sessions.html", batches=batches, sig_counts=sig_counts)


@bp.route("/")
@organizer_required
def manage():
    """Organizer/admin view of all batches."""
    page = request.args.get("page", 1, type=int)
    enterer_id = request.args.get("enterer_id", type=int)
    status_filter = request.args.get("status", "")

    query = Batch.query.order_by(Batch.created_at.desc())
    if enterer_id:
        query = query.filter_by(enterer_id=enterer_id)
    if status_filter:
        query = query.filter_by(status=status_filter)

    pagination = query.paginate(page=page, per_page=50, error_out=False)
    batches = pagination.items
    sig_counts = _sig_counts_for(batches)

    # Enterers for the filter dropdown
    from app.models import User
    enterers = (
        User.query
        .filter(User.id.in_(db.session.query(Batch.enterer_id).distinct()))
        .order_by(User.last_name, User.first_name)
        .all()
    )

    return render_template(
        "batches/manage.html",
        batches=batches,
        sig_counts=sig_counts,
        pagination=pagination,
        enterers=enterers,
        enterer_id=enterer_id,
        status_filter=status_filter,
    )


@bp.route("/<int:batch_id>/confirm-rollback")
@login_required
def confirm_rollback(batch_id):
    """HTMX endpoint — returns the confirmation fragment."""
    batch = db.session.get(Batch, batch_id)
    if not batch:
        abort(404)
    _check_rollback_access(batch)
    count = Signature.query.filter_by(batch_id=batch.id).count()
    return render_template("batches/_confirm_rollback.html", batch=batch, count=count)


@bp.route("/<int:batch_id>/rollback", methods=["POST"])
@login_required
def rollback(batch_id):
    """Delete all signatures in a batch and mark it rolled_back."""
    batch = db.session.get(Batch, batch_id)
    if not batch:
        abort(404)

    _check_rollback_access(batch)

    if not batch.can_rollback:
        flash("This session cannot be rolled back (it may already be rolled back or is still open).", "error")
        return _rollback_redirect()

    count = Signature.query.filter_by(batch_id=batch.id).count()
    Signature.query.filter_by(batch_id=batch.id).delete(synchronize_session=False)

    batch.status = "rolled_back"

    event = BatchEvent(
        batch_id=batch.id,
        action="rolled_back",
        performed_by_id=current_user.id,
        performed_at=datetime.utcnow(),
        signatures_deleted=count,
    )
    db.session.add(event)
    db.session.commit()

    flash(
        f"Session for Book {batch.book_number} rolled back — {count} {'entry' if count == 1 else 'entries'} removed.",
        "success",
    )
    return _rollback_redirect()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _check_rollback_access(batch):
    """Abort 403 if the current user may not touch this batch."""
    if current_user.is_admin_or_organizer:
        return
    if batch.enterer_id != current_user.id:
        abort(403)


def _rollback_redirect():
    """Redirect back to the page that initiated the rollback."""
    if current_user.is_admin_or_organizer:
        return redirect(url_for("batches.manage"))
    return redirect(url_for("batches.my_sessions"))


def _sig_counts_for(batches):
    """Return a dict of {batch_id: signature_count} for a list of batches."""
    if not batches:
        return {}
    ids = [b.id for b in batches]
    from sqlalchemy import func
    rows = (
        db.session.query(Signature.batch_id, func.count(Signature.id))
        .filter(Signature.batch_id.in_(ids))
        .group_by(Signature.batch_id)
        .all()
    )
    return {batch_id: count for batch_id, count in rows}
