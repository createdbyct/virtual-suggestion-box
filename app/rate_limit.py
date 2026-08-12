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
from collections import defaultdict
from threading import Lock
from typing import Optional

_lock = Lock()

# key -> list of timestamps (seconds) within the current window
_hits: dict[str, list[float]] = defaultdict(list)

# key -> (failure_count, locked_until_timestamp_or_None)
_login_failures: dict = {}  # key -> (failure_count, locked_until_timestamp_or_None)


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


# ---- Login lockout ----

LOGIN_MAX_FAILURES = 5
LOGIN_LOCKOUT_SECONDS = 15 * 60


def is_locked_out(identifier_key: str) -> Optional[float]:
    """Returns seconds remaining if locked out, else None."""
    with _lock:
        entry = _login_failures.get(identifier_key)
        if not entry:
            return None
        _, locked_until = entry
        if locked_until is None:
            return None
        remaining = locked_until - time.time()
        if remaining <= 0:
            del _login_failures[identifier_key]
            return None
        return remaining


def record_login_failure(identifier_key: str) -> None:
    with _lock:
        count, _ = _login_failures.get(identifier_key, (0, None))
        count += 1
        locked_until = time.time() + LOGIN_LOCKOUT_SECONDS if count >= LOGIN_MAX_FAILURES else None
        _login_failures[identifier_key] = (count, locked_until)


def clear_login_failures(identifier_key: str) -> None:
    with _lock:
        _login_failures.pop(identifier_key, None)


def get_client_ip(request) -> str:
    """Prefers X-Forwarded-For (set by a reverse proxy / Cloudflare Tunnel)
    over the raw connection address, so rate limits key on the real client
    rather than the proxy's own IP."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
