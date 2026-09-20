"""
SQLAlchemy models: users (admin/owner), projects (forms), submissions.
"""
import secrets
from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, Boolean, ForeignKey, DateTime, CheckConstraint,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from .database import Base


def gen_token() -> str:
    """Opaque, unguessable token for edit links."""
    return secrets.token_urlsafe(32)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    username = Column(String, unique=True, nullable=False, index=True)
    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False, default="owner")
    is_active = Column(Boolean, nullable=False, default=True)
    # 2FA (TOTP) — secret is only meaningful once totp_enabled is True.
    # A secret can exist while disabled (mid-setup, not yet confirmed);
    # only a confirmed code flips totp_enabled on.
    totp_secret = Column(String, nullable=True)
    totp_enabled = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    projects = relationship("Project", back_populates="owner", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("role IN ('admin', 'owner', 'superadmin')", name="ck_user_role"),
    )


class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String, nullable=False)
    type = Column(String, nullable=False)  # 'suggestion' | 'nomination' | 'both'
    slug = Column(String, unique=True, nullable=False, index=True)
    # Optional — shown as the form's subtitle on the public page if set;
    # falls back to a generic per-type description if left blank.
    description = Column(String, nullable=True)
    # Every form always accepts anonymous submissions — a name is optional,
    # never required — so there's no per-form toggle for it anymore.
    # Notifications are superadmin-only and global (see SiteSettings +
    # WatchedForm below) — no per-form notification config on purpose.
    created_at = Column(DateTime, default=datetime.utcnow)

    owner = relationship("User", back_populates="projects")
    submissions = relationship("Submission", back_populates="project", cascade="all, delete-orphan")
    survey_questions = relationship("SurveyQuestion", back_populates="project", cascade="all, delete-orphan", order_by="SurveyQuestion.display_order")
    # People other than the owner who can view (not edit) this form's data.
    shares = relationship("ProjectShare", back_populates="project", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("type IN ('suggestion', 'nomination', 'both', 'survey')", name="ck_project_type"),
    )


class Submission(Base):
    __tablename__ = "submissions"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    # Both are nullable since a single submission can be a suggestion only,
    # a nomination only, or both at once — whichever the submitter picked
    # on a 'both'-type box. At least one is always populated (enforced in
    # the router, not the schema, since it depends on project.type).
    suggestion_text = Column(String, nullable=True)
    nomination_reason = Column(String, nullable=True)
    submitter_name = Column(String, nullable=True)
    submitter_email = Column(String, nullable=True)
    is_anonymous = Column(Boolean, default=False)
    edit_token = Column(String, unique=True, default=gen_token, index=True)
    edit_expires_at = Column(DateTime, nullable=False)
    # Triage state for whoever's working through submissions — owner or a
    # shared viewer can both update this, since it's collaborative review,
    # not a form-management action.
    status = Column(String, nullable=False, default="new")
    created_at = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project", back_populates="submissions")
    # Only populated when this submission includes a nomination. One
    # submission can name several people, each with their own role — e.g.
    # nominating both a cashier and a shift lead in the same nomination.
    nominees = relationship("Nominee", back_populates="submission", cascade="all, delete-orphan", order_by="Nominee.id")
    # Optional survey answers, attached to this same submission — a form
    # can have suggestion/nomination content AND survey answers together,
    # since questions are an add-on to any form type, not a separate one.
    survey_answers = relationship("SurveyAnswer", back_populates="submission", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("status IN ('new', 'reviewed', 'done')", name="ck_submission_status"),
    )


class Nominee(Base):
    __tablename__ = "nominees"

    id = Column(Integer, primary_key=True)
    submission_id = Column(Integer, ForeignKey("submissions.id"), nullable=False)
    name = Column(String, nullable=False)
    role = Column(String, nullable=True)

    submission = relationship("Submission", back_populates="nominees")


class SurveyQuestion(Base):
    """A question attached to a form — add-on to any form type (suggestion,
    nomination, or both), not a separate form kind. Deleting a question
    cascades to delete its historical answers too, same permanence as
    deleting a form or a submission elsewhere in this app."""
    __tablename__ = "survey_questions"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    question_text = Column(String, nullable=False)
    question_type = Column(String, nullable=False)  # 'multiple_choice' | 'rating' | 'short_text' | 'yes_no'
    # JSON-encoded list of strings — only meaningful for multiple_choice.
    options = Column(String, nullable=True)
    required = Column(Boolean, nullable=False, default=False)
    display_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project", back_populates="survey_questions")
    answers = relationship("SurveyAnswer", back_populates="question", cascade="all, delete-orphan")


