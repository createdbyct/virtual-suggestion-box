"""
Public-facing endpoints — no login required.
Covers: viewing a form by its slug, submitting to it, and editing
a submission within the 24hr window via its edit token.
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..database import get_db
from ..mailer import is_email_configured, send_email
from ..models import Project, Submission, Nominee
from ..rate_limit import check_rate_limit, get_client_ip
from ..schemas import (
    SubmissionCreate, SubmissionEdit, SubmissionOut,
    SubmissionConfirmation, ProjectPublicOut,
)
from ..webhook import send_webhook_notification, build_submission_message

router = APIRouter(prefix="/api/vb", tags=["public"])

# Hardcoded per project preference — not a settings-UI field.
EDIT_WINDOW_HOURS = 24

# Anti-spam ceiling on public submission — generous enough for real shared
# office/wifi IPs, tight enough to stop a naive script.
SUBMIT_RATE_MAX = 10
SUBMIT_RATE_WINDOW_SECONDS = 600  # 10 minutes


def _get_project_or_404(slug: str, db: Session) -> Project:
    project = db.query(Project).filter(Project.slug == slug).first()
    if not project:
        raise HTTPException(status_code=404, detail="Form not found")
    return project


@router.get("/{slug}", response_model=ProjectPublicOut)
def get_form(slug: str, db: Session = Depends(get_db)):
    """Public metadata for a form — used to render the submission page."""
    return _get_project_or_404(slug, db)


@router.post("/{slug}/submit", response_model=SubmissionConfirmation)
def submit(slug: str, payload: SubmissionCreate, request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    ip = get_client_ip(request)
    if not check_rate_limit(f"submit:{ip}", SUBMIT_RATE_MAX, SUBMIT_RATE_WINDOW_SECONDS):
        raise HTTPException(status_code=429, detail="Too many submissions from this connection — please wait a bit and try again")

    project = _get_project_or_404(slug, db)

    # Every form always accepts anonymous submissions. Whether this one
    # counts as anonymous is just whatever the submitter chose to fill in.
    is_anonymous = not bool(payload.submitter_name)

    has_suggestion = bool(payload.suggestion_text)
    has_nomination = bool(payload.nominees)

    # Which sections are even allowed on this form, and did the submitter
    # fill in something valid for at least one of them?
    if project.type == "suggestion":
        if not has_suggestion:
            raise HTTPException(status_code=400, detail="A suggestion is required for this form")
        has_nomination = False  # ignore stray nominee data on a suggestion-only form
    elif project.type == "nomination":
        if not has_nomination:
            raise HTTPException(status_code=400, detail="At least one nominee is required for this form")
        has_suggestion = False
    else:  # 'both' — submitter chooses either or both
        if not has_suggestion and not has_nomination:
            raise HTTPException(status_code=400, detail="Add a suggestion, a nomination, or both")

    expires_at = datetime.utcnow() + timedelta(hours=EDIT_WINDOW_HOURS)

    submission = Submission(
        project_id=project.id,
        suggestion_text=payload.suggestion_text if has_suggestion else None,
        nomination_reason=payload.nomination_reason if has_nomination else None,
        submitter_name=payload.submitter_name,
        submitter_email=payload.submitter_email if not is_anonymous else None,
        is_anonymous=is_anonymous,
        edit_expires_at=expires_at,
    )
    if has_nomination:
        submission.nominees = [
            Nominee(name=n.name, role=n.role) for n in payload.nominees
        ]

    db.add(submission)
    db.commit()
    db.refresh(submission)

    # Best-effort notifications — fired as background tasks so a slow or
    # failing webhook/email send never adds latency to the submitter's
    # response, and never breaks the submission itself either way.
    if project.webhook_url or project.notify_email:
        nominee_names = [n.name for n in payload.nominees] if has_nomination else []
        message = build_submission_message(
            project.title, payload.submitter_name, is_anonymous,
            submission.suggestion_text, submission.nomination_reason, nominee_names,
        )
        if project.webhook_url:
            background_tasks.add_task(send_webhook_notification, project.webhook_url, message)
        if project.notify_email and is_email_configured():
            background_tasks.add_task(
                send_email, project.notify_email,
                f"New submission — {project.title}", message,
            )

    return SubmissionConfirmation(
        id=submission.id,
        edit_url=f"/edit/{submission.edit_token}",
        edit_expires_at=submission.edit_expires_at,
    )


def _get_editable_submission_or_404(token: str, db: Session) -> Submission:
    submission = db.query(Submission).filter(Submission.edit_token == token).first()
    if not submission:
        raise HTTPException(status_code=404, detail="Edit link not found")
    if datetime.utcnow() > submission.edit_expires_at:
        raise HTTPException(status_code=410, detail="Edit window has expired")
    return submission


# Separate router (mounted at /api/edit instead of /api/b) since edit links
# are token-based and don't belong under a specific form's slug path.
edit_router = APIRouter(prefix="/api/edit", tags=["public-edit"])


@edit_router.get("/{token}", response_model=SubmissionOut)
def get_submission_for_edit(token: str, db: Session = Depends(get_db)):
    return _get_editable_submission_or_404(token, db)


@edit_router.put("/{token}", response_model=SubmissionOut)
def edit_submission(token: str, payload: SubmissionEdit, db: Session = Depends(get_db)):
    submission = _get_editable_submission_or_404(token, db)
    project = submission.project

    has_suggestion = bool(payload.suggestion_text)
    has_nomination = bool(payload.nominees)

    if not has_suggestion and not has_nomination:
        raise HTTPException(status_code=400, detail="Add a suggestion, a nomination, or both")

    # Keep edits within what the form actually supports.
    if project.type == "suggestion" and has_nomination:
        raise HTTPException(status_code=400, detail="This form doesn't accept nominations")
    if project.type == "nomination" and has_suggestion:
        raise HTTPException(status_code=400, detail="This form doesn't accept suggestions")

    submission.suggestion_text = payload.suggestion_text if has_suggestion else None
    submission.nomination_reason = payload.nomination_reason if has_nomination else None

    submission.nominees.clear()
    if has_nomination:
        submission.nominees = [
            Nominee(name=n.name, role=n.role) for n in payload.nominees
        ]

    db.commit()
    db.refresh(submission)
    return submission
