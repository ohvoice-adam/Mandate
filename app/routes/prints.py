"""
Petition print routes — upload PDF templates and generate serialized booklets.

PDF generation uses PyMuPDF (fitz) to stamp serial numbers onto the uploaded
cover and petition page templates.  The resulting PDF is stored base64-encoded
in the ``petition_print_jobs`` DB table and served back via a ``Response``
object with ``Content-Disposition: attachment``.

Flask concepts used here:
- **current_user**: Flask-Login proxy for the logged-in User — used to record
  ``generated_by_id`` on each print job.
- **Response(bytes, mimetype=..., headers=...)**: used to stream the binary
  PDF to the browser rather than rendering an HTML template.
"""

import base64
from datetime import datetime

from flask import Blueprint, Response, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.models import admin_required
from app.models.print_job import PetitionPrintJob
from app.models.settings import Settings
from app.services.pdf_print import (
    generate_petition_pdf,
    get_cover_bytes,
    get_highest_printed,
    get_petition_bytes,
    get_template_config,
)

bp = Blueprint("prints", __name__)


@bp.route("/", methods=["GET"])
@login_required
@admin_required
def index():
    template_config = get_template_config()
    highest = get_highest_printed()
    next_start = highest + 1
    jobs = PetitionPrintJob.query.order_by(PetitionPrintJob.created_at.desc()).all()
    allow_pdf_deletion = Settings.get("allow_pdf_deletion", "false") == "true"
    return render_template(
        "prints/index.html",
        template_config=template_config,
        next_start=next_start,
        jobs=jobs,
        allow_pdf_deletion=allow_pdf_deletion,
    )


@bp.route("/save-templates", methods=["POST"])
@login_required
@admin_required
def save_templates():
    saved = []
    saved_sizes = {}

    def _validate_and_read_pdf(file_storage, label):
        """Read and validate a PDF upload. Returns bytes or None on error."""
        if not file_storage or not file_storage.filename:
            return None
        if not file_storage.filename.lower().endswith(".pdf"):
            flash(f"{label} must be a PDF file.", "error")
            return None
        content = file_storage.read()
        try:
            import fitz
            doc = fitz.open(stream=content, filetype="pdf")
            if doc.page_count < 1:
                raise ValueError("PDF has no pages")
            doc.close()
        except Exception:
            flash(f"{label} is not a valid PDF.", "error")
            return None
        return content

    cover_file = request.files.get("cover_pdf")
    if cover_file and cover_file.filename:
        content = _validate_and_read_pdf(cover_file, "Cover file")
        if content is None:
            return redirect(url_for("prints.index"))
        saved_sizes["cover"] = len(content)
        Settings.set("petition_cover_pdf", base64.b64encode(content).decode())
        Settings.set("petition_cover_pdf_name", cover_file.filename)
        saved.append("cover")

    petition_file = request.files.get("petition_pdf")
    if petition_file and petition_file.filename:
        content = _validate_and_read_pdf(petition_file, "Petition file")
        if content is None:
            return redirect(url_for("prints.index"))
        saved_sizes["petition"] = len(content)
        Settings.set("petition_page_pdf", base64.b64encode(content).decode())
        Settings.set("petition_page_pdf_name", petition_file.filename)
        saved.append("petition")

    if saved:
        size_info = ", ".join(f"{k} ({saved_sizes[k]:,} bytes)" for k in saved)
        flash(f"Saved template(s): {size_info}", "success")
    else:
        flash("No files selected", "warning")

    return redirect(url_for("prints.index"))


@bp.route("/generate", methods=["POST"])
@login_required
@admin_required
def generate():
    template_config = get_template_config()
    if not template_config["has_cover"] or not template_config["has_petition"]:
        flash("Both cover and petition templates must be uploaded before generating.", "error")
        return redirect(url_for("prints.index"))

    try:
        start_number = int(request.form["start_number"])
        end_number = int(request.form["end_number"])
    except (KeyError, ValueError):
        flash("Invalid serial number range.", "error")
        return redirect(url_for("prints.index"))

    if start_number < 1:
        flash("Start number must be at least 1.", "error")
        return redirect(url_for("prints.index"))
    if end_number < start_number:
        flash("End number must be greater than or equal to start number.", "error")
        return redirect(url_for("prints.index"))
    if (end_number - start_number + 1) > 500:
        flash("Cannot generate more than 500 books at once.", "error")
        return redirect(url_for("prints.index"))

    overlapping = PetitionPrintJob.query.filter(
        PetitionPrintJob.start_number <= end_number,
        PetitionPrintJob.end_number >= start_number,
    ).order_by(PetitionPrintJob.start_number).all()
    if overlapping:
        ranges = ", ".join(f"#{j.start_number:05d}–#{j.end_number:05d}" for j in overlapping)
        flash(
            f"Serial number range overlaps with existing job(s): {ranges}. "
            "Choose a non-overlapping range or delete the conflicting job(s) first.",
            "error",
        )
        return redirect(url_for("prints.index"))

    cover_bytes = get_cover_bytes()
    petition_bytes = get_petition_bytes()

    try:
        pdf_bytes, page_count = generate_petition_pdf(cover_bytes, petition_bytes, start_number, end_number)
    except Exception:
        current_app.logger.exception("PDF generation failed for range %d–%d", start_number, end_number)
        flash("PDF generation failed. Check the application logs.", "error")
        return redirect(url_for("prints.index"))

    filename = f"petition-books-{start_number:05d}-{end_number:05d}-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.pdf"

    job = PetitionPrintJob(
        start_number=start_number,
        end_number=end_number,
        page_count=page_count,
        generated_by_id=current_user.id,
        filename=filename,
        pdf_content=base64.b64encode(pdf_bytes).decode(),
    )
    db.session.add(job)
    db.session.commit()

    flash(
        f"Generated {job.book_count} book(s) (serials #{start_number:05d}–#{end_number:05d}), {page_count} pages total.",
        "success",
    )
    return redirect(url_for("prints.index"))


@bp.route("/download/<int:job_id>", methods=["GET"])
@login_required
@admin_required
def download(job_id):
    job = db.session.get(PetitionPrintJob, job_id)
    if not job:
        flash("Print job not found.", "error")
        return redirect(url_for("prints.index"))
    return Response(
        job.get_pdf_bytes(),
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{job.filename}"'},
    )


@bp.route("/delete/<int:job_id>", methods=["GET"])
@login_required
@admin_required
def delete_confirm(job_id):
    if Settings.get("allow_pdf_deletion", "false") != "true":
        flash("PDF deletion is disabled. Enable it in Settings.", "error")
        return redirect(url_for("prints.index"))
    job = db.session.get(PetitionPrintJob, job_id)
    if not job:
        flash("Print job not found.", "error")
        return redirect(url_for("prints.index"))
    return render_template("prints/delete_confirm.html", job=job)


@bp.route("/delete/<int:job_id>", methods=["POST"])
@login_required
@admin_required
def delete(job_id):
    if Settings.get("allow_pdf_deletion", "false") != "true":
        flash("PDF deletion is disabled. Enable it in Settings.", "error")
        return redirect(url_for("prints.index"))
    job = db.session.get(PetitionPrintJob, job_id)
    if not job:
        flash("Print job not found.", "error")
        return redirect(url_for("prints.index"))
    db.session.delete(job)
    db.session.commit()
    flash(f"Deleted {job.filename}.", "success")
    return redirect(url_for("prints.index"))
