#!/usr/bin/env python3
"""
Promote a user to admin or superadmin.

Usage:
    python3 promote_admin.py your@email.com
    python3 promote_admin.py your@email.com superadmin
    python3 promote_admin.py your_username admin
"""
import sys

from app.database import SessionLocal
from app.models import User

VALID_ROLES = ("admin", "superadmin")

if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        print("Usage: python3 promote_admin.py <email-or-username> [admin|superadmin]")
        sys.exit(1)

    identifier = sys.argv[1]
    role = sys.argv[2] if len(sys.argv) == 3 else "admin"

    if role not in VALID_ROLES:
        print(f"Role must be one of: {', '.join(VALID_ROLES)}")
        sys.exit(1)

    db = SessionLocal()
    user = (
        db.query(User)
        .filter((User.email == identifier) | (User.username == identifier))
        .first()
    )

    if not user:
        print(f"No account found with email or username '{identifier}'.")
        print("Make sure you've registered first, and double-check for typos.")
        sys.exit(1)

    if user.role == role:
        print(f"{user.email} is already {role} — nothing to do.")
        sys.exit(0)

    user.role = role
    db.commit()
    print(f"{user.email} (@{user.username}) is now {role}.")
