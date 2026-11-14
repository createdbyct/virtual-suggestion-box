"""
In-memory rate limiting and login lockout.

Deliberately not backed by Redis or similar — this app runs as a single
process (see README), so an in-process dict is sufficient and keeps the
zero-extra-infrastructure philosophy used everywhere else. If this ever
runs behind multiple worker processes, these limits would need to move to
a shared store (Redis, the DB itself, etc.) since each process would
otherwise track its own separate counts.
"""
import time
from datetime import datetime, timedelta
from collections import defaultdict
from threading import Lock
from typing import Optional

_lock = Lock()

# key -> list of timestamps (seconds) within the current window
_hits: dict[str, list[float]] = defaultdict(list)



def _prune(key: str, window_seconds: float) -> None:
    cutoff = time.time() - window_seconds
    _hits[key] = [t for t in _hits[key] if t > cutoff]


def check_rate_limit(key: str, max_hits: int, window_seconds: float) -> bool:
    """Records a hit for `key` and returns True if it's still within the
    limit, False if this hit should be rejected. Call only when you intend
    to count the attempt — don't call speculatively."""
    with _lock:
        _prune(key, window_seconds)
        if len(_hits[key]) >= max_hits:
            return False
        _hits[key].append(time.time())
        return True


# ---- Login lockout (persisted in the database) ----
#
# Stored in the login_attempts table so a restart can't be used to reset an
# attacker's guess count. Same behaviour as before: 5 failures lock that
# IP + identifier for 15 minutes. A counter that has gone quiet for longer
# than the lockout window starts over.

LOGIN_MAX_FAILURES = 5
LOGIN_LOCKOUT_SECONDS = 15 * 60


def _attempts_session():
    from .database import SessionLocal
    return SessionLocal()


def is_locked_out(identifier_key: str) -> Optional[float]:
    """Returns seconds remaining if locked out, else None."""
    from .models import LoginAttempt
    with _lock:
        db = _attempts_session()
        try:
            row = db.get(LoginAttempt, identifier_key)
            if not row or row.locked_until is None:
                return None
            remaining = (row.locked_until - datetime.utcnow()).total_seconds()
            if remaining <= 0:
                db.delete(row)
                db.commit()
                return None
            return remaining
        finally:
            db.close()


def record_login_failure(identifier_key: str) -> None:
    from .models import LoginAttempt
    with _lock:
        db = _attempts_session()
        try:
            now = datetime.utcnow()
            row = db.get(LoginAttempt, identifier_key)
            if row and (now - row.updated_at).total_seconds() > LOGIN_LOCKOUT_SECONDS and row.locked_until is None:
                row.failures = 0  # quiet for a full window — start over
            if not row:
                row = LoginAttempt(key=identifier_key, failures=0)
                db.add(row)
            row.failures = (row.failures or 0) + 1
            row.updated_at = now
            if row.failures >= LOGIN_MAX_FAILURES:
                row.locked_until = now + timedelta(seconds=LOGIN_LOCKOUT_SECONDS)
            db.commit()
        finally:
            db.close()


def clear_login_failures(identifier_key: str) -> None:
    from .models import LoginAttempt
    with _lock:
        db = _attempts_session()
        try:
            db.query(LoginAttempt).filter(LoginAttempt.key == identifier_key).delete()
            db.commit()
        finally:
            db.close()


def get_client_ip(request) -> str:
    """Prefers X-Forwarded-For (set by a reverse proxy / Cloudflare Tunnel)
    over the raw connection address, so rate limits key on the real client
    rather than the proxy's own IP."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
