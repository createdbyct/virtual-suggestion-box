"""
TOTP two-factor auth — implemented directly from RFC 6238 using only the
standard library (hmac/hashlib/base64/struct), matching the same
no-extra-native-dependency approach as password hashing in auth.py.

The QR code itself is rendered client-side from the otpauth:// URI this
module builds — no image library needed on the backend at all.
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time
import urllib.parse

TOTP_INTERVAL_SECONDS = 30
TOTP_DIGITS = 6
# How many 30s windows on either side of "now" to accept, to tolerate
# clock drift between the server and the person's phone.
TOTP_VALID_WINDOW = 1


def generate_totp_secret() -> str:
    """160-bit secret, base32-encoded — the standard size for TOTP."""
    return base64.b32encode(secrets.token_bytes(20)).decode("utf-8")


def _totp_code_at(secret: str, for_time: int) -> str:
    key = base64.b32decode(secret, casefold=True)
    counter = int(for_time // TOTP_INTERVAL_SECONDS)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    truncated = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    code = truncated % (10 ** TOTP_DIGITS)
    return str(code).zfill(TOTP_DIGITS)


def verify_totp_code(secret: str, code: str) -> bool:
    if not code or not code.isdigit():
        return False
    now = int(time.time())
    for step in range(-TOTP_VALID_WINDOW, TOTP_VALID_WINDOW + 1):
        candidate = _totp_code_at(secret, now + step * TOTP_INTERVAL_SECONDS)
        if hmac.compare_digest(candidate, code):
            return True
    return False


def build_otpauth_url(secret: str, account_name: str, issuer: str = "Virtual Suggestion Box") -> str:
    """Standard otpauth:// URI that any authenticator app (and the QR
    rendered from it) understands — Google Authenticator, Authy, etc."""
    label = urllib.parse.quote(f"{issuer}:{account_name}")
    params = urllib.parse.urlencode({
        "secret": secret,
        "issuer": issuer,
        "algorithm": "SHA1",
        "digits": TOTP_DIGITS,
        "period": TOTP_INTERVAL_SECONDS,
    })
    return f"otpauth://totp/{label}?{params}"


def generate_recovery_codes(count: int = 8) -> list[str]:
    """One-time-use 2FA backup codes, formatted like XXXX-XXXX for easy
    reading/typing. Caller is responsible for hashing before storage."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I ambiguity
    codes = []
    for _ in range(count):
        raw = "".join(secrets.choice(alphabet) for _ in range(8))
        codes.append(f"{raw[:4]}-{raw[4:]}")
    return codes
