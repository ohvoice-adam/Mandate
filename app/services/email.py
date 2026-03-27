"""SMTP email service."""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    """Return True if all required SMTP settings are present."""
    from app.models import Settings

    return all(
        Settings.get(k)
        for k in ("smtp_host", "smtp_user", "smtp_from_email")
    )


def send_email(to: str, subject: str, body_html: str, body_text: str) -> None:
    """Send an email via SMTP. Raises on failure."""
    from app.models import Settings

    host = Settings.get("smtp_host", "")
    port = int(Settings.get("smtp_port", "587") or "587")
    user = Settings.get("smtp_user", "")
    password = Settings.get("smtp_password", "")
    from_email = Settings.get("smtp_from_email", "")
    use_tls = Settings.get("smtp_use_tls", "true").lower() != "false"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to
    msg.attach(MIMEText(body_text, "plain"))
    msg.attach(MIMEText(body_html, "html"))

    with smtplib.SMTP(host, port, timeout=15) as smtp:
        if use_tls:
            smtp.starttls()
        if user and password:
            smtp.login(user, password)
        smtp.sendmail(from_email, [to], msg.as_string())

    logger.info("Email sent to %s: %s", to, subject)


def send_backup_success_email(to: str, backup_time_iso: str) -> None:
    """Send a single-backup success notification."""
    subject = "Backup Succeeded"
    backup_time = backup_time_iso[:19].replace("T", " ") if backup_time_iso else "unknown"
    body_text = f"A database backup completed successfully at {backup_time} UTC."
    body_html = f"<p>A database backup completed successfully at <strong>{backup_time} UTC</strong>.</p>"
    send_email(to, subject, body_html, body_text)


def send_backup_failure_email(to: str, error_msg: str, backup_time_iso: str) -> None:
    """Send an immediate failure alert with error detail."""
    subject = "Backup Failed"
    backup_time = backup_time_iso[:19].replace("T", " ") if backup_time_iso else "unknown"
    body_text = (
        f"A database backup failed at {backup_time} UTC.\n\n"
        f"Error: {error_msg}"
    )
    body_html = (
        f"<p>A database backup failed at <strong>{backup_time} UTC</strong>.</p>"
        f"<p><strong>Error:</strong></p>"
        f"<pre style='background:#f5f5f5;padding:8px;border-radius:4px'>{error_msg}</pre>"
    )
    send_email(to, subject, body_html, body_text)


def send_backup_digest_email(to: str, entries: list) -> None:
    """Send a digest listing all successful backup timestamps."""
    subject = f"Backup Digest — {len(entries)} backup{'s' if len(entries) != 1 else ''}"
    formatted = "\n".join(
        f"  • {ts[:19].replace('T', ' ')} UTC" for ts in entries
    )
    body_text = (
        f"{len(entries)} backup{'s' if len(entries) != 1 else ''} completed successfully:\n\n"
        f"{formatted}"
    )
    items_html = "".join(
        f"<li>{ts[:19].replace('T', ' ')} UTC</li>" for ts in entries
    )
    body_html = (
        f"<p>{len(entries)} backup{'s' if len(entries) != 1 else ''} completed successfully:</p>"
        f"<ul>{items_html}</ul>"
    )
    send_email(to, subject, body_html, body_text)


def send_invitation_email(to: str, invite_url: str, inviter_name: str) -> None:
    """Send an account setup invitation email."""
    from app.models import Settings
    org_name = Settings.get("branding_org_name", "").strip()
    app_name = org_name or "Mandate"
    platform_desc = f"Mandate, {org_name}'s petition signature" if org_name else "Mandate, the petition signature"

    subject = f"You're invited to {app_name}"

    body_text = (
        f"Hi,\n\n"
        f"{inviter_name} has set up an account for you on {platform_desc}\n"
        f"management system.\n\n"
        f"To get started, click the link below to choose your password and sign in.\n"
        f"This link is valid for 72 hours.\n\n"
        f"  {invite_url}\n\n"
        f"If the link has expired, you can request a new one from the login page\n"
        f"using the \"Forgot your password?\" link.\n\n"
        f"If you weren't expecting this invitation, you can safely ignore this email.\n\n"
        f"— The {app_name} team"
    )

    body_html = f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#f4f4f5;font-family:system-ui,-apple-system,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f5;padding:40px 0;">
    <tr><td align="center">
      <table width="560" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,0.08);">

        <!-- Header -->
        <tr>
          <td style="background:#0c3e6b;padding:28px 40px;">
            <p style="margin:0;font-size:20px;font-weight:700;color:#ffffff;letter-spacing:-0.3px;">{app_name}</p>
          </td>
        </tr>

        <!-- Body -->
        <tr>
          <td style="padding:36px 40px 28px;">
            <h1 style="margin:0 0 16px;font-size:22px;font-weight:700;color:#111827;">You're invited!</h1>
            <p style="margin:0 0 12px;font-size:15px;color:#374151;line-height:1.6;">
              <strong>{inviter_name}</strong> has set up an account for you on
              <strong>{platform_desc}</strong> management system.
            </p>
            <p style="margin:0 0 28px;font-size:15px;color:#374151;line-height:1.6;">
              Click the button below to choose your password and sign in for the first time.
              This invitation link is valid for <strong>72 hours</strong>.
            </p>

            <!-- CTA button -->
            <table cellpadding="0" cellspacing="0" style="margin-bottom:28px;">
              <tr>
                <td style="background:#f56708;border-radius:6px;">
                  <a href="{invite_url}"
                     style="display:inline-block;padding:13px 28px;font-size:15px;font-weight:600;
                            color:#ffffff;text-decoration:none;letter-spacing:0.1px;">
                    Activate My Account →
                  </a>
                </td>
              </tr>
            </table>

            <p style="margin:0 0 8px;font-size:13px;color:#6b7280;line-height:1.5;">
              If the button doesn't work, paste this link into your browser:
            </p>
            <p style="margin:0 0 28px;font-size:12px;color:#6b7280;word-break:break-all;">
              <a href="{invite_url}" style="color:#0c3e6b;">{invite_url}</a>
            </p>

            <hr style="border:none;border-top:1px solid #e5e7eb;margin:0 0 20px;">

            <p style="margin:0;font-size:13px;color:#9ca3af;line-height:1.5;">
              If your link expires, use the
              <a href="#" style="color:#0c3e6b;">Forgot your password?</a>
              link on the login page to request a new one.<br>
              If you weren't expecting this invitation, you can safely ignore this email.
            </p>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""

    send_email(to, subject, body_html, body_text)


def send_password_reset_email(to: str, reset_url: str) -> None:
    """Send a password reset email with the given reset URL."""
    subject = "Password Reset Request"
    body_text = (
        f"You requested a password reset.\n\n"
        f"Click the link below to set a new password (expires in 1 hour):\n\n"
        f"{reset_url}\n\n"
        f"If you did not request this, you can safely ignore this email."
    )
    body_html = f"""
<p>You requested a password reset.</p>
<p>Click the link below to set a new password (expires in 1 hour):</p>
<p><a href="{reset_url}">{reset_url}</a></p>
<p>If you did not request this, you can safely ignore this email.</p>
"""
    send_email(to, subject, body_html, body_text)
