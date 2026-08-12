"""
Admin-only endpoints — see every owner's forms and submissions, and
manage user accounts (promote/demote, disable/enable, delete, reset
password).
"""
import secrets

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import destroy_all_sessions_for_user, hash_password, require_admin
from ..database import get_db
from ..models import Project, Submission, User
from .projects import build_submissions_csv
from ..schemas import (
    ProjectAdminOut, SubmissionOut, UserAdminOut, UserUpdateIn,
    AdminResetPasswordOut,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


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
    result = []
    for project, owner_email, count in rows:
        project.submission_count = count
        project.owner_email = owner_email
        result.append(project)
    return result


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

    temp_password = secrets.token_urlsafe(9)  # ~12 readable chars
    target.password_hash = hash_password(temp_password)
    destroy_all_sessions_for_user(db, target.id)
    db.commit()
    return AdminResetPasswordOut(temporary_password=temp_password)
