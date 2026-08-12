"""
Auth endpoints — register/login/logout, password reset, TOTP two-factor
auth with recovery codes, and session management.

Registration always creates an 'owner' account; the first admin has to be
promoted manually (see README) since there's no public path to admin.
"""
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..auth import (
    hash_password, verify_password, create_session, destroy_session,
    destroy_all_sessions_for_user, get_current_user, SESSION_COOKIE_NAME,
    SESSION_COOKIE_SECURE,
)
from ..database import get_db
from ..models import User, AuthSession, PasswordResetToken, RecoveryCode
from ..mailer import is_email_configured, send_email, build_public_url
from ..rate_limit import (
    check_rate_limit, is_locked_out, record_login_failure,
    clear_login_failures, get_client_ip,
)
from ..schemas import (
    RegisterIn, LoginIn, UserOut, UserSelfUpdateIn,
    TotpSetupOut, TotpConfirmIn, TotpConfirmOut, TotpDisableIn, RecoveryCodesOut,
    ForgotPasswordIn, ForgotPasswordOut, ResetPasswordIn, ResetPasswordTokenInfo,
    SessionOut,
)
from ..totp import generate_totp_secret, verify_totp_code, build_otpauth_url, generate_recovery_codes

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Cookie is httponly so JS can't read it (mitigates XSS token theft) and
# samesite=lax so it's still sent on top-level navigation to the dashboard.
COOKIE_KWARGS = dict(httponly=True, samesite="lax", path="/", secure=SESSION_COOKIE_SECURE)

RESET_TOKEN_VALID_HOURS = 1


def _set_session_cookie(response: Response, token: str, expires_at) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME, token,
        expires=expires_at.strftime("%a, %d %b %Y %H:%M:%S GMT"),
        **COOKIE_KWARGS,
    )


