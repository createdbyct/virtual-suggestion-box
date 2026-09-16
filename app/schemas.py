"""
Pydantic schemas — request/response shapes, separate from the DB models.
"""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, EmailStr, field_validator


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


class SubmissionEdit(BaseModel):
    suggestion_text: Optional[str] = None
    nomination_reason: Optional[str] = None
    nominees: Optional[List[NomineeIn]] = None

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
    visitors, so it deliberately excludes notification_webhook_url and
    last_backup_at. See SiteSettingsAdminOut for the superadmin view."""
    footer_text: Optional[str] = None
    footer_link_url: Optional[str] = None
    dark_mode_enabled: bool = True

    class Config:
        from_attributes = True


class SiteSettingsAdminOut(SiteSettingsOut):
    notification_webhook_url: Optional[str] = None
    last_backup_at: Optional[datetime] = None


class SiteSettingsUpdate(BaseModel):
    footer_text: Optional[str] = None
    footer_link_url: Optional[str] = None
    dark_mode_enabled: Optional[bool] = None
    notification_webhook_url: Optional[str] = None

    @field_validator("footer_text", "footer_link_url", "notification_webhook_url")
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
    slug: Optional[str] = None  # auto-generated from title if omitted
    webhook_url: Optional[str] = None
    notify_email: Optional[str] = None

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("title cannot be blank")
        return v

    @field_validator("webhook_url", "notify_email")
    @classmethod
    def blank_to_none(cls, v):
        if v is None:
            return None
        v = v.strip()
        return v or None

    @field_validator("type")
    @classmethod
    def type_valid(cls, v: str) -> str:
        if v not in ("suggestion", "nomination", "both"):
            raise ValueError("type must be 'suggestion', 'nomination', or 'both'")
        return v

    @field_validator("slug")
    @classmethod
    def slug_format(cls, v):
        if v is None:
            return v
        v = v.strip().lower()
        if not v:
            return None
        import re
        if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", v):
            raise ValueError("slug can only contain lowercase letters, numbers, and hyphens")
        return v


class ProjectOut(BaseModel):
    id: int
    title: str
    type: str
    slug: str
    webhook_url: Optional[str] = None
    notify_email: Optional[str] = None
    created_at: datetime
    submission_count: int = 0
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
    slug: Optional[str] = None
    webhook_url: Optional[str] = None
    notify_email: Optional[str] = None

    @field_validator("webhook_url", "notify_email")
    @classmethod
    def blank_to_none(cls, v):
        if v is None:
            return None
        v = v.strip()
        return v or None

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, v):
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("title cannot be blank")
        return v

    @field_validator("type")
    @classmethod
    def type_valid(cls, v):
        if v is not None and v not in ("suggestion", "nomination", "both"):
            raise ValueError("type must be 'suggestion', 'nomination', or 'both'")
        return v

    @field_validator("slug")
    @classmethod
    def slug_format(cls, v):
        if v is None:
            return v
        v = v.strip().lower()
        if not v:
            raise ValueError("slug cannot be blank")
        import re
        if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", v):
            raise ValueError("slug can only contain lowercase letters, numbers, and hyphens")
        return v


class ProjectAdminOut(ProjectOut):
    owner_email: str
    is_watched: bool = False
