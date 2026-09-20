"""
Pydantic schemas — request/response shapes, separate from the DB models.
"""
import json
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, EmailStr, field_validator, model_validator


class NomineeIn(BaseModel):
    name: str
    role: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("nominee name cannot be blank")
        return v


class NomineeOut(BaseModel):
    name: str
    role: Optional[str]

    class Config:
        from_attributes = True


QUESTION_TYPES = ("multiple_choice", "rating", "short_text", "yes_no")


class SurveyQuestionCreate(BaseModel):
    question_text: str
    question_type: str  # 'multiple_choice' | 'rating' | 'short_text' | 'yes_no'
    options: Optional[List[str]] = None  # required, 2+ entries, only for multiple_choice
    required: bool = False

    @field_validator("question_text")
    @classmethod
    def text_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("question text cannot be blank")
        return v

    @field_validator("question_type")
    @classmethod
    def type_valid(cls, v: str) -> str:
        if v not in QUESTION_TYPES:
            raise ValueError(f"question_type must be one of {QUESTION_TYPES}")
        return v

    @field_validator("options")
    @classmethod
    def clean_options(cls, v):
        if v is None:
            return None
        cleaned = [o.strip() for o in v if o and o.strip()]
        return cleaned or None

    @model_validator(mode="after")
    def options_required_for_multiple_choice(self):
        if self.question_type == "multiple_choice" and (not self.options or len(self.options) < 2):
            raise ValueError("multiple choice questions need at least 2 options")
        return self


class SurveyQuestionUpdate(BaseModel):
    """All fields optional — PATCH semantics. No cross-field options/type
    check here (unlike Create) since a partial update might touch only
    one field; the router keeps whichever value isn't being changed."""
    question_text: Optional[str] = None
    question_type: Optional[str] = None
    options: Optional[List[str]] = None
    required: Optional[bool] = None

    @field_validator("question_text")
    @classmethod
    def text_not_blank(cls, v):
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("question text cannot be blank")
        return v

    @field_validator("question_type")
    @classmethod
    def type_valid(cls, v):
        if v is not None and v not in QUESTION_TYPES:
            raise ValueError(f"question_type must be one of {QUESTION_TYPES}")
        return v

    @field_validator("options")
    @classmethod
    def clean_options(cls, v):
        if v is None:
            return None
        cleaned = [o.strip() for o in v if o and o.strip()]
        return cleaned or None


class SurveyQuestionReorder(BaseModel):
    question_ids: List[int]  # full ordered list of every question's id for this form


class SurveyQuestionOut(BaseModel):
    id: int
    question_text: str
    question_type: str
    options: Optional[List[str]] = None
    required: bool
    display_order: int

    class Config:
        from_attributes = True

    @field_validator("options", mode="before")
    @classmethod
    def parse_options(cls, v):
        if v is None or isinstance(v, list):
            return v
        try:
            return json.loads(v)
        except (json.JSONDecodeError, TypeError):
            return None


class SurveyAnswerIn(BaseModel):
    """Only include an entry for a question the submitter actually
    answered — omit the question_id entirely to skip it, rather than
    submitting a blank answer_text."""
    question_id: int
    answer_text: str

    @field_validator("answer_text")
    @classmethod
    def answer_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("answer cannot be blank — omit this question instead of sending an empty answer")
        return v


class SurveyAnswerOut(BaseModel):
    question_id: int
    question_text: str
    question_type: str
    answer_text: str

    class Config:
        from_attributes = True


class QuestionAnalytics(BaseModel):
    question_id: int
    question_text: str
    question_type: str
    response_count: int
    option_counts: Optional[dict] = None  # multiple_choice / yes_no — option text -> count
    average_rating: Optional[float] = None  # rating questions only
    text_answers: Optional[List[str]] = None  # short_text questions only — raw answers


class SurveyAnalyticsOut(BaseModel):
    total_submissions: int
    questions: List[QuestionAnalytics]


class SubmissionCreate(BaseModel):
    # A submitter fills in whichever of these apply to what they checked —
    # neither is individually required here; the router enforces "at least
    # one of suggestion_text or nominees" based on the project's type.
    # Every form always accepts anonymous submissions — submitter_name is
    # purely optional and never required. Whether the submission counts as
    # anonymous is derived server-side from whether a name was given.
    suggestion_text: Optional[str] = None
    nomination_reason: Optional[str] = None
    nominees: Optional[List[NomineeIn]] = None
    submitter_name: Optional[str] = None
    submitter_email: Optional[EmailStr] = None
    survey_answers: Optional[List[SurveyAnswerIn]] = None

    @field_validator("submitter_name")
    @classmethod
    def strip_name(cls, v):
        if v is None:
            return None
        v = v.strip()
        return v or None

    @field_validator("suggestion_text", "nomination_reason")
    @classmethod
    def strip_or_none(cls, v):
        if v is None:
            return None
        v = v.strip()
        return v or None