@router.post("/register", response_model=UserOut)
def register(payload: RegisterIn, response: Response, request: Request, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=400, detail="An account with this email already exists")
    if db.query(User).filter(User.username == payload.username).first():
        raise HTTPException(status_code=400, detail="That username is already taken")

    user = User(
        name=payload.name,
        username=payload.username,
        email=payload.email,
        password_hash=hash_password(payload.password),
        role="owner",
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token, expires_at = create_session(db, user, get_client_ip(request), request.headers.get("user-agent"))
    _set_session_cookie(response, token, expires_at)
    return user


@router.post("/login", response_model=UserOut)
def login(payload: LoginIn, response: Response, request: Request, db: Session = Depends(get_db)):
    ip = get_client_ip(request)
    lockout_key = f"{ip}:{payload.identifier.lower()}"

    remaining = is_locked_out(lockout_key)
    if remaining is not None:
        minutes = max(1, int(remaining // 60) + 1)
        raise HTTPException(status_code=429, detail=f"Too many failed attempts — try again in about {minutes} minute{'s' if minutes != 1 else ''}")

    user = (
        db.query(User)
        .filter(or_(User.email == payload.identifier, User.username == payload.identifier))
        .first()
    )
    if not user or not verify_password(payload.password, user.password_hash):
        record_login_failure(lockout_key)
        raise HTTPException(status_code=401, detail="Incorrect email/username or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="This account has been disabled")

    if user.totp_enabled:
        if not payload.totp_code:
            # Distinct detail text the frontend keys off of to show the
            # code field, rather than a generic error.
            raise HTTPException(status_code=401, detail="2FA code required")

        if not verify_totp_code(user.totp_secret, payload.totp_code):
            # Fall back to checking recovery codes before failing outright.
            used_recovery = False
            codes = db.query(RecoveryCode).filter(RecoveryCode.user_id == user.id, RecoveryCode.used == False).all()  # noqa: E712
            for rc in codes:
                if verify_password(payload.totp_code.strip(), rc.code_hash):
                    rc.used = True
                    db.commit()
                    used_recovery = True
                    break
            if not used_recovery:
                record_login_failure(lockout_key)
                raise HTTPException(status_code=401, detail="Incorrect 2FA code")

    clear_login_failures(lockout_key)
    token, expires_at = create_session(db, user, ip, request.headers.get("user-agent"))
    _set_session_cookie(response, token, expires_at)
    return user


@router.post("/logout")
def logout(
    response: Response,
    session_token: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    db: Session = Depends(get_db),
):
    if session_token:
        destroy_session(db, session_token)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"status": "logged out"}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.patch("/me", response_model=UserOut)
def update_me(payload: UserSelfUpdateIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    user.name = payload.name
    db.commit()
    db.refresh(user)
    return user


# ---- Password reset ----
# If SMTP is configured (see mailer.py), the reset link is emailed for
# real. If it isn't, this falls back to handing the link back directly in
# the response — functionally "click forgot password, land on the reset
# screen" for local/dev use, not pretending to be more secure than it is.
# An admin can also reset a user's password directly from the Users tab.

@router.post("/forgot-password", response_model=ForgotPasswordOut)
def forgot_password(payload: ForgotPasswordIn, request: Request, db: Session = Depends(get_db)):
    ip = get_client_ip(request)
    if not check_rate_limit(f"forgot:{ip}", max_hits=5, window_seconds=600):
        raise HTTPException(status_code=429, detail="Too many requests — try again shortly")

    user = (
        db.query(User)
        .filter(or_(User.email == payload.identifier, User.username == payload.identifier))
        .first()
    )
    if not user:
        raise HTTPException(status_code=404, detail="No account found with that email or username")

    token = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(hours=RESET_TOKEN_VALID_HOURS)
    db.add(PasswordResetToken(token=token, user_id=user.id, expires_at=expires_at))
    db.commit()

    reset_path = f"/reset-password/{token}"

    if is_email_configured():
        full_url = build_public_url(reset_path, request)
        sent = send_email(
            user.email,
            "Reset your password — Virtual Suggestion Box",
            f"Hi {user.name},\n\n"
            f"Someone (hopefully you) requested a password reset for your account.\n\n"
            f"Reset your password here:\n{full_url}\n\n"
            f"This link expires in {RESET_TOKEN_VALID_HOURS} hour and can only be used once.\n\n"
            f"If you didn't request this, you can safely ignore this email — "
            f"your password hasn't been changed.",
        )
        if sent:
            return ForgotPasswordOut(delivered_via="email", expires_at=expires_at)
        # SMTP is configured but the send actually failed (bad creds, host
        # down, etc.) — degrade to on-screen rather than leaving the
        # person with no way to reset at all.

    return ForgotPasswordOut(delivered_via="on_screen", reset_url=reset_path, expires_at=expires_at)


def _get_valid_reset_token(token: str, db: Session) -> PasswordResetToken:
    entry = db.query(PasswordResetToken).filter(PasswordResetToken.token == token).first()
    if not entry or entry.used or entry.expires_at < datetime.utcnow():
        raise HTTPException(status_code=404, detail="This reset link is invalid or has expired")
    return entry


@router.get("/reset-password/{token}", response_model=ResetPasswordTokenInfo)
def check_reset_token(token: str, db: Session = Depends(get_db)):
    try:
        entry = _get_valid_reset_token(token, db)
    except HTTPException:
        return ResetPasswordTokenInfo(valid=False)
    user = db.query(User).filter(User.id == entry.user_id).first()
    return ResetPasswordTokenInfo(valid=True, username=user.username if user else None)


@router.post("/reset-password/{token}")
def reset_password(token: str, payload: ResetPasswordIn, db: Session = Depends(get_db)):
    entry = _get_valid_reset_token(token, db)
    user = db.query(User).filter(User.id == entry.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Account no longer exists")

    user.password_hash = hash_password(payload.new_password)
    entry.used = True
    destroy_all_sessions_for_user(db, user.id)  # force re-login everywhere, including any active attacker session
    db.commit()
    return {"status": "password reset"}


# ---- Two-factor auth ----

@router.post("/2fa/setup", response_model=TotpSetupOut)
def setup_2fa(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Generates a new secret and returns it plus a QR-ready otpauth:// URL.
    Not enabled yet — a confirmed code via /2fa/confirm turns it on."""
    secret = generate_totp_secret()
    user.totp_secret = secret
    user.totp_enabled = False
    db.commit()
    return TotpSetupOut(secret=secret, otpauth_url=build_otpauth_url(secret, user.email))


@router.post("/2fa/confirm", response_model=TotpConfirmOut)
def confirm_2fa(payload: TotpConfirmIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not user.totp_secret:
        raise HTTPException(status_code=400, detail="Start setup first")
    if not verify_totp_code(user.totp_secret, payload.code):
        raise HTTPException(status_code=400, detail="Incorrect code — check your authenticator app and try again")

    user.totp_enabled = True

    # Fresh recovery codes every time 2FA is (re-)confirmed — old ones from
    # a previous setup, if any, are invalidated so they can't be reused.
    db.query(RecoveryCode).filter(RecoveryCode.user_id == user.id).delete()
    plaintext_codes = generate_recovery_codes()
    for code in plaintext_codes:
        db.add(RecoveryCode(user_id=user.id, code_hash=hash_password(code)))

    db.commit()
    db.refresh(user)
    return TotpConfirmOut(user=user, recovery_codes=plaintext_codes)


@router.post("/2fa/disable", response_model=UserOut)
def disable_2fa(payload: TotpDisableIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect password")

    user.totp_enabled = False
    user.totp_secret = None
    db.query(RecoveryCode).filter(RecoveryCode.user_id == user.id).delete()
    db.commit()
    db.refresh(user)
    return user


@router.post("/2fa/recovery-codes/regenerate", response_model=RecoveryCodesOut)
def regenerate_recovery_codes(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not user.totp_enabled:
        raise HTTPException(status_code=400, detail="2FA isn't enabled")

    db.query(RecoveryCode).filter(RecoveryCode.user_id == user.id).delete()
    plaintext_codes = generate_recovery_codes()
    for code in plaintext_codes:
        db.add(RecoveryCode(user_id=user.id, code_hash=hash_password(code)))
    db.commit()
    return RecoveryCodesOut(codes=plaintext_codes)


@router.get("/2fa/recovery-codes/remaining")
def count_remaining_recovery_codes(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    count = db.query(RecoveryCode).filter(RecoveryCode.user_id == user.id, RecoveryCode.used == False).count()  # noqa: E712
    return {"remaining": count}


# ---- Sessions ----

@router.get("/sessions", response_model=list[SessionOut])
def list_sessions(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    session_token: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
):
    sessions = (
        db.query(AuthSession)
        .filter(AuthSession.user_id == user.id)
        .order_by(AuthSession.created_at.desc())
        .all()
    )
    result = []
    for s in sessions:
        out = SessionOut.model_validate(s)
        out.is_current = (s.token == session_token)
        result.append(out)
    return result


@router.delete("/sessions/{session_id}")
def revoke_session(session_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    session = db.query(AuthSession).filter(AuthSession.id == session_id, AuthSession.user_id == user.id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    db.delete(session)
    db.commit()
    return {"status": "revoked"}


@router.post("/sessions/revoke-others")
def revoke_other_sessions(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    session_token: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
):
    destroy_all_sessions_for_user(db, user.id, except_token=session_token)
    return {"status": "revoked"}
