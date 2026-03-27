import secrets

from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from itsdangerous import URLSafeTimedSerializer

from app import db
from app.models import User, UserRole, Organization, admin_required, organizer_required
from app.services import email as email_service
from app.utils import is_valid_email

bp = Blueprint("users", __name__)


@bp.route("/")
@login_required
@organizer_required
def index():
    """List all users."""
    users = User.query.order_by(User.last_name, User.first_name).all()
    return render_template("users/index.html", users=users)


@bp.route("/new", methods=["GET", "POST"])
@login_required
@organizer_required
def new():
    """Create a new user."""
    organizations = Organization.query.order_by(Organization.name).all()
    # Organizers cannot assign the Admin role
    available_roles = UserRole.CHOICES if current_user.is_admin else [
        c for c in UserRole.CHOICES if c[0] != UserRole.ADMIN
    ]

    smtp_configured = email_service.is_configured()

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        first_name = request.form.get("first_name")
        last_name = request.form.get("last_name")
        role = request.form.get("role", UserRole.ENTERER)
        org_id = request.form.get("organization_id")
        organization_id = int(org_id) if org_id else None

        # Organizers cannot assign admin role
        if not current_user.is_admin and role == UserRole.ADMIN:
            flash("You don't have permission to assign the Administrator role.", "error")
            return render_template("users/new.html", roles=available_roles, organizations=organizations, smtp_configured=smtp_configured)

        if not is_valid_email(email):
            flash("Invalid email address.", "error")
            return render_template("users/new.html", roles=available_roles, organizations=organizations, smtp_configured=smtp_configured)

        if User.query.filter_by(email=email).first():
            flash("Email already registered", "error")
            return render_template("users/new.html", roles=available_roles, organizations=organizations, smtp_configured=smtp_configured)

        user = User(
            email=email,
            first_name=first_name,
            last_name=last_name,
            role=role,
            organization_id=organization_id,
        )
        # Set a random unusable password — the user will set their own via the invite link
        user.set_password(secrets.token_hex(32))

        db.session.add(user)
        db.session.commit()

        # Generate a 72-hour invite token
        s = URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="account-setup")
        token = s.dumps({"id": user.id, "ph": user.password_hash[-8:]})
        invite_url = url_for("auth.setup_password", token=token, _external=True)

        # Send invite email if SMTP is configured
        email_sent = False
        if smtp_configured:
            try:
                email_service.send_invitation_email(user.email, invite_url, current_user.full_name)
                email_sent = True
            except Exception:
                current_app.logger.exception("Failed to send invite email to %s", user.email)

        return render_template(
            "users/invite_link.html",
            user=user,
            invite_url=invite_url,
            email_sent=email_sent,
            smtp_configured=smtp_configured,
        )

    return render_template("users/new.html", roles=available_roles, organizations=organizations, smtp_configured=smtp_configured)


@bp.route("/<int:id>/edit", methods=["GET", "POST"])
@login_required
@organizer_required
def edit(id):
    """Edit a user."""
    user = db.session.get(User, id)
    organizations = Organization.query.order_by(Organization.name).all()

    if not user:
        flash("User not found", "error")
        return redirect(url_for("users.index"))

    # Organizers cannot edit admin accounts
    if not current_user.is_admin and user.is_admin:
        flash("You don't have permission to edit an Administrator account.", "error")
        return redirect(url_for("users.index"))

    # Organizers cannot assign the Admin role
    available_roles = UserRole.CHOICES if current_user.is_admin else [
        c for c in UserRole.CHOICES if c[0] != UserRole.ADMIN
    ]

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        if not is_valid_email(email):
            flash("Invalid email address.", "error")
            return render_template("users/edit.html", user=user, roles=available_roles, organizations=organizations)

        role = request.form.get("role", UserRole.ENTERER)
        if not current_user.is_admin and role == UserRole.ADMIN:
            flash("You don't have permission to assign the Administrator role.", "error")
            return render_template("users/edit.html", user=user, roles=available_roles, organizations=organizations)

        user.email = email
        user.first_name = request.form.get("first_name")
        user.last_name = request.form.get("last_name")
        user.role = role
        user.is_active = request.form.get("is_active") == "on"

        org_id = request.form.get("organization_id")
        user.organization_id = int(org_id) if org_id else None

        # Only update password if provided
        new_password = request.form.get("password")
        if new_password:
            user.set_password(new_password)

        db.session.commit()

        flash(f"User {user.full_name} updated successfully", "success")
        return redirect(url_for("users.index"))

    return render_template("users/edit.html", user=user, roles=available_roles, organizations=organizations)


@bp.route("/<int:id>/toggle-active", methods=["POST"])
@login_required
@organizer_required
def toggle_active(id):
    """Toggle user active status."""
    user = db.session.get(User, id)

    if not user:
        flash("User not found", "error")
        return redirect(url_for("users.index"))

    # Organizers cannot toggle admin accounts
    if not current_user.is_admin and user.is_admin:
        flash("You don't have permission to modify an Administrator account.", "error")
        return redirect(url_for("users.index"))

    # Prevent deactivating yourself
    if user.id == current_user.id:
        flash("You cannot deactivate your own account", "error")
        return redirect(url_for("users.index"))

    user.is_active = not user.is_active
    db.session.commit()

    status = "activated" if user.is_active else "deactivated"
    flash(f"User {user.full_name} {status}", "success")
    return redirect(url_for("users.index"))


@bp.route("/<int:id>/resend-invite", methods=["POST"])
@login_required
@organizer_required
def resend_invite(id):
    """Generate a fresh invite link for a user (and send via email if configured)."""
    user = db.session.get(User, id)
    if not user:
        flash("User not found", "error")
        return redirect(url_for("users.index"))

    if not current_user.is_admin and user.is_admin:
        flash("You don't have permission to modify an Administrator account.", "error")
        return redirect(url_for("users.index"))

    smtp_configured = email_service.is_configured()

    s = URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="account-setup")
    token = s.dumps({"id": user.id, "ph": user.password_hash[-8:] if user.password_hash else ""})
    invite_url = url_for("auth.setup_password", token=token, _external=True)

    email_sent = False
    if smtp_configured:
        try:
            email_service.send_invitation_email(user.email, invite_url, current_user.full_name)
            email_sent = True
        except Exception:
            current_app.logger.exception("Failed to send invite email to %s", user.email)

    return render_template(
        "users/invite_link.html",
        user=user,
        invite_url=invite_url,
        email_sent=email_sent,
        smtp_configured=smtp_configured,
    )
