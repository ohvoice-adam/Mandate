"""
Organization management routes — CRUD for collector organizations.

Organizations group Collectors and Users.  Only organizers and admins can
manage organizations (@organizer_required).  An organization cannot be
deleted while it still has collectors or users assigned to it.
"""

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required

from app import db
from app.models import Organization, organizer_required

bp = Blueprint("organizations", __name__)


@bp.route("/")
@login_required
@organizer_required
def index():
    """List all organizations."""
    organizations = Organization.query.order_by(Organization.name).all()
    return render_template("organizations/index.html", organizations=organizations)


@bp.route("/new", methods=["GET", "POST"])
@login_required
@organizer_required
def new():
    """Create a new organization."""
    if request.method == "POST":
        name = request.form.get("name", "").strip()

        if not name:
            flash("Organization name is required", "error")
            return render_template("organizations/new.html")

        if Organization.query.filter_by(name=name).first():
            flash("Organization already exists", "error")
            return render_template("organizations/new.html")

        org = Organization(name=name)
        db.session.add(org)
        db.session.commit()

        flash(f"Organization '{org.name}' created successfully", "success")
        return redirect(url_for("organizations.index"))

    return render_template("organizations/new.html")


@bp.route("/<int:id>/edit", methods=["GET", "POST"])
@login_required
@organizer_required
def edit(id):
    """Edit an organization."""
    org = db.session.get(Organization, id)

    if not org:
        flash("Organization not found", "error")
        return redirect(url_for("organizations.index"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()

        if not name:
            flash("Organization name is required", "error")
            return render_template("organizations/edit.html", organization=org)

        # Check if name already exists for a different org
        existing = Organization.query.filter_by(name=name).first()
        if existing and existing.id != org.id:
            flash("Organization name already exists", "error")
            return render_template("organizations/edit.html", organization=org)

        org.name = name
        db.session.commit()

        flash(f"Organization '{org.name}' updated successfully", "success")
        return redirect(url_for("organizations.index"))

    return render_template("organizations/edit.html", organization=org)


@bp.route("/<int:id>/delete", methods=["POST"])
@login_required
@organizer_required
def delete(id):
    """Delete an organization."""
    org = db.session.get(Organization, id)

    if not org:
        flash("Organization not found", "error")
        return redirect(url_for("organizations.index"))

    # Check if organization has collectors or users
    if org.collectors:
        flash(f"Cannot delete organization with {len(org.collectors)} collector(s) assigned", "error")
        return redirect(url_for("organizations.index"))

    if org.users:
        flash(f"Cannot delete organization with {len(org.users)} user(s) assigned", "error")
        return redirect(url_for("organizations.index"))

    name = org.name
    db.session.delete(org)
    db.session.commit()

    flash(f"Organization '{name}' deleted", "success")
    return redirect(url_for("organizations.index"))
