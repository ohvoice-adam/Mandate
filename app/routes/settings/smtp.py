"""Outgoing email (SMTP) configuration routes."""

from flask import current_app, flash, jsonify, redirect, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import current_user, login_required

from app.models import Settings, admin_required
from app.routes.settings import bp
from app.services import email as email_service
from app.utils import is_valid_email


@bp.route("/save-smtp-config", methods=["POST"])
@login_required
@admin_required
def save_smtp_config() -> ResponseReturnValue:
    """Save SMTP email configuration."""
    from_email = request.form.get("smtp_from_email", "").strip()
    if from_email and not is_valid_email(from_email):
        flash("Invalid From Address email.", "error")
        return redirect(url_for("settings.index"))

    Settings.save_smtp_config(
        host=request.form.get("smtp_host", ""),
        port=request.form.get("smtp_port", "587"),
        user=request.form.get("smtp_user", ""),
        from_email=from_email,
        use_tls=bool(request.form.get("smtp_use_tls")),
        password=request.form.get("smtp_password") or None,
    )
    flash("Email configuration saved", "success")
    return redirect(url_for("settings.index"))


@bp.route("/test-smtp", methods=["POST"])
@login_required
@admin_required
def test_smtp() -> ResponseReturnValue:
    """Send a test email and return JSON {ok, message}."""
    try:
        if not email_service.is_configured():
            return jsonify(ok=False, message="Email is not fully configured.")
        email_service.send_email(
            to=current_user.email,
            subject="Mandate — SMTP Test",
            body_html="<p>SMTP is configured correctly.</p>",
            body_text="SMTP is configured correctly.",
        )
        return jsonify(ok=True, message=f"Test email sent to {current_user.email}.")
    except Exception as exc:
        current_app.logger.exception("SMTP test failed")
        return jsonify(ok=False, message=f"Error: {exc}"), 500
