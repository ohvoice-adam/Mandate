"""
Main routes — the data-entry home page and session management.

Flask concepts used here:
- **session[key]**: Flask's signed-cookie session dict.  Values stored here
  persist across requests for one browser session.  Unlike a plain cookie,
  the session is tamper-proof because Flask signs it with SECRET_KEY — but
  the data is still visible to the client, so never store secrets in it.
- **request.form**: an ImmutableMultiDict populated from a submitted HTML
  form's POST body.  ``request.form.get("field")`` returns a string or None.
- **flash(msg, category)**: stores a one-time message in the session.
  ``base.html`` renders and then discards it on the next page load.
- **redirect(url_for(...))**: returns HTTP 302, sending the browser to a new
  URL resolved by the endpoint name rather than a hard-coded path.
"""

from datetime import date

from flask import Blueprint, render_template, redirect, url_for, request, session, flash
from flask_login import login_required, current_user
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import Book, Batch, Collector

bp = Blueprint("main", __name__)


@bp.route("/")
@login_required  # Redirects to auth.login if no authenticated user in session
def index():
    """Home page - session setup for data entry."""
    collectors = Collector.query.order_by(Collector.last_name, Collector.first_name).all()

    # session is Flask's signed-cookie dict — values survive page refreshes
    # because they're stored in the browser's session cookie (signed by
    # SECRET_KEY).  .get() returns None if the key hasn't been set yet.
    book_id = session.get("book_id")
    batch_id = session.get("batch_id")
    book_number = session.get("book_number")

    return render_template(
        "main/index.html",
        collectors=collectors,
        book_id=book_id,
        batch_id=batch_id,
        book_number=book_number,
        today=date.today().isoformat(),
    )


@bp.route("/start-session", methods=["POST"])
@login_required
def start_session():
    """Start a new data entry session."""
    book_number = request.form.get("book_number")
    collector_id = request.form.get("collector_id")
    date_out = request.form.get("date_out") or date.today().isoformat()
    date_back = request.form.get("date_back") or date.today().isoformat()

    if not book_number or not collector_id:
        flash("Please enter book number and select a collector", "error")
        return redirect(url_for("main.index"))

    if date_back < date_out:
        flash("Date In cannot be before Date Out.", "error")
        return redirect(url_for("main.index"))

    # Find or create the book (handle race condition with retry on IntegrityError)
    book = Book.query.filter_by(book_number=book_number).first()
    if not book:
        try:
            book = Book(
                book_number=book_number,
                collector_id=collector_id,
                date_out=date_out,
                date_back=date_back,
            )
            db.session.add(book)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            book = Book.query.filter_by(book_number=book_number).first()
            if not book:
                flash("Could not create book. Please try again.", "error")
                return redirect(url_for("main.index"))

    if book:
        # Update dates on existing book
        book.date_out = date_out
        book.date_back = date_back
        db.session.commit()

    # Create a new batch for this session
    batch = Batch(
        book_id=book.id,
        book_number=book_number,
        collector_id=collector_id,
        enterer_id=current_user.id,
        enterer_first=current_user.first_name,
        enterer_last=current_user.last_name,
        enterer_email=current_user.email,
        date_entered=date.today(),
    )
    db.session.add(batch)
    db.session.commit()

    # Store the active book/batch IDs in the session cookie so the
    # signatures blueprint can read them on subsequent requests.
    session["book_id"] = book.id
    session["batch_id"] = batch.id
    session["book_number"] = book_number

    flash(f"Started session for Book {book_number}", "success")
    # redirect() + url_for() = HTTP 302 to /signatures/ without hard-coding the path
    return redirect(url_for("signatures.entry"))


@bp.route("/check-book", methods=["POST"])
@login_required
def check_book():
    """
    HTMX endpoint — check if a book number already exists.

    Returns a plain dict, which Flask automatically converts to a JSON
    response (Content-Type: application/json).  HTMX reads this JSON to
    decide whether to warn the user about a duplicate book number.
    """
    book_number = request.form.get("book_number", "").strip()
    if not book_number:
        return {"exists": False}

    book = Book.query.filter_by(book_number=book_number).first()
    if book:
        open_batches = [b for b in book.batches if b.status == "open"]
        return {
            "exists": True,
            "book_number": book.book_number,
            "collector": book.collector.display_name if book.collector else "Unknown",
            "open_batches": len(open_batches),
        }
    return {"exists": False}


@bp.route("/end-session", methods=["POST"])
@login_required
def end_session():
    """End the current data entry session."""
    # session.pop() removes the key from the cookie and returns its value.
    # The None default prevents a KeyError if the key was never set.
    batch_id = session.pop("batch_id", None)
    session.pop("book_id", None)
    session.pop("book_number", None)

    if batch_id:
        batch = db.session.get(Batch, batch_id)
        if batch:
            batch.status = "complete"
            db.session.commit()

    flash("Session ended", "info")
    return redirect(url_for("main.index"))
