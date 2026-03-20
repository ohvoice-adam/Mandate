from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app  # noqa: F401 (current_app used in exception handlers)
from flask_login import login_required

from app import db
from app.models import admin_required
from app.models.voter_import import VoterImport, ImportStatus
from app.services.voter_import import VoterImportService

bp = Blueprint("imports", __name__)


@bp.route("/", methods=["GET"])
@login_required
@admin_required
def index():
    """Import management page."""
    # Get all imports, most recent first
    imports = VoterImport.query.order_by(VoterImport.created_at.desc()).all()

    # Get all Ohio counties for the upload dropdown
    counties = VoterImportService.get_ohio_counties()

    # Get loaded counties for the delete dropdown
    loaded_counties = VoterImportService.get_loaded_counties()

    # Separate into active and completed
    active_imports = [i for i in imports if i.status in (ImportStatus.PENDING, ImportStatus.RUNNING)]
    completed_imports = [i for i in imports if i.status not in (ImportStatus.PENDING, ImportStatus.RUNNING)]

    return render_template(
        "imports/index.html",
        active_imports=active_imports,
        completed_imports=completed_imports,
        counties=counties,
        loaded_counties=loaded_counties,
    )


@bp.route("/upload", methods=["POST"])
@login_required
@admin_required
def upload():
    """Handle file upload and start import."""
    if "file" not in request.files:
        flash("No file selected", "error")
        return redirect(url_for("imports.index"))

    files = request.files.getlist("file")
    county_name = request.form.get("county_name", "").strip()

    if not county_name:
        flash("County name is required", "error")
        return redirect(url_for("imports.index"))

    if not files or all(f.filename == "" for f in files):
        flash("No file selected", "error")
        return redirect(url_for("imports.index"))

    imports_created = []
    for file in files:
        if file.filename == "":
            continue

        # Validate file extension
        if not file.filename.lower().endswith((".csv", ".txt", ".zip")):
            flash(f"Invalid file type: {file.filename}. Must be .csv, .txt, or .zip", "error")
            continue

        try:
            new_imports = VoterImportService.handle_upload(file, county_name, current_app._get_current_object())
            imports_created.extend(new_imports)
        except Exception:
            current_app.logger.exception("Error processing upload: %s", file.filename)
            flash("An error occurred processing the upload. Check the application logs.", "error")

    if imports_created:
        flash(f"Started {len(imports_created)} import(s)", "success")

    return redirect(url_for("imports.index"))


@bp.route("/<int:import_id>/status", methods=["GET"])
@login_required
@admin_required
def status(import_id):
    """Get import status as JSON for polling."""
    voter_import = db.session.get(VoterImport, import_id)
    if not voter_import:
        return jsonify({"error": "Import not found"}), 404

    return jsonify(voter_import.to_status_dict())


@bp.route("/<int:import_id>/cancel", methods=["POST"])
@login_required
@admin_required
def cancel(import_id):
    """Cancel a running import."""
    voter_import = db.session.get(VoterImport, import_id)
    if not voter_import:
        flash("Import not found", "error")
        return redirect(url_for("imports.index"))

    if voter_import.status not in (ImportStatus.RUNNING, ImportStatus.PENDING):
        flash("Import is not running", "error")
        return redirect(url_for("imports.index"))

    # Try to signal the running thread
    thread_signalled = VoterImportService.cancel_import(import_id)

    if thread_signalled:
        # Thread is alive — set DB flag and let the thread handle it
        voter_import.cancel_requested = True
        db.session.commit()
        flash("Cancellation requested", "info")
    else:
        # Thread is dead (e.g. process was killed) — force-cancel directly
        VoterImportService.force_cancel_import(import_id)
        flash("Import was orphaned and has been cancelled", "info")

    return redirect(url_for("imports.index"))


@bp.route("/<int:import_id>/rollback", methods=["POST"])
@login_required
@admin_required
def rollback(import_id):
    """Roll back a completed import."""
    voter_import = db.session.get(VoterImport, import_id)
    if not voter_import:
        flash("Import not found", "error")
        return redirect(url_for("imports.index"))

    if not voter_import.can_rollback:
        flash("This import cannot be rolled back", "error")
        return redirect(url_for("imports.index"))

    try:
        VoterImportService.rollback_import(import_id)
        flash("Import rolled back successfully", "success")
    except Exception:
        current_app.logger.exception("Rollback failed for import %d", import_id)
        flash("Rollback failed. Check the application logs.", "error")

    return redirect(url_for("imports.index"))


@bp.route("/<int:import_id>/cleanup", methods=["POST"])
@login_required
@admin_required
def cleanup(import_id):
    """Clean up backup table for a completed import."""
    voter_import = db.session.get(VoterImport, import_id)
    if not voter_import:
        flash("Import not found", "error")
        return redirect(url_for("imports.index"))

    try:
        VoterImportService.cleanup_backup(import_id)
        flash("Backup cleaned up", "success")
    except Exception:
        current_app.logger.exception("Cleanup failed for import %d", import_id)
        flash("Cleanup failed. Check the application logs.", "error")

    return redirect(url_for("imports.index"))


@bp.route("/delete-all", methods=["POST"])
@login_required
@admin_required
def delete_all():
    """Delete all voters."""
    try:
        deleted_count = VoterImportService.delete_all_voters()
        flash(f"Deleted {deleted_count:,} voters", "success")
    except Exception:
        current_app.logger.exception("delete_all_voters failed")
        flash("Delete failed. Check the application logs.", "error")

    return redirect(url_for("imports.index"))


@bp.route("/delete-county", methods=["POST"])
@login_required
@admin_required
def delete_county():
    """Delete all voters for a county."""
    county_name = request.form.get("county_name", "").strip()

    if not county_name:
        flash("County name is required", "error")
        return redirect(url_for("imports.index"))

    county_number = VoterImportService.get_county_number(county_name)
    if not county_number:
        flash(f"Unknown county: {county_name}", "error")
        return redirect(url_for("imports.index"))

    try:
        deleted_count = VoterImportService.delete_county(county_number)
        flash(f"Deleted {deleted_count:,} voters from {county_name} county", "success")
    except Exception:
        current_app.logger.exception("delete_county failed for %s", county_name)
        flash("Delete failed. Check the application logs.", "error")

    return redirect(url_for("imports.index"))
