"""
Admin-only endpoints — see every owner's forms and submissions, and
manage user accounts (promote/demote, disable/enable, delete, reset
password). Superadmin-only: full data export/import (see bottom of file).
"""
import datetime
import json
import secrets
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from ..audit import record
from ..auth import destroy_all_sessions_for_user, hash_password, require_admin, require_superadmin
from ..backup import build_backup_dict, restore_backup_dict
from ..backup_job import DEFAULT_DIR, check_destination, run_and_record
from ..database import get_db
from ..models import (
    AuditLog, Project, ProjectShare, Submission, Nominee, RecoveryCode,
    SiteSettings, WatchedForm, AuthSession, PasswordResetToken, User,
)
from .projects import build_submissions_csv
from ..analytics import build_survey_analytics
from ..schemas import (
    ProjectAdminOut, SubmissionOut, UserAdminOut, UserUpdateIn,
    AdminResetPasswordOut, AuditLogOut, BackupScheduleIn, BackupScheduleOut, SiteSettingsAdminOut, SurveyAnalyticsOut,
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
    new_count_expr = func.sum(case((Submission.status == "new", 1), else_=0))
    rows = (
        db.query(Project, User.email, User.name, func.count(Submission.id).label("submission_count"), new_count_expr.label("new_count"))
        .join(User, Project.owner_id == User.id)
        .outerjoin(Submission, Submission.project_id == Project.id)
        .group_by(Project.id)
        .order_by(Project.created_at.desc())
        .all()
    )
    watched_by_id = {w.project_id: w for w in db.query(WatchedForm).all()}
    result = []
    for project, owner_email, owner_name, count, new_count in rows:
        project.submission_count = count
        project.new_submission_count = new_count or 0
        project.owner_email = owner_email
        project.owner_name = owner_name
        watched = watched_by_id.get(project.id)
        project.notify_email = watched.notify_email if watched else False
        project.notify_webhook = watched.notify_webhook if watched else False
        result.append(project)
    return result


@router.post("/projects/{project_id}/watch")
def toggle_watch_form(
    project_id: int,
    notify_email: bool = Body(embed=True),
    notify_webhook: bool = Body(embed=True),
    superadmin: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    """Superadmin-only, per-channel — a form can notify via email only,
    webhook only, both, or neither. If both end up off, the row is
    removed entirely rather than kept as a no-op."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Form not found")

    existing = db.query(WatchedForm).filter(WatchedForm.project_id == project_id).first()

    if not notify_email and not notify_webhook:
        if existing:
            db.delete(existing)
            db.commit()
        return {"status": "ok", "notify_email": False, "notify_webhook": False}

    if existing:
        existing.notify_email = notify_email
        existing.notify_webhook = notify_webhook
    else:
        db.add(WatchedForm(project_id=project_id, notify_email=notify_email, notify_webhook=notify_webhook))
    db.commit()
    return {"status": "ok", "notify_email": notify_email, "notify_webhook": notify_webhook}


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
        notification_email=settings.notification_email,
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


@router.get("/projects/{project_id}/analytics", response_model=SurveyAnalyticsOut)
def get_project_analytics_admin(project_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Admin/superadmin equivalent of the owner's own analytics endpoint —
    same shared builder, so the numbers can never drift between views."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Form not found")
    return build_survey_analytics(project, db, detailed=True)


@router.get("/audit-log", response_model=list[AuditLogOut])
def get_audit_log(
    limit: int = 100, offset: int = 0, action: Optional[str] = None,
    superadmin: User = Depends(require_superadmin), db: Session = Depends(get_db),
):
    """Newest first. Superadmin only — it names who changed whose access."""
    q = db.query(AuditLog)
    if action:
        q = q.filter(AuditLog.action == action)
    return q.order_by(AuditLog.id.desc()).offset(max(offset, 0)).limit(min(max(limit, 1), 500)).all()


@router.get("/users", response_model=list[UserAdminOut])
def list_users(
    organization: Optional[str] = None,
    department: Optional[str] = None,
    admin: User = Depends(require_admin), db: Session = Depends(get_db),
):
    q = db.query(User, func.count(Project.id).label("project_count")).outerjoin(Project, Project.owner_id == User.id)
    if organization:
        q = q.filter(User.organization == organization)
    if department:
        q = q.filter(User.department == department)
    rows = q.group_by(User.id).order_by(User.created_at.desc()).all()
    result = []
    for user, count in rows:
        user.project_count = count
        result.append(user)
    return result


@router.get("/users/filter-options")
def get_user_filter_options(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Distinct organization/department values currently in use, for
    populating the Users tab's filter dropdowns."""
    orgs = [r[0] for r in db.query(User.organization).filter(User.organization.isnot(None)).distinct().order_by(User.organization).all()]
    depts = [r[0] for r in db.query(User.department).filter(User.department.isnot(None)).distinct().order_by(User.department).all()]
    return {"organizations": orgs, "departments": depts}


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

    changes = []
    if payload.role is not None and payload.role != target.role:
        changes.append(f"role {target.role} -> {payload.role}")
    if payload.is_active is not None and payload.is_active != target.is_active:
        changes.append("enabled" if payload.is_active else "disabled")
    if payload.role is not None:
        target.role = payload.role
    if payload.is_active is not None:
        target.is_active = payload.is_active
    if "organization" in payload.model_fields_set:
        target.organization = payload.organization
    if "department" in payload.model_fields_set:
        target.department = payload.department

    if changes:
        record(db, admin, "user_updated", "user", target.id, f"@{target.username}: " + ", ".join(changes))
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

    record(db, admin, "user_deleted", "user", target.id, f"@{target.username}")
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
    target.must_change_password = True  # they must pick their own at next sign-in
    destroy_all_sessions_for_user(db, target.id)
    record(db, admin, "password_reset_by_admin", "user", target.id, f"for @{target.username}")
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
# up, this works across schema changes — columns missing from an older
# backup just take the model's default.
#
# All of the actual logic lives in app/backup.py and is driven by the
# models themselves, so a new column is covered automatically and export
# refuses to run if a new table hasn't been registered there. (The first
# version listed fields by hand and silently lost survey data on restore.)
#
# Sessions and password-reset tokens are intentionally NOT included —
# they're short-lived by design, and forcing a fresh login after a
# restore is expected, not a bug.

def _schedule_out(s: SiteSettings) -> BackupScheduleOut:
    directory = s.backup_dir or DEFAULT_DIR
    try:
        files = sorted((p.name for p in Path(directory).expanduser().glob("backup-*.json")), reverse=True)[:5]
    except OSError:
        files = []
    return BackupScheduleOut(
        enabled=bool(s.backup_enabled), hour=s.backup_hour if s.backup_hour is not None else 2,
        keep=s.backup_keep or 14, directory=directory, copy_dir=s.backup_copy_dir,
        rclone=s.backup_rclone, rsync=s.backup_rsync, last_ok_at=s.backup_last_ok_at,
        last_attempt_at=s.backup_last_attempt_at, last_error=s.backup_last_error,
        server_time=datetime.datetime.now().astimezone().strftime("%H:%M %Z"), recent_files=files,
    )


@router.get("/backup-schedule", response_model=BackupScheduleOut)
def get_backup_schedule(superadmin: User = Depends(require_superadmin), db: Session = Depends(get_db)):
    return _schedule_out(_get_or_create_settings(db))


@router.put("/backup-schedule", response_model=BackupScheduleOut)
def set_backup_schedule(payload: BackupScheduleIn, superadmin: User = Depends(require_superadmin), db: Session = Depends(get_db)):
    """Saves the built-in backup schedule. Folders are test-written first, so a
    typo or an unplugged USB stick is caught now rather than at 2am."""
    s = _get_or_create_settings(db)
    directory = payload.directory or DEFAULT_DIR
    for label, folder in (("Backup folder", directory), ("Second folder", payload.copy_dir)):
        if folder:
            problem = check_destination(folder)
            if problem:
                raise HTTPException(status_code=400, detail=f"{label}: {problem}")
    s.backup_enabled, s.backup_hour, s.backup_keep = payload.enabled, payload.hour, payload.keep
    s.backup_dir = None if directory == DEFAULT_DIR else directory
    s.backup_copy_dir, s.backup_rclone, s.backup_rsync = payload.copy_dir, payload.rclone, payload.rsync
    record(db, superadmin, "backup_settings_changed", detail=(
        f"{'on, daily at %02d:00' % payload.hour if payload.enabled else 'off'}, keep {payload.keep}"))
    db.commit()
    return _schedule_out(s)


@router.post("/backup-now", response_model=BackupScheduleOut)
def backup_now(superadmin: User = Depends(require_superadmin), db: Session = Depends(get_db)):
    """Runs a backup right now with the saved folders (saved on this machine,
    not downloaded — use Export for a download)."""
    s = _get_or_create_settings(db)
    try:
        run_and_record(db, s)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Backup failed: {e}")
    record(db, superadmin, "backup_run_now")
    db.commit()
    db.refresh(s)
    return _schedule_out(s)


@router.get("/export")
def export_data(superadmin: User = Depends(require_superadmin), db: Session = Depends(get_db)):
    data = build_backup_dict(db)

    settings = _get_or_create_settings(db)
    settings.last_backup_at = datetime.datetime.utcnow()
    record(db, superadmin, "backup_exported")
    db.commit()

    filename = f"virtual-suggestion-box-backup-{datetime.datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.json"
    return Response(
        content=json.dumps(data, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/import")
def import_data(payload: dict = Body(...), superadmin: User = Depends(require_superadmin), db: Session = Depends(get_db)):
    try:
        counts = restore_backup_dict(db, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # The restore replaced every account, so the actor may no longer exist —
    # record who did it in the text instead of a foreign key.
    record(db, None, "backup_restored", detail=f"by @{superadmin.username}")
    db.commit()
    return {"status": "imported", **counts}
