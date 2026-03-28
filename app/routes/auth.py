from urllib.parse import urlparse

from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature

from app import db
from app.models import User, UserLoginEvent
from app.services import email as email_service

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))

    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        user = User.query.filter_by(email=email).first()

        if user and user.check_password(password):
            if not user.is_active:
                flash("Your account has been deactivated. Please contact an administrator.", "error")
                return render_template("auth/login.html")
            login_user(user)
            db.session.add(UserLoginEvent(user_id=user.id, ip_address=request.remote_addr))
            db.session.commit()
            if user.must_change_password:
                return redirect(url_for("auth.change_password"))
            next_page = request.args.get("next", "")
            # Only allow relative redirects to prevent open redirect attacks
            if next_page and urlparse(next_page).netloc:
                next_page = ""
            return redirect(next_page or url_for("main.index"))

        flash("Invalid email or password", "error")

    return render_template("auth/login.html")


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


@bp.route("/register")
def register():
    # Public self-registration is disabled. Accounts are created by administrators.
    flash("Account registration is not open. Contact an administrator to create an account.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if len(new_password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return render_template("auth/change_password.html")

        if new_password != confirm_password:
            flash("Passwords do not match.", "error")
            return render_template("auth/change_password.html")

        current_user.set_password(new_password)
        current_user.must_change_password = False
        db.session.commit()
        flash("Password updated successfully.", "success")
        return redirect(url_for("main.index"))

    return render_template("auth/change_password.html")


@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    from flask import current_app

    smtp_configured = email_service.is_configured()

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        # Always show the same message to prevent email enumeration
        flash(
            "If that email address is registered, you will receive a password reset link shortly.",
            "success",
        )
        user = User.query.filter_by(email=email).first()
        if user and smtp_configured:
            try:
                s = URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="password-reset")
                # Include a hash of the current password so the token is invalidated after use
                token = s.dumps({"id": user.id, "ph": user.password_hash[-8:] if user.password_hash else ""})
                reset_url = url_for("auth.reset_password", token=token, _external=True)
                email_service.send_password_reset_email(user.email, reset_url)
            except Exception:
                current_app.logger.exception("Failed to send password reset email to %s", email)
        return redirect(url_for("auth.forgot_password"))

    return render_template("auth/forgot_password.html", smtp_configured=smtp_configured)


@bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    from flask import current_app

    s = URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="password-reset")
    try:
        payload = s.loads(token, max_age=3600)
    except SignatureExpired:
        flash("This password reset link has expired. Please request a new one.", "error")
        return redirect(url_for("auth.forgot_password"))
    except BadSignature:
        flash("Invalid password reset link.", "error")
        return redirect(url_for("auth.forgot_password"))

    user_id = payload.get("id") if isinstance(payload, dict) else payload
    user = db.session.get(User, user_id)
    if user is None:
        flash("Invalid password reset link.", "error")
        return redirect(url_for("auth.forgot_password"))

    # Verify the password hasn't changed since the token was issued (prevents replay)
    expected_ph = payload.get("ph", "") if isinstance(payload, dict) else ""
    current_ph = user.password_hash[-8:] if user.password_hash else ""
    if expected_ph and expected_ph != current_ph:
        flash("This password reset link has already been used. Please request a new one.", "error")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "POST":
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if len(new_password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return render_template("auth/reset_password.html", token=token)

        if new_password != confirm_password:
            flash("Passwords do not match.", "error")
            return render_template("auth/reset_password.html", token=token)

        user.set_password(new_password)
        user.must_change_password = False
        db.session.commit()
        flash("Password reset successfully. Please log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html", token=token)


@bp.route("/setup-password/<token>", methods=["GET", "POST"])
def setup_password(token):
    """Account setup for newly created users (invite link flow)."""
    from flask import current_app

    if current_user.is_authenticated:
        return redirect(url_for("main.index"))

    s = URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="account-setup")
    try:
        payload = s.loads(token, max_age=259200)  # 72 hours
    except SignatureExpired:
        flash("This invitation link has expired. Please contact an administrator to resend it.", "error")
        return redirect(url_for("auth.login"))
    except BadSignature:
        flash("Invalid invitation link.", "error")
        return redirect(url_for("auth.login"))

    user_id = payload.get("id") if isinstance(payload, dict) else payload
    user = db.session.get(User, user_id)
    if user is None:
        flash("Invalid invitation link.", "error")
        return redirect(url_for("auth.login"))

    # Token is invalidated once the user sets their password (ph fingerprint changes)
    expected_ph = payload.get("ph", "") if isinstance(payload, dict) else ""
    current_ph = user.password_hash[-8:] if user.password_hash else ""
    if expected_ph and expected_ph != current_ph:
        flash("This invitation link has already been used. Please log in or use the forgot-password link.", "error")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if len(new_password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return render_template("auth/setup_password.html", token=token, user=user)

        if new_password != confirm_password:
            flash("Passwords do not match.", "error")
            return render_template("auth/setup_password.html", token=token, user=user)

        user.set_password(new_password)
        user.must_change_password = False
        db.session.add(UserLoginEvent(user_id=user.id, ip_address=request.remote_addr))
        db.session.commit()
        login_user(user)
        flash("Welcome! Your account is ready.", "success")
        return redirect(url_for("main.index"))

    return render_template("auth/setup_password.html", token=token, user=user)
