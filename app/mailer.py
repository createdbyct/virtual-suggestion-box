"""
Email delivery via stdlib smtplib — no extra dependency to install.
Used for password-reset links and per-form submission notification
emails. The 24hr submission-edit link stays on-screen by design.

SMTP settings can be configured two ways, checked in this order:
1. Superadmin Site Settings panel (stored in SiteSettings, in the DB)
2. Environment variables (SMTP_HOST, SMTP_PORT, SMTP_USERNAME,
   SMTP_PASSWORD, SMTP_FROM_EMAIL, SMTP_FROM_NAME) — the original
   systemd-service approach, kept as a fallback so existing deployments
   using env vars keep working without any change.

get_smtp_config() resolves the two into one plain dict and must be
called with a live db session — do this BEFORE scheduling a background
task, since a background task's db session may already be closed by
the time it actually runs. send_email() itself takes the resolved dict,
not a db session, so it's always safe to pass into a background task.
"""
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from sqlalchemy.orm import Session

# Used to build the actual link in an email. Request.base_url isn't
# reliable behind a reverse proxy/tunnel unless proxy headers are
# explicitly trusted, so this is set explicitly instead — e.g.
# https://suggestions.yourdomain.com — with no trailing slash. Kept as
# an env var (not DB-configurable) since it's a deployment/networking
# detail, not a mail-account credential.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")


def get_smtp_config(db: Session) -> dict:
    """DB-stored values win if set; otherwise falls back to environment
    variables. Returns plain data — safe to pass into a background task,
    unlike a db session which may not still be valid by then."""
    from .models import SiteSettings  # local import avoids a circular import at module load

    settings = db.query(SiteSettings).filter(SiteSettings.id == 1).first()

    host = (settings.smtp_host if settings else None) or os.environ.get("SMTP_HOST")
    port_raw = (settings.smtp_port if settings else None) or os.environ.get("SMTP_PORT")
    port = int(port_raw) if port_raw else 587
    username = (settings.smtp_username if settings else None) or os.environ.get("SMTP_USERNAME")
    password = (settings.smtp_password if settings else None) or os.environ.get("SMTP_PASSWORD")
    from_email = (settings.smtp_from_email if settings else None) or os.environ.get("SMTP_FROM_EMAIL") or username
    from_name = (settings.smtp_from_name if settings else None) or os.environ.get("SMTP_FROM_NAME") or "Virtual Suggestion Box"

    return {
        "host": host, "port": port, "username": username,
        "password": password, "from_email": from_email, "from_name": from_name,
    }


def is_smtp_configured(config: dict) -> bool:
    return bool(config.get("host") and config.get("username") and config.get("password"))


def send_email(config: dict, to_email: str, subject: str, body_text: str) -> bool:
    """Returns True if the send succeeded. Never raises — logs and
    returns False on failure so the caller can fall back gracefully
    (e.g. to showing the link on-screen) instead of erroring out."""
    if not is_smtp_configured(config):
        return False

    msg = MIMEMultipart()
    msg["From"] = f"{config['from_name']} <{config['from_email']}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body_text, "plain"))

    try:
        with smtplib.SMTP(config["host"], config["port"], timeout=10) as server:
            server.starttls()
            server.login(config["username"], config["password"])
            server.sendmail(config["from_email"], [to_email], msg.as_string())
        return True
    except Exception as exc:  # noqa: BLE001 — any SMTP failure just degrades gracefully
        print(f"[email] Failed to send to {to_email}: {exc}")
        return False


def build_public_url(path: str, request) -> str:
    """PUBLIC_BASE_URL wins if set (correct behind Cloudflare Tunnel etc);
    otherwise falls back to the request's own host (fine for direct/local
    access, not reliable behind an unconfigured proxy)."""
    base = PUBLIC_BASE_URL or str(request.base_url).rstrip("/")
    return f"{base}{path}"
