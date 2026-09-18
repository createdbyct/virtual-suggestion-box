"""
Owner-facing endpoints — require login. Covers creating forms (projects),
listing forms you own or have been given view access to, viewing/exporting
their submissions, and — owner only — editing, deleting, sharing, and
transferring ownership.
"""
import csv
import io
import secrets
import string
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from ..auth import get_current_user, find_user_by_identifier
from ..database import get_db
from ..mailer import get_smtp_config, is_smtp_configured, send_email
from ..models import Project, ProjectShare, Submission, Nominee, User, WatchedForm, SiteSettings
from ..schemas import (
    ProjectCreate, ProjectOut, ProjectUpdate, SubmissionOut, SubmissionListOut,
    SubmissionStatusUpdate, SubmissionStatusCounts, ShareIn, ShareOut, TransferOwnershipIn,
)
from ..webhook import send_webhook_notification, build_new_form_message

router = APIRouter(prefix="/api/projects", tags=["projects"])

SHORT_CODE_CHARS = string.ascii_lowercase + string.digits


def _generate_short_code(db: Session, length: int = 5) -> str:
    """Short, random link code — like a URL shortener, not based on the
    title at all. 36^5 ≈ 60M possible codes, so collisions are
    negligible for any realistic number of forms; retries a few times
    just in case, then fails loudly rather than looping forever."""
    for _ in range(20):
        code = "".join(secrets.choice(SHORT_CODE_CHARS) for _ in range(length))
        if not db.query(Project).filter(Project.slug == code).first():
            return code
    raise HTTPException(status_code=500, detail="Could not generate a unique link — please try again")


def build_submissions_csv(project: Project, submissions: list[Submission]) -> StreamingResponse:
    """Shared by the owner/viewer export here and the admin export in
    admin.py, so the column layout can't drift between the two."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "submitted_at", "status", "anonymous", "submitter_name", "submitter_email",
        "suggestion", "nomination_reason", "nominees",
    ])
    for s in submissions:
        nominees_str = "; ".join(
            f"{n.name} ({n.role})" if n.role else n.name for n in s.nominees
        )
        writer.writerow([
            s.created_at.isoformat(),
            s.status,
            "yes" if s.is_anonymous else "no",
            s.submitter_name or "",
            s.submitter_email or "",
            s.suggestion_text or "",
            s.nomination_reason or "",
            nominees_str,
        ])

    buffer.seek(0)
    filename = f"{project.slug}-submissions.csv"
    return StreamingResponse(
        buffer,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _get_owned_project_or_404(project_id: int, user: User, db: Session) -> Project:
    """Strict owner-only access — for editing, deleting, sharing, and
    transferring. Being a shared viewer does NOT satisfy this check."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Form not found")
    if project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Only the owner can do that")
    return project


