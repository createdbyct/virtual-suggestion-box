"""
Every state machine in the app, in one place.

Why this file exists
--------------------
The app has always had state machines — a submission is "new" until someone
reviews it, a form is open until it expires, a password-reset link works
once — but they lived implicitly: a boolean here, a timestamp comparison
there, a magic string in an error message. This module makes each one
explicit so that

  * people (DX) can read the whole behaviour of the app in one screen,
  * the UI (UX) can render the right badge/button from a single derived
    value instead of re-deriving it in JavaScript, and
  * scripts and AI agents (AX) can read a machine-readable `state`, the
    list of legal next moves, and a stable `X-Error-Code` on failures
    instead of parsing English sentences.

Nothing here changes what the database stores. States are *derived* from
the columns that already exist, so there is no migration and old data keeps
working. See docs/STATE_MACHINES.md for diagrams.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Dict, FrozenSet, Iterable, Optional


class StrEnum(str, Enum):
    """str-valued enum that serialises as its plain value in JSON."""
    def __str__(self) -> str:
        return self.value


# ---------------------------------------------------------------------------
# 1. Submission review status  (stored: submissions.status)
# ---------------------------------------------------------------------------
class SubmissionStatus(StrEnum):
    NEW = "new"            # shown to the team as "Needs review"
    REVIEWED = "reviewed"


SUBMISSION_STATUS_VALUES = tuple(s.value for s in SubmissionStatus)

# Reviewing is collaborative and reversible, so each state can reach the
# other. Listed explicitly so adding a state later forces a decision here.
SUBMISSION_TRANSITIONS: Dict[SubmissionStatus, FrozenSet[SubmissionStatus]] = {
    SubmissionStatus.NEW: frozenset({SubmissionStatus.REVIEWED}),
    SubmissionStatus.REVIEWED: frozenset({SubmissionStatus.NEW}),
}

# The retired "done" status folds into "reviewed" (see migrate.retire_done_status).
_SUBMISSION_ALIASES = {"done": SubmissionStatus.REVIEWED}


def parse_submission_status(value: str) -> SubmissionStatus:
    if value in _SUBMISSION_ALIASES:
        return _SUBMISSION_ALIASES[value]
    try:
        return SubmissionStatus(value)
    except ValueError:
        raise ValueError("status must be 'new' or 'reviewed'")


def next_submission_statuses(current: str) -> list[str]:
    """Legal next moves from `current` — drives buttons in the UI."""
    try:
        cur = parse_submission_status(current)
    except ValueError:
        return []
    return sorted(s.value for s in SUBMISSION_TRANSITIONS[cur])


def can_transition_submission(current: str, target: str) -> bool:
    """Same-state is always allowed (idempotent — safe to retry a bulk action)."""
    try:
        cur, tgt = parse_submission_status(current), parse_submission_status(target)
    except ValueError:
        return False
    return cur == tgt or tgt in SUBMISSION_TRANSITIONS[cur]


# ---------------------------------------------------------------------------
# 2. Form lifecycle  (derived from projects.is_archived + projects.expires_at)
# ---------------------------------------------------------------------------
class FormState(StrEnum):
    ACTIVE = "active"        # open and visible in the default list
    ARCHIVED = "archived"    # tucked away, still accepts responses
    EXPIRED = "expired"      # past its close date — public form is closed


def form_is_expired(expires_at: Optional[datetime], now: Optional[datetime] = None) -> bool:
    return bool(expires_at) and (now or datetime.utcnow()) > expires_at


def form_state(is_archived: bool, expires_at: Optional[datetime], now: Optional[datetime] = None) -> FormState:
    """Expired wins over archived: a closed form is closed no matter where
    it is filed. The two flags stay independent in the database."""
    if form_is_expired(expires_at, now):
        return FormState.EXPIRED
    return FormState.ARCHIVED if is_archived else FormState.ACTIVE


def form_accepts_responses(expires_at: Optional[datetime], now: Optional[datetime] = None) -> bool:
    # Archiving does NOT close a form — only the close date does.
    return not form_is_expired(expires_at, now)


# ---------------------------------------------------------------------------
# 3. Two-factor auth  (derived from users.totp_secret + users.totp_enabled)
# ---------------------------------------------------------------------------
class TwoFactorState(StrEnum):
    OFF = "off"
    SETUP_PENDING = "setup_pending"   # secret issued, no code confirmed yet
    ON = "on"


def two_factor_state(totp_secret: Optional[str], totp_enabled: bool) -> TwoFactorState:
    if totp_enabled:
        return TwoFactorState.ON
    return TwoFactorState.SETUP_PENDING if totp_secret else TwoFactorState.OFF


# Legal moves. `start_setup` from ON is deliberately absent: it used to
# silently switch 2FA off mid-setup. Turning it off now needs the password.
TWO_FACTOR_TRANSITIONS: Dict[str, Dict[TwoFactorState, TwoFactorState]] = {
    "start_setup": {TwoFactorState.OFF: TwoFactorState.SETUP_PENDING,
                    TwoFactorState.SETUP_PENDING: TwoFactorState.SETUP_PENDING},
    "confirm":     {TwoFactorState.SETUP_PENDING: TwoFactorState.ON},
    "disable":     {TwoFactorState.ON: TwoFactorState.OFF,
                    TwoFactorState.SETUP_PENDING: TwoFactorState.OFF,
                    TwoFactorState.OFF: TwoFactorState.OFF},
}


def two_factor_allows(action: str, state: TwoFactorState) -> bool:
    return state in TWO_FACTOR_TRANSITIONS.get(action, {})


# ---------------------------------------------------------------------------
# 4. User account  (users.role × users.is_active)
# ---------------------------------------------------------------------------
class Role(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    SUPERADMIN = "superadmin"


ROLE_VALUES = tuple(r.value for r in Role)
_ROLE_RANK = {Role.OWNER: 0, Role.ADMIN: 1, Role.SUPERADMIN: 2}


class AccountState(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


def account_state(is_active: bool) -> AccountState:
    return AccountState.ACTIVE if is_active else AccountState.DISABLED


def role_at_least(role: str, minimum: Role) -> bool:
    try:
        return _ROLE_RANK[Role(role)] >= _ROLE_RANK[minimum]
    except (ValueError, KeyError):
        return False


# ---------------------------------------------------------------------------
# 5. Single-use links  (password_reset_tokens.used + expires_at)
# ---------------------------------------------------------------------------
class LinkState(StrEnum):
    VALID = "valid"
    USED = "used"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


def reset_link_state(entry, now: Optional[datetime] = None) -> LinkState:
    if entry is None:
        return LinkState.UNKNOWN
    if entry.used:
        return LinkState.USED
    if entry.expires_at < (now or datetime.utcnow()):
        return LinkState.EXPIRED
    return LinkState.VALID


# 6. Response view-link  (submissions.edit_expires_at) — read-only, time-boxed
def view_link_state(edit_expires_at: Optional[datetime], now: Optional[datetime] = None) -> LinkState:
    if edit_expires_at is None:
        return LinkState.UNKNOWN
    return LinkState.EXPIRED if (now or datetime.utcnow()) > edit_expires_at else LinkState.VALID


# 7. Login session  (auth_sessions.expires_at)
class SessionState(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"


def session_state(expires_at: datetime, now: Optional[datetime] = None) -> SessionState:
    return SessionState.EXPIRED if expires_at < (now or datetime.utcnow()) else SessionState.ACTIVE


# ---------------------------------------------------------------------------
# 8. Login flow — a transient machine (never stored). Each failure has a
#    stable machine-readable code, sent as the `X-Error-Code` header so
#    clients don't have to match English text.
#
#    credentials ─► (locked out?) ─► (password ok?) ─► (account active?)
#                 ─► (2FA on? ─► code given? ─► code ok?) ─► session
# ---------------------------------------------------------------------------
class ErrorCode(StrEnum):
    LOCKED_OUT = "locked_out"
    BAD_CREDENTIALS = "bad_credentials"
    ACCOUNT_DISABLED = "account_disabled"
    TWO_FACTOR_REQUIRED = "2fa_required"
    TWO_FACTOR_REJECTED = "2fa_rejected"
    FORM_CLOSED = "form_closed"
    LINK_EXPIRED = "link_expired"
    LINK_INVALID = "link_invalid"
    TWO_FACTOR_ALREADY_ON = "2fa_already_on"
    WRONG_PASSWORD = "wrong_password"


ERROR_CODE_HEADER = "X-Error-Code"


def error_headers(code: ErrorCode) -> Dict[str, str]:
    return {ERROR_CODE_HEADER: code.value}


# ---------------------------------------------------------------------------
# Registry — one machine-readable map of everything above. Used by the docs
# test so the written documentation can never drift from the code.
# ---------------------------------------------------------------------------
STATE_MACHINES: Dict[str, Iterable[str]] = {
    "submission_status": SUBMISSION_STATUS_VALUES,
    "form_lifecycle": tuple(s.value for s in FormState),
    "two_factor": tuple(s.value for s in TwoFactorState),
    "account": tuple(s.value for s in AccountState),
    "role": ROLE_VALUES,
    "single_use_link": tuple(s.value for s in LinkState),
    "login_session": tuple(s.value for s in SessionState),
}