class SurveyAnswer(Base):
    """One answer to one question, attached to a submission — a single
    submission can carry suggestion/nomination content and survey
    answers together, since questions are an add-on, not their own
    submission flow."""
    __tablename__ = "survey_answers"

    id = Column(Integer, primary_key=True)
    submission_id = Column(Integer, ForeignKey("submissions.id"), nullable=False)
    question_id = Column(Integer, ForeignKey("survey_questions.id"), nullable=False)
    # The actual answer — selected option text, a rating as a string,
    # free text, or "Yes"/"No". Kept as a single string column rather
    # than type-specific columns, since only one question type ever
    # applies per answer and this keeps analytics queries simple.
    answer_text = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    submission = relationship("Submission", back_populates="survey_answers")
    question = relationship("SurveyQuestion", back_populates="answers")

    # Plain Python properties (not columns) so SubmissionOut's automatic
    # ORM-to-schema conversion can read question_text/question_type
    # directly off each answer, without every endpoint having to
    # manually join and rebuild this list by hand.
    @property
    def question_text(self):
        return self.question.question_text if self.question else None

    @property
    def question_type(self):
        return self.question.question_type if self.question else None


class ProjectShare(Base):
    """Grants a user (not the owner) view access to a form's submissions.
    Only the owner can create/remove these or transfer ownership — sharing
    itself never grants management rights."""
    __tablename__ = "project_shares"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project", back_populates="shares")

    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_project_share_user"),
    )


class AuthSession(Base):
    """Login session, keyed by an opaque cookie token — not SQLAlchemy's
    own Session class, hence the Auth prefix to avoid confusion."""
    __tablename__ = "auth_sessions"

    id = Column(Integer, primary_key=True)
    token = Column(String, unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    ip_address = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class PasswordResetToken(Base):
    """Forgot-password token. There's no email service configured, so the
    reset link is shown directly on-screen to whoever requested it rather
    than emailed — see /forgot-password. Single-use, short expiry."""
    __tablename__ = "password_reset_tokens"

    id = Column(Integer, primary_key=True)
    token = Column(String, unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    used = Column(Boolean, nullable=False, default=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class RecoveryCode(Base):
    """One-time-use 2FA backup codes, issued in a batch when 2FA is turned
    on. Hashed at rest the same way passwords are — shown in plaintext
    exactly once, at generation time."""
    __tablename__ = "recovery_codes"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    code_hash = Column(String, nullable=False)
    used = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class SiteSettings(Base):
    """Singleton row (always id=1) for site-wide config — superadmin only.
    Explicit typed columns rather than a generic key-value store, matching
    the rest of this codebase; add a column if a new setting shows up."""
    __tablename__ = "site_settings"

    id = Column(Integer, primary_key=True)
    footer_text = Column(String, nullable=True)
    footer_link_url = Column(String, nullable=True)
    dark_mode_enabled = Column(Boolean, nullable=False, default=True)
    # Global notification channels (webhook and/or email) — separate from
    # any per-form config (there is none — notifications are superadmin-
    # only and global by design). Both fire independently for whichever
    # forms are in WatchedForm, regardless of who owns them.
    notification_webhook_url = Column(String, nullable=True)
    notification_email = Column(String, nullable=True)
    # SMTP config, settable via the superadmin UI — falls back to env
    # vars (SMTP_HOST etc.) if any of these are unset, so an existing
    # systemd-based deployment keeps working without changes.
    smtp_host = Column(String, nullable=True)
    smtp_port = Column(Integer, nullable=True)
    smtp_username = Column(String, nullable=True)
    smtp_password = Column(String, nullable=True)
    smtp_from_email = Column(String, nullable=True)
    smtp_from_name = Column(String, nullable=True)
    # Set automatically by backup_db.py and by the manual export endpoint —
    # not user-editable directly.
    last_backup_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class WatchedForm(Base):
    """A form the superadmin wants global notifications for. Independent
    of who owns the form — superadmin sees and can watch any form.
    Per-channel, not all-or-nothing: a form can be watched via email
    only, webhook only, or both — whichever the superadmin picks. If
    both end up off, the row is deleted entirely rather than kept
    around as a no-op (see toggle_watch_form in routers/admin.py)."""
    __tablename__ = "watched_forms"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, unique=True)
    notify_email = Column(Boolean, nullable=False, default=True)
    notify_webhook = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
