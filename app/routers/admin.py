"""
Admin-only endpoints — see every owner's forms and submissions, and
manage user accounts (promote/demote, disable/enable, delete, reset
password). Superadmin-only: full data export/import (see bottom of file).
"""
import datetime
import json
import secrets

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import destroy_all_sessions_for_user, hash_password, require_admin, require_superadmin
from ..backup import build_backup_dict
from ..database import get_db
from ..models import (
    Project, ProjectShare, Submission, Nominee, RecoveryCode,
    SiteSettings, WatchedForm, AuthSession, PasswordResetToken, User,
)
from .projects import build_submissions_csv
from ..schemas import (
    ProjectAdminOut, SubmissionOut, UserAdminOut, UserUpdateIn,
    AdminResetPasswordOut, SiteSettingsAdminOut,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _get_or_create_settings(db: Session) -> SiteSettings:
    settings = db.query(SiteSettings).filter(SiteSettings.id == 1).first()
    if not settings:
        settings = SiteSettings(id=1)
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


@router.get("/projects", response_model=list[ProjectAdminOut])
def list_all_projects(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = (
        db.query(Project, User.email, func.count(Submission.id).label("submission_count"))
        .join(User, Project.owner_id == User.id)
        .outerjoin(Submission, Submission.project_id == Project.id)
        .group_by(Project.id)
        .order_by(Project.created_at.desc())
        .all()
    )
    watched_ids = {w.project_id for w in db.query(WatchedForm).all()}
    result = []
    for project, owner_email, count in rows:
        project.submission_count = count
        project.owner_email = owner_email
        project.is_watched = project.id in watched_ids
        result.append(project)
    return result


@router.post("/projects/{project_id}/watch")
def toggle_watch_form(project_id: int, watched: bool = Body(embed=True), superadmin: User = Depends(require_superadmin), db: Session = Depends(get_db)):
    """Superadmin-only — adds/removes a form from the global notification
    watch list, independent of who owns the form."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Form not found")

    existing = db.query(WatchedForm).filter(WatchedForm.project_id == project_id).first()
    if watched and not existing:
        db.add(WatchedForm(project_id=project_id))
    elif not watched and existing:
        db.delete(existing)
    db.commit()
    return {"status": "ok", "watched": watched}


@router.get("/settings", response_model=SiteSettingsAdminOut)
def get_admin_settings(superadmin: User = Depends(require_superadmin), db: Session = Depends(get_db)):
    """Superadmin's own view of settings — includes last_backup_at, the
    notification_webhook_url, and SMTP config (never the raw password),
    unlike the public GET /api/settings."""
    settings = _get_or_create_settings(db)
    return SiteSettingsAdminOut(
        footer_text=settings.footer_text,
        footer_link_url=settings.footer_link_url,
        dark_mode_enabled=settings.dark_mode_enabled,
        notification_webhook_url=settings.notification_webhook_url,
        last_backup_at=settings.last_backup_at,
        smtp_host=settings.smtp_host,
        smtp_port=settings.smtp_port,
        smtp_username=settings.smtp_username,
        smtp_from_email=settings.smtp_from_email,
        smtp_from_name=settings.smtp_from_name,
        smtp_password_set=bool(settings.smtp_password),
    )


@router.get("/projects/{project_id}/submissions", response_model=list[SubmissionOut])
def list_project_submissions(project_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Form not found")
    return (
        db.query(Submission)
        .filter(Submission.project_id == project_id)
        .order_by(Submission.created_at.desc())
        .all()
    )


@router.get("/projects/{project_id}/export.csv")
def export_project_csv(project_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Form not found")
    submissions = (
        db.query(Submission)
        .filter(Submission.project_id == project_id)
        .order_by(Submission.created_at.desc())
        .all()
    )
    return build_submissions_csv(project, submissions)


@router.get("/users", response_model=list[UserAdminOut])
def list_users(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = (
        db.query(User, func.count(Project.id).label("project_count"))
        .outerjoin(Project, Project.owner_id == User.id)
        .group_by(User.id)
        .order_by(User.created_at.desc())
        .all()
    )
    result = []
    for user, count in rows:
        user.project_count = count
        result.append(user)
    return result


@router.patch("/users/{user_id}", response_model=UserAdminOut)
def update_user(user_id: int, payload: UserUpdateIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    # Superadmin accounts are off-limits to regular admins entirely —
    # only another superadmin can touch one (and even then, self-protection
    # below still applies).
    if target.role == "superadmin" and admin.role != "superadmin":
        raise HTTPException(status_code=403, detail="Only a super admin can modify a super admin account")

    # Prevent an admin from locking themselves out — no self-demotion,
    # no self-deactivation. They can still be changed by a *different* admin.
    if target.id == admin.id:
        if payload.role is not None and payload.role != "admin":
            raise HTTPException(status_code=400, detail="You can't remove your own admin access")
        if payload.is_active is False:
            raise HTTPException(status_code=400, detail="You can't disable your own account")

    if payload.role is not None:
        target.role = payload.role
    if payload.is_active is not None:
        target.is_active = payload.is_active

    db.commit()
    db.refresh(target)
    target.project_count = db.query(func.count(Project.id)).filter(Project.owner_id == target.id).scalar()
    return target


@router.delete("/users/{user_id}")
def delete_user(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="You can't delete your own account")

    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    if target.role == "superadmin" and admin.role != "superadmin":
        raise HTTPException(status_code=403, detail="Only a super admin can delete a super admin account")

    db.delete(target)  # cascades to their projects, submissions, and nominees
    db.commit()
    return {"status": "deleted"}


@router.post("/users/{user_id}/reset-password", response_model=AdminResetPasswordOut)
def admin_reset_password(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Backup path for when there's no self-service email flow available —
    generates a temporary password and hands it to the admin once, to pass
    along out of band. Forces the account to log in fresh everywhere."""
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    if target.role == "superadmin" and admin.role != "superadmin":
        raise HTTPException(status_code=403, detail="Only a super admin can reset a super admin's password")

    temp_password = secrets.token_urlsafe(9)  # ~12 readable chars
    target.password_hash = hash_password(temp_password)
    destroy_all_sessions_for_user(db, target.id)
    db.commit()
    return AdminResetPasswordOut(temporary_password=temp_password)


# ---- Full data export/import (superadmin only) ----
#
# This exists specifically because of schema changes: every time a new
# column or table shows up in models.py, the SQLite file has to be wiped
# and recreated (SQLAlchemy's create_all() only creates missing tables —
# it never alters existing ones). A raw copy of the .db file wouldn't
# survive that; it'd just be missing whatever the new schema expects.
#
# So instead: export walks the *current* ORM models and writes structured
# JSON, and import reads that JSON back through the *current* models too.
# As long as you export before wiping and import after the fresh schema is
# up, this works across schema changes — new nullable columns just fall
# back to their model defaults on old backups, since every field read
# below uses .get() rather than assuming it exists.
#
# Sessions and password-reset tokens are intentionally NOT included —
# they're short-lived by design, and forcing a fresh login after a
# restore is expected, not a bug.

def _iso(dt):
    return dt.isoformat() if dt else None


def _parse_dt(s):
    return datetime.datetime.fromisoformat(s) if s else None


@router.get("/export")
def export_data(superadmin: User = Depends(require_superadmin), db: Session = Depends(get_db)):
    data = build_backup_dict(db)

    settings = _get_or_create_settings(db)
    settings.last_backup_at = datetime.datetime.utcnow()
    db.commit()

    filename = f"virtual-suggestion-box-backup-{datetime.datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.json"
    return Response(
        content=json.dumps(data, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/import")
def import_data(payload: dict = Body(...), superadmin: User = Depends(require_superadmin), db: Session = Depends(get_db)):
    if "users" not in payload:
        raise HTTPException(status_code=400, detail="This doesn't look like a valid backup file")

    # Wipe everything first — children before parents, to respect FK
    # constraints. This also clears every session (including the one doing
    # the import), since post-import the current session's user_id may no
    # longer correspond to the same account.
    db.query(Nominee).delete()
    db.query(RecoveryCode).delete()
    db.query(Submission).delete()
    db.query(ProjectShare).delete()
    db.query(Project).delete()
    db.query(AuthSession).delete()
    db.query(PasswordResetToken).delete()
    db.query(User).delete()
    db.query(SiteSettings).delete()
    db.commit()

    for u in payload.get("users", []):
        db.add(User(
            id=u["id"], name=u["name"], username=u["username"], email=u["email"],
            password_hash=u["password_hash"], role=u.get("role", "owner"),
            is_active=u.get("is_active", True), totp_secret=u.get("totp_secret"),
            totp_enabled=u.get("totp_enabled", False), created_at=_parse_dt(u.get("created_at")),
        ))
    db.commit()

    for p in payload.get("projects", []):
        db.add(Project(
            id=p["id"], owner_id=p["owner_id"], title=p["title"], type=p["type"],
            slug=p["slug"], webhook_url=p.get("webhook_url"), notify_email=p.get("notify_email"),
            created_at=_parse_dt(p.get("created_at")),
        ))
    db.commit()

    for s in payload.get("project_shares", []):
        db.add(ProjectShare(
            id=s["id"], project_id=s["project_id"], user_id=s["user_id"],
            created_at=_parse_dt(s.get("created_at")),
        ))
    db.commit()

    for s in payload.get("submissions", []):
        db.add(Submission(
            id=s["id"], project_id=s["project_id"], suggestion_text=s.get("suggestion_text"),
            nomination_reason=s.get("nomination_reason"), submitter_name=s.get("submitter_name"),
            submitter_email=s.get("submitter_email"), is_anonymous=s.get("is_anonymous", False),
            edit_token=s.get("edit_token"), edit_expires_at=_parse_dt(s.get("edit_expires_at")),
            status=s.get("status", "new"), created_at=_parse_dt(s.get("created_at")),
        ))
    db.commit()

    for n in payload.get("nominees", []):
        db.add(Nominee(id=n["id"], submission_id=n["submission_id"], name=n["name"], role=n.get("role")))
    db.commit()

    for r in payload.get("recovery_codes", []):
        db.add(RecoveryCode(
            id=r["id"], user_id=r["user_id"], code_hash=r["code_hash"],
            used=r.get("used", False), created_at=_parse_dt(r.get("created_at")),
        ))
    db.commit()

    settings_data = payload.get("site_settings")
    if settings_data:
        db.add(SiteSettings(
            id=1, footer_text=settings_data.get("footer_text"),
            footer_link_url=settings_data.get("footer_link_url"),
            dark_mode_enabled=settings_data.get("dark_mode_enabled", True),
        ))
        db.commit()

    return {
        "status": "imported",
        "users": len(payload.get("users", [])),
        "projects": len(payload.get("projects", [])),
        "submissions": len(payload.get("submissions", [])),
    }