def _get_accessible_project_or_404(project_id: int, user: User, db: Session) -> Project:
    """Owner OR a user this form has been shared with — for viewing data."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Form not found")
    if project.owner_id == user.id:
        return project
    shared = (
        db.query(ProjectShare)
        .filter(ProjectShare.project_id == project.id, ProjectShare.user_id == user.id)
        .first()
    )
    if not shared:
        raise HTTPException(status_code=403, detail="You don't have access to this form")
    return project


@router.post("", response_model=ProjectOut)
def create_project(payload: ProjectCreate, background_tasks: BackgroundTasks, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # Always a short random code, like a URL shortener — no custom/vanity
    # link option, so there's no path to an arbitrarily long URL at all.
    slug = _generate_short_code(db)

    project = Project(
        owner_id=user.id,
        title=payload.title,
        type=payload.type,
        slug=slug,
    )
    db.add(project)
    db.commit()
    db.refresh(project)

    # Every new form is watched by default, so the superadmin doesn't have
    # to remember to go check a box for each one — they can always
    # unwatch it from Site Settings if they don't want the noise.
    db.add(WatchedForm(project_id=project.id))
    db.commit()

    # Best-effort, background — a slow/failed notification never blocks
    # form creation, same pattern as the new-submission notifications.
    site_settings = db.query(SiteSettings).filter(SiteSettings.id == 1).first()
    if site_settings and (site_settings.notification_webhook_url or site_settings.notification_email):
        message = build_new_form_message(project.title, user.name)
        if site_settings.notification_webhook_url:
            background_tasks.add_task(send_webhook_notification, site_settings.notification_webhook_url, message)
        if site_settings.notification_email:
            smtp_config = get_smtp_config(db)  # resolved now, synchronously — db may not
            if is_smtp_configured(smtp_config):  # still be valid by the time a background task runs
                background_tasks.add_task(
                    send_email, smtp_config, site_settings.notification_email,
                    f"New form created — {project.title}", message,
                )

    project.submission_count = 0
    project.new_submission_count = 0
    project.is_owner = True
    return project


@router.get("", response_model=list[ProjectOut])
def list_my_projects(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    new_count_expr = func.sum(case((Submission.status == "new", 1), else_=0))

    owned_rows = (
        db.query(Project, func.count(Submission.id).label("submission_count"), new_count_expr.label("new_count"))
        .outerjoin(Submission, Submission.project_id == Project.id)
        .filter(Project.owner_id == user.id)
        .group_by(Project.id)
        .all()
    )
    shared_rows = (
        db.query(Project, func.count(Submission.id).label("submission_count"), new_count_expr.label("new_count"), User.name)
        .join(ProjectShare, ProjectShare.project_id == Project.id)
        .join(User, Project.owner_id == User.id)
        .outerjoin(Submission, Submission.project_id == Project.id)
        .filter(ProjectShare.user_id == user.id)
        .group_by(Project.id)
        .all()
    )

    result = []
    for project, count, new_count in owned_rows:
        project.submission_count = count
        project.new_submission_count = new_count or 0
        project.is_owner = True
        result.append(project)
    for project, count, new_count, owner_name in shared_rows:
        project.submission_count = count
        project.new_submission_count = new_count or 0
        project.is_owner = False
        project.owner_name = owner_name
        result.append(project)

    result.sort(key=lambda p: p.created_at, reverse=True)
    return result


@router.get("/{project_id}/submissions/status-counts", response_model=SubmissionStatusCounts)
def get_status_counts(project_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Powers the filter pills on the submissions page — one grouped query
    instead of the frontend fetching each status count separately."""
    project = _get_accessible_project_or_404(project_id, user, db)
    rows = (
        db.query(Submission.status, func.count(Submission.id))
        .filter(Submission.project_id == project.id)
        .group_by(Submission.status)
        .all()
    )
    counts = SubmissionStatusCounts()
    for status, count in rows:
        setattr(counts, status, count)
        counts.total += count
    return counts