class SubmissionOut(BaseModel):
    id: int
    suggestion_text: Optional[str]
    nomination_reason: Optional[str]
    nominees: List[NomineeOut] = []
    survey_answers: List[SurveyAnswerOut] = []
    submitter_name: Optional[str]
    submitter_email: Optional[str]
    is_anonymous: bool
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


class SubmissionStatusUpdate(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def status_valid(cls, v: str) -> str:
        if v not in ("new", "reviewed", "done"):
            raise ValueError("status must be 'new', 'reviewed', or 'done'")
        return v


class SubmissionListOut(BaseModel):
    """Paginated submission list — total is the count before pagination,
    so the frontend can render page controls."""
    submissions: List[SubmissionOut]
    total: int


class SubmissionStatusCounts(BaseModel):
    new: int = 0
    reviewed: int = 0
    done: int = 0
    total: int = 0


class SubmissionConfirmation(BaseModel):
    """Returned right after submit — includes the one-time edit link."""
    id: int
    edit_url: str
    edit_expires_at: datetime


class ProjectPublicOut(BaseModel):
    """What a public visitor is allowed to see about a project."""
    title: str
    type: str  # 'suggestion' | 'nomination' | 'both'
    description: Optional[str] = None
    survey_questions: List[SurveyQuestionOut] = []
    public_analytics: bool = False

    class Config:
        from_attributes = True


# ---- Auth ----

class RegisterIn(BaseModel):
    name: str
    username: str
    email: EmailStr
    password: str

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name cannot be blank")
        return v

    @field_validator("username")
    @classmethod
    def username_format(cls, v: str) -> str:
        v = v.strip().lower()
        import re
        if not re.fullmatch(r"[a-z0-9_-]{3,30}", v):
            raise ValueError("username must be 3-30 characters: letters, numbers, underscores, or hyphens")
        return v

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("password must be at least 8 characters")
        return v


class LoginIn(BaseModel):
    identifier: str  # email or username
    password: str
    totp_code: Optional[str] = None


class UserOut(BaseModel):
    id: int
    name: str
    username: str
    email: str
    role: str
    totp_enabled: bool

    class Config:
        from_attributes = True


class UserAdminOut(BaseModel):
    id: int
    name: str
    username: str
    email: str
    role: str
    is_active: bool
    totp_enabled: bool
    created_at: datetime
    project_count: int = 0

    class Config:
        from_attributes = True


class UserUpdateIn(BaseModel):
    role: Optional[str] = None
    is_active: Optional[bool] = None

    @field_validator("role")
    @classmethod
    def role_valid(cls, v):
        if v is not None and v not in ("admin", "owner"):
            raise ValueError("role must be 'admin' or 'owner'")
        return v


class UserSelfUpdateIn(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name cannot be blank")
        return v


# ---- Password reset ----

class ForgotPasswordIn(BaseModel):
    identifier: str  # email or username


class ForgotPasswordOut(BaseModel):
    """delivered_via is 'email' when SMTP is configured and the send
    succeeded, otherwise 'on_screen' — reset_url is only populated in the
    latter case. See mailer.py for the fallback logic."""
    delivered_via: str
    reset_url: Optional[str] = None
    expires_at: datetime


class ResetPasswordIn(BaseModel):
    new_password: str

    @field_validator("new_password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("password must be at least 8 characters")
        return v


class ResetPasswordTokenInfo(BaseModel):
    valid: bool
    username: Optional[str] = None


class AdminResetPasswordOut(BaseModel):
    """Temporary password shown once to the admin performing the reset —
    they're responsible for getting it to the account holder out of band."""
    temporary_password: str


# ---- Site settings (superadmin only) ----

class SiteSettingsOut(BaseModel):
    """Public shape — every page fetches this, including anonymous
    visitors, so it deliberately excludes notification_webhook_url,
    notification_email, and last_backup_at. See SiteSettingsAdminOut for
    the superadmin view."""
    footer_text: Optional[str] = None
    footer_link_url: Optional[str] = None
    dark_mode_enabled: bool = True

    class Config:
        from_attributes = True


class SiteSettingsAdminOut(SiteSettingsOut):
    notification_webhook_url: Optional[str] = None
    notification_email: Optional[str] = None
    last_backup_at: Optional[datetime] = None
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_username: Optional[str] = None
    smtp_from_email: Optional[str] = None
    smtp_from_name: Optional[str] = None
    # Never the actual password — just whether one is currently set, so
    # the UI can show "configured" without ever exposing the value back.
    smtp_password_set: bool = False


class SiteSettingsUpdate(BaseModel):
    footer_text: Optional[str] = None
    footer_link_url: Optional[str] = None
    dark_mode_enabled: Optional[bool] = None
    notification_webhook_url: Optional[str] = None
    notification_email: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_username: Optional[str] = None
    # Blank/omitted means "leave the existing password alone" — there's
    # deliberately no way to explicitly clear it back to empty via this
    # field; unset the whole SMTP config's other fields if that's the goal.
    smtp_password: Optional[str] = None
    smtp_from_email: Optional[str] = None
    smtp_from_name: Optional[str] = None

    @field_validator("footer_text", "footer_link_url", "notification_webhook_url", "notification_email",
                      "smtp_host", "smtp_username", "smtp_from_email", "smtp_from_name")
    @classmethod
    def blank_to_none(cls, v):
        if v is None:
            return None
        v = v.strip()
        return v or None


# ---- Sessions ----

class SessionOut(BaseModel):
    id: int
    ip_address: Optional[str]
    user_agent: Optional[str]
    created_at: datetime
    expires_at: datetime
    is_current: bool = False

    class Config:
        from_attributes = True


# ---- Two-factor auth ----

class TotpSetupOut(BaseModel):
    secret: str
    otpauth_url: str


class TotpConfirmIn(BaseModel):
    code: str


class TotpConfirmOut(BaseModel):
    user: UserOut
    recovery_codes: List[str]


class TotpDisableIn(BaseModel):
    password: str


class RecoveryCodesOut(BaseModel):
    codes: List[str]


# ---- Owner project management ----

class ProjectCreate(BaseModel):
    title: str
    type: str  # 'suggestion' | 'nomination' | 'both'
    description: Optional[str] = None  # shown as the form's subtitle if set; generic fallback otherwise
    # No custom/vanity link option — every form gets a short random code
    # (see _generate_short_code in routers/projects.py).

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("title cannot be blank")
        return v

    @field_validator("description")
    @classmethod
    def description_blank_to_none(cls, v):
        if v is None:
            return None
        v = v.strip()
        return v or None

    @field_validator("type")
    @classmethod
    def type_valid(cls, v: str) -> str:
        if v not in ("suggestion", "nomination", "both", "survey"):
            raise ValueError("type must be 'suggestion', 'nomination', 'both', or 'survey'")
        return v


class ProjectOut(BaseModel):
    id: int
    title: str
    type: str
    slug: str
    description: Optional[str] = None
    public_analytics: bool = False
    created_at: datetime
    submission_count: int = 0
    new_submission_count: int = 0
    is_owner: bool = True
    owner_name: Optional[str] = None  # populated only for forms shared with you

    class Config:
        from_attributes = True


class ShareIn(BaseModel):
    identifier: str  # email or username of the person to share with


class ShareOut(BaseModel):
    user_id: int
    name: str
    username: str
    email: str


class TransferOwnershipIn(BaseModel):
    identifier: str  # email or username of the new owner


class ProjectUpdate(BaseModel):
    """All fields optional — PATCH semantics, only provided fields change."""
    title: Optional[str] = None
    type: Optional[str] = None
    description: Optional[str] = None
    public_analytics: Optional[bool] = None

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, v):
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("title cannot be blank")
        return v

    @field_validator("description")
    @classmethod
    def description_blank_to_none(cls, v):
        if v is None:
            return None
        v = v.strip()
        return v or None

    @field_validator("type")
    @classmethod
    def type_valid(cls, v):
        if v is not None and v not in ("suggestion", "nomination", "both", "survey"):
            raise ValueError("type must be 'suggestion', 'nomination', 'both', or 'survey'")
        return v


class ProjectAdminOut(ProjectOut):
    owner_id: int
    owner_email: str
    notify_email: bool = False
    notify_webhook: bool = False
