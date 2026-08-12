"""
Email delivery via stdlib smtplib — no extra dependency to install.
Used only for password-reset links. Submission notifications and the
24hr edit link stay on-screen by design (see README).

Configured entirely through environment variables so it works with Gmail,
a cPanel/Namecheap mailbox, or any other SMTP provider without code
changes. If unset, forgot-password gracefully falls back to showing the
reset link on-screen instead of emailing it — keeps local dev usable
without needing real SMTP credentials every time.
"""
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")
SMTP_FROM_EMAIL = os.environ.get("SMTP_FROM_EMAIL") or SMTP_USERNAME
SMTP_FROM_NAME = os.environ.get("SMTP_FROM_NAME", "Virtual Suggestion Box")

# Used to build the actual link in the email. Request.base_url isn't
# reliable behind a reverse proxy/tunnel unless proxy headers are
# explicitly trusted, so this is set explicitly instead — e.g.
# https://suggestions.yourdomain.com — with no trailing slash.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")


def is_email_configured() -> bool:
    return bool(SMTP_HOST and SMTP_USERNAME and SMTP_PASSWORD)


def send_email(to_email: str, subject: str, body_text: str) -> bool:
    """Returns True if the send succeeded. Never raises — logs and
    returns False on failure so the caller can fall back gracefully
    (e.g. to showing the link on-screen) instead of erroring out."""
    if not is_email_configured():
        return False

    msg = MIMEMultipart()
    msg["From"] = f"{SMTP_FROM_NAME} <{SMTP_FROM_EMAIL}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body_text, "plain"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM_EMAIL, [to_email], msg.as_string())
        return True
    except Exception as exc:  # noqa: BLE001 — any SMTP failure just degrades to on-screen
        print(f"[email] Failed to send to {to_email}: {exc}")
        return False


def build_public_url(path: str, request) -> str:
    """PUBLIC_BASE_URL wins if set (correct behind Cloudflare Tunnel etc);
    otherwise falls back to the request's own host (fine for direct/local
    access, not reliable behind an unconfigured proxy)."""
    base = PUBLIC_BASE_URL or str(request.base_url).rstrip("/")
    return f"{base}{path}"
