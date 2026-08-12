"""
Auth helpers: password hashing (stdlib PBKDF2 — no bcrypt dependency to
install) and cookie-based sessions backed by the auth_sessions table.
"""
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Cookie, Depends, HTTPException
from sqlalchemy.orm import Session as DbSession

from .database import get_db
from .models import User, AuthSession

PBKDF2_ITERATIONS = 260_000
SESSION_COOKIE_NAME = "session_token"
SESSION_DURATION_DAYS = 14

# Off by default so local http:// dev keeps working out of the box. Set
# COOKIE_SECURE=true in the environment once this runs behind real HTTPS
# (e.g. behind a Cloudflare Tunnel) — see README.
SESSION_COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "false").lower() == "true"


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt, digest_hex = stored_hash.split("$")
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return hmac.compare_digest(digest.hex(), digest_hex)


def create_session(
    db: DbSession, user: User,
    ip_address: Optional[str] = None, user_agent: Optional[str] = None,
) -> tuple[str, datetime]:
    token = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(days=SESSION_DURATION_DAYS)
    db.add(AuthSession(
        token=token, user_id=user.id, expires_at=expires_at,
        ip_address=ip_address, user_agent=user_agent,
    ))
    db.commit()
    return token, expires_at


def destroy_session(db: DbSession, token: str) -> None:
    db.query(AuthSession).filter(AuthSession.token == token).delete()
    db.commit()


def destroy_all_sessions_for_user(db: DbSession, user_id: int, except_token: Optional[str] = None) -> None:
    """Used after a password reset/change — forces re-login everywhere
    except optionally the session doing the changing."""
    q = db.query(AuthSession).filter(AuthSession.user_id == user_id)
    if except_token:
        q = q.filter(AuthSession.token != except_token)
    q.delete()
    db.commit()


def get_current_user(
    session_token: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    db: DbSession = Depends(get_db),
) -> User:
    if not session_token:
        raise HTTPException(status_code=401, detail="Not logged in")

    session = db.query(AuthSession).filter(AuthSession.token == session_token).first()
    if not session or session.expires_at < datetime.utcnow():
        raise HTTPException(status_code=401, detail="Session expired — please log in again")

    user = db.query(User).filter(User.id == session.user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="Not logged in")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="This account has been disabled")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
