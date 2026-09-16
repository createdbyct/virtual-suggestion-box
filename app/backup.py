"""
Builds the full-database backup dict — shared by the superadmin export
API endpoint (app/routers/admin.py) and the standalone cron script
(backup_db.py) so the two never drift out of sync with each other.

See the export/import section of app/routers/admin.py for the full
rationale on why this is structured JSON rather than a raw .db copy.
"""
import datetime

from sqlalchemy.orm import Session

from .models import Project, ProjectShare, Submission, Nominee, RecoveryCode, SiteSettings, User

EXPORT_VERSION = 1


def _iso(dt):
    return dt.isoformat() if dt else None


def build_backup_dict(db: Session) -> dict:
    users = db.query(User).all()
    projects = db.query(Project).all()
    shares = db.query(ProjectShare).all()
    submissions = db.query(Submission).all()
    nominees = db.query(Nominee).all()
    recovery_codes = db.query(RecoveryCode).all()
    settings = db.query(SiteSettings).filter(SiteSettings.id == 1).first()

    return {
        "export_version": EXPORT_VERSION,
        "exported_at": _iso(datetime.datetime.utcnow()),
        "users": [
            {
                "id": u.id, "name": u.name, "username": u.username, "email": u.email,
                "password_hash": u.password_hash, "role": u.role, "is_active": u.is_active,
                "totp_secret": u.totp_secret, "totp_enabled": u.totp_enabled,
                "created_at": _iso(u.created_at),
            } for u in users
        ],
        "projects": [
            {
                "id": p.id, "owner_id": p.owner_id, "title": p.title, "type": p.type,
                "slug": p.slug, "webhook_url": getattr(p, "webhook_url", None),
                "notify_email": getattr(p, "notify_email", None),
                "created_at": _iso(p.created_at),
            } for p in projects
        ],
        "project_shares": [
            {"id": s.id, "project_id": s.project_id, "user_id": s.user_id, "created_at": _iso(s.created_at)}
            for s in shares
        ],
        "submissions": [
            {
                "id": s.id, "project_id": s.project_id, "suggestion_text": s.suggestion_text,
                "nomination_reason": s.nomination_reason, "submitter_name": s.submitter_name,
                "submitter_email": s.submitter_email, "is_anonymous": s.is_anonymous,
                "edit_token": s.edit_token, "edit_expires_at": _iso(s.edit_expires_at),
                "status": s.status, "created_at": _iso(s.created_at),
            } for s in submissions
        ],
        "nominees": [
            {"id": n.id, "submission_id": n.submission_id, "name": n.name, "role": n.role}
            for n in nominees
        ],
        "recovery_codes": [
            {
                "id": r.id, "user_id": r.user_id, "code_hash": r.code_hash,
                "used": r.used, "created_at": _iso(r.created_at),
            } for r in recovery_codes
        ],
        "site_settings": {
            "footer_text": settings.footer_text,
            "footer_link_url": settings.footer_link_url,
            "dark_mode_enabled": settings.dark_mode_enabled,
        } if settings else None,
    }
