from datetime import date

from flask import Blueprint, render_template, request, session, flash, redirect, url_for
from flask_login import login_required

from app import db
from app.models import Signature, Voter
from app.services import VoterSearchService

bp = Blueprint("signatures", __name__)


@bp.route("/")
@login_required
def entry():
    """Main signature entry page."""
    book_id = session.get("book_id")
    batch_id = session.get("batch_id")
    book_number = session.get("book_number")

    if not book_id or not batch_id:
        flash("Please start a session first", "warning")
        return redirect(url_for("main.index"))

    return render_template(
        "signatures/entry.html",
        book_id=book_id,
        batch_id=batch_id,
        book_number=book_number,
        today=date.today().isoformat(),
    )


@bp.route("/search", methods=["POST"])
@login_required
def search():
    """Search for voters by address (HTMX endpoint)."""
    address = request.form.get("address", "").strip()

    if len(address) < 3:
        return render_template("signatures/_results.html", voters=[], message="Enter at least 3 characters")

    voters = VoterSearchService.search_by_address(address)

    return render_template("signatures/_results.html", voters=voters)


@bp.route("/record-match", methods=["POST"])
@login_required
def record_match():
    """Record a signature match (person matched to voter record)."""
    book_id = session.get("book_id")
    batch_id = session.get("batch_id")

    if not book_id or not batch_id:
        return {"error": "No active session"}, 400

    voter_id = request.form.get("voter_id")
    voter = db.session.get(Voter, voter_id) if voter_id else None

    if not voter:
        return {"error": "Voter not found"}, 404

    dupe_in_batch = Signature.query.filter_by(batch_id=batch_id, sos_voterid=voter.sos_voterid).count() > 0
    dupe_cross_batch = not dupe_in_batch and Signature.query.filter(
        Signature.sos_voterid == voter.sos_voterid,
        Signature.batch_id != batch_id,
    ).count() > 0

    signature = Signature(
        book_id=book_id,
        batch_id=batch_id,
        sos_voterid=voter.sos_voterid,
        county_number=voter.county_number,
        residential_address1=voter.residential_address1,
        residential_address2=voter.residential_address2,
        residential_city=voter.residential_city,
        residential_state=voter.residential_state,
        residential_zip=voter.residential_zip,
        registered_city=voter.city,
        matched=True,
    )

    db.session.add(signature)
    db.session.commit()

    return render_template(
        "signatures/_success.html",
        message="Person Match recorded",
        voter=voter,
        match_type="person",
        dupe_in_batch=dupe_in_batch,
        dupe_cross_batch=dupe_cross_batch,
    )


@bp.route("/record-address-only", methods=["POST"])
@login_required
def record_address_only():
    """Record an address-only match (address matches but name doesn't)."""
    book_id = session.get("book_id")
    batch_id = session.get("batch_id")

    if not book_id or not batch_id:
        return {"error": "No active session"}, 400

    voter_id = request.form.get("voter_id")
    voter = db.session.get(Voter, voter_id) if voter_id else None

    if not voter:
        return {"error": "Voter not found"}, 404

    dupe_in_batch = Signature.query.filter_by(batch_id=batch_id, sos_voterid=voter.sos_voterid).count() > 0
    dupe_cross_batch = not dupe_in_batch and Signature.query.filter(
        Signature.sos_voterid == voter.sos_voterid,
        Signature.batch_id != batch_id,
    ).count() > 0

    signature = Signature(
        book_id=book_id,
        batch_id=batch_id,
        sos_voterid=voter.sos_voterid,
        county_number=voter.county_number,
        residential_address1=voter.residential_address1,
        residential_address2=voter.residential_address2,
        residential_city=voter.residential_city,
        residential_state=voter.residential_state,
        residential_zip=voter.residential_zip,
        registered_city=voter.city,
        matched=False,  # Address only = not a person match
    )

    db.session.add(signature)
    db.session.commit()

    return render_template(
        "signatures/_success.html",
        message="Address Only recorded",
        voter=voter,
        match_type="address",
        dupe_in_batch=dupe_in_batch,
        dupe_cross_batch=dupe_cross_batch,
    )


@bp.route("/undo-last", methods=["POST"])
@login_required
def undo_last():
    """Delete the most recently recorded signature in the current batch."""
    batch_id = session.get("batch_id")

    if not batch_id:
        return {"error": "No active session"}, 400

    last = (
        Signature.query
        .filter_by(batch_id=batch_id)
        .order_by(Signature.id.desc())
        .first()
    )

    if not last:
        return render_template("signatures/_undo.html", undone=False)

    db.session.delete(last)
    db.session.commit()

    return render_template("signatures/_undo.html", undone=True)


@bp.route("/record-no-match", methods=["POST"])
@login_required
def record_no_match():
    """Record a signature with no voter match found."""
    book_id = session.get("book_id")
    batch_id = session.get("batch_id")

    if not book_id or not batch_id:
        return {"error": "No active session"}, 400

    # Create signature with minimal info (no voter data)
    signature = Signature(
        book_id=book_id,
        batch_id=batch_id,
        matched=False,
    )

    db.session.add(signature)
    db.session.commit()

    return render_template(
        "signatures/_success.html",
        message="No Match recorded",
        voter=None,
        match_type="none"
    )
