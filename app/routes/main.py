from datetime import date

from flask import Blueprint, render_template, redirect, url_for, request, session, flash
from flask_login import login_required, current_user

from app import db
from app.models import Book, Batch, Collector

bp = Blueprint("main", __name__)


@bp.route("/")
@login_required
def index():
    """Home page - session setup for data entry."""
    collectors = Collector.query.order_by(Collector.last_name, Collector.first_name).all()

    # Get current session info if set
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

    # Find or create the book
    book = Book.query.filter_by(book_number=book_number).first()
    if not book:
        book = Book(
            book_number=book_number,
            collector_id=collector_id,
            date_out=date_out,
            date_back=date_back,
        )
        db.session.add(book)
        db.session.commit()
    else:
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

    # Store session info
    session["book_id"] = book.id
    session["batch_id"] = batch.id
    session["book_number"] = book_number

    flash(f"Started session for Book {book_number}", "success")
    return redirect(url_for("signatures.entry"))


@bp.route("/check-book", methods=["POST"])
@login_required
def check_book():
    """Check if a book number already exists."""
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
