"""Branding routes: logo upload/serve/clear, color extraction, fonts."""

import base64
import io

from flask import Response, abort, flash, redirect, request, url_for
from flask.typing import ResponseReturnValue
from flask_login import login_required

from app.models import Settings, admin_required
from app.routes.settings import bp

_ALLOWED_LOGO_FORMATS = {"png", "jpeg", "gif", "webp"}


@bp.route("/branding-logo")
@login_required
def branding_logo() -> ResponseReturnValue:
    """Serve the stored org logo as a raw image response."""
    data = Settings.get_logo_bytes()
    if not data:
        # abort(404) raises an HTTP 404 immediately — nothing after it runs.
        abort(404)
    mime = Settings.get("branding_logo_mime", "image/png")
    # Response() builds a custom HTTP response; mimetype sets Content-Type
    # so the browser knows to render this as an image.
    return Response(data, mimetype=mime)


@bp.route("/clear-logo", methods=["POST"])
@login_required
@admin_required
def clear_logo() -> ResponseReturnValue:
    """Remove the stored org logo."""
    Settings.set("branding_logo_content", "")
    Settings.set("branding_logo_mime", "")
    flash("Logo removed", "success")
    return redirect(url_for("settings.index"))


@bp.route("/generate-logo-colors", methods=["POST"])
@login_required
@admin_required
def generate_logo_colors() -> ResponseReturnValue:
    """Extract and save brand colors from the stored logo."""
    logo_bytes = Settings.get_logo_bytes()
    if not logo_bytes:
        flash("No logo uploaded. Upload a logo first.", "error")
        return redirect(url_for("settings.index"))
    from app.services.branding import extract_colors_from_image
    primary, accent = extract_colors_from_image(logo_bytes)
    branding_config = Settings.get_branding_config()
    Settings.save_branding_config(branding_config["mode"], branding_config["org_name"], primary, accent)
    flash("Colors generated from logo.", "success")
    return redirect(url_for("settings.index"))


def _store_uploaded_logo() -> str | None:
    """Validate and store an uploaded logo, if any; return an error or None."""
    logo_file = request.files.get("branding_logo")
    if not logo_file or not logo_file.filename:
        return None
    logo_bytes = logo_file.read()
    # Validate actual image content with Pillow — never trust the client MIME type
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(logo_bytes))
        img.verify()
        fmt = (img.format or "").lower()
    except Exception:
        return "Uploaded logo is not a valid image file."
    if fmt not in _ALLOWED_LOGO_FORMATS:
        return f"Logo must be a PNG, JPEG, GIF, or WebP image (got {fmt or 'unknown'})."
    Settings.set("branding_logo_content", base64.b64encode(logo_bytes).decode())
    Settings.set("branding_logo_mime", f"image/{fmt}")
    return None


def _save_font_choices() -> None:
    """Persist headline/body fonts, falling back to defaults for unknown names."""
    from app.services.fonts import (
        BODY_FONTS,
        DEFAULT_BODY_FONT,
        DEFAULT_HEADLINE_FONT,
        HEADLINE_FONTS,
    )
    headline_names = {name for name, _, _ in HEADLINE_FONTS}
    body_names = {name for name, _, _ in BODY_FONTS}
    headline_font = request.form.get("branding_headline_font", "").strip()
    body_font = request.form.get("branding_body_font", "").strip()
    if headline_font not in headline_names:
        headline_font = DEFAULT_HEADLINE_FONT
    if body_font not in body_names:
        body_font = DEFAULT_BODY_FONT
    Settings.save_branding_fonts(headline_font, body_font)


@bp.route("/save-branding-config", methods=["POST"])
@login_required
@admin_required
def save_branding_config() -> ResponseReturnValue:
    """Save branding configuration."""
    mode = request.form.get("branding_mode", "")
    if mode not in ("", "dual", "white-label"):
        mode = ""
    org_name = request.form.get("branding_org_name", "").strip()

    logo_error = _store_uploaded_logo()
    if logo_error:
        flash(logo_error, "error")
        return redirect(url_for("settings.index"))

    # Honor whatever the color pickers submitted.
    final_primary = request.form.get("branding_primary_color", "").strip()
    final_accent = request.form.get("branding_accent_color", "").strip()

    # Switching back to default Mandate branding clears stored colors so the
    # context processor falls back to the hardcoded navy/orange defaults.
    if mode == "":
        final_primary = ""
        final_accent = ""

    Settings.save_branding_config(mode, org_name, final_primary, final_accent)
    _save_font_choices()

    flash("Branding configuration saved", "success")
    return redirect(url_for("settings.index"))