@router.get("/{project_id}/submissions", response_model=SubmissionListOut)
def list_submissions(
    project_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=200),
    search: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    project = _get_accessible_project_or_404(project_id, user, db)
    q = db.query(Submission).filter(Submission.project_id == project.id)

    if status:
        q = q.filter(Submission.status == status)

    if search:
        like = f"%{search}%"
        # Match on suggestion text, nomination reason, submitter name, or
        # any nominee's name — whichever the form actually has populated.
        q = q.outerjoin(Nominee, Nominee.submission_id == Submission.id).filter(
            or_(
                Submission.suggestion_text.ilike(like),
                Submission.nomination_reason.ilike(like),
                Submission.submitter_name.ilike(like),
                Nominee.name.ilike(like),
            )
        ).distinct()

    total = q.count()
    submissions = (
        q.order_by(Submission.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return SubmissionListOut(submissions=submissions, total=total)


@router.patch("/{project_id}/submissions/{submission_id}/status", response_model=SubmissionOut)
def update_submission_status(
    project_id: int, submission_id: int, payload: SubmissionStatusUpdate,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    # Accessible (owner or shared viewer) rather than owner-only — triaging
    # submissions is collaborative review, not a form-management action.
    project = _get_accessible_project_or_404(project_id, user, db)
    submission = db.query(Submission).filter(Submission.id == submission_id, Submission.project_id == project.id).first()
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    submission.status = payload.status
    db.commit()
    db.refresh(submission)
    return submission


@router.delete("/{project_id}/submissions/{submission_id}")
def delete_submission(
    project_id: int, submission_id: int,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    # Owner-only — deleting data is a management action, unlike viewing or
    # triaging it, which shared viewers can also do.
    project = _get_owned_project_or_404(project_id, user, db)
    submission = db.query(Submission).filter(Submission.id == submission_id, Submission.project_id == project.id).first()
    if not submission:
        raise HTTPException(status_code=404, detail="Submission not found")
    db.delete(submission)
    db.commit()
    return {"status": "deleted"}


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(project_id: int, payload: ProjectUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = _get_owned_project_or_404(project_id, user, db)

    if payload.title is not None:
        project.title = payload.title
    if payload.type is not None:
        project.type = payload.type

    db.commit()
    db.refresh(project)
    project.submission_count = db.query(func.count(Submission.id)).filter(Submission.project_id == project.id).scalar()
    project.new_submission_count = db.query(func.count(Submission.id)).filter(Submission.project_id == project.id, Submission.status == "new").scalar()
    project.is_owner = True
    return project


@router.delete("/{project_id}")
def delete_project(project_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = _get_owned_project_or_404(project_id, user, db)
    db.delete(project)  # cascades to submissions, nominees, and shares
    db.commit()
    return {"status": "deleted"}


@router.get("/{project_id}/export.csv")
def export_csv(project_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = _get_accessible_project_or_404(project_id, user, db)
    submissions = (
        db.query(Submission)
        .filter(Submission.project_id == project.id)
        .order_by(Submission.created_at.desc())
        .all()
    )
    return build_submissions_csv(project, submissions)


# ---- Sharing & ownership (owner only) ----

@router.get("/{project_id}/shares", response_model=list[ShareOut])
def list_shares(project_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = _get_owned_project_or_404(project_id, user, db)
    users = (
        db.query(User)
        .join(ProjectShare, ProjectShare.user_id == User.id)
        .filter(ProjectShare.project_id == project.id)
        .order_by(User.name)
        .all()
    )
    return [ShareOut(user_id=u.id, name=u.name, username=u.username, email=u.email) for u in users]


@router.post("/{project_id}/shares", response_model=ShareOut)
def add_share(project_id: int, payload: ShareIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = _get_owned_project_or_404(project_id, user, db)

    target = find_user_by_identifier(payload.identifier, db)
    if not target:
        raise HTTPException(status_code=404, detail="No account found with that email or username")
    if target.id == user.id:
        raise HTTPException(status_code=400, detail="You already own this form")

    existing = (
        db.query(ProjectShare)
        .filter(ProjectShare.project_id == project.id, ProjectShare.user_id == target.id)
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="This person already has access")

    db.add(ProjectShare(project_id=project.id, user_id=target.id))
    db.commit()
    return ShareOut(user_id=target.id, name=target.name, username=target.username, email=target.email)


@router.delete("/{project_id}/shares/{user_id}")
def remove_share(project_id: int, user_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = _get_owned_project_or_404(project_id, user, db)
    db.query(ProjectShare).filter(
        ProjectShare.project_id == project.id, ProjectShare.user_id == user_id
    ).delete()
    db.commit()
    return {"status": "removed"}


@router.post("/{project_id}/transfer-ownership", response_model=ProjectOut)
def transfer_ownership(project_id: int, payload: TransferOwnershipIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    project = _get_owned_project_or_404(project_id, user, db)

    target = find_user_by_identifier(payload.identifier, db)
    if not target:
        raise HTTPException(status_code=404, detail="No account found with that email or username")
    if target.id == user.id:
        raise HTTPException(status_code=400, detail="You already own this form")

    # The new owner shouldn't also have a separate viewer-share row.
    db.query(ProjectShare).filter(
        ProjectShare.project_id == project.id, ProjectShare.user_id == target.id
    ).delete()

    # Give the outgoing owner viewer access so they don't lose visibility
    # into data they used to own, unless they already have it somehow.
    already_shared = (
        db.query(ProjectShare)
        .filter(ProjectShare.project_id == project.id, ProjectShare.user_id == user.id)
        .first()
    )
    if not already_shared:
        db.add(ProjectShare(project_id=project.id, user_id=user.id))

    project.owner_id = target.id
    db.commit()
    db.refresh(project)
    project.submission_count = db.query(func.count(Submission.id)).filter(Submission.project_id == project.id).scalar()
    project.new_submission_count = db.query(func.count(Submission.id)).filter(Submission.project_id == project.id, Submission.status == "new").scalar()
    project.is_owner = False
    return project
