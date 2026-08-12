#!/usr/bin/env python3
"""
Promote a user to admin.

Usage:
    python3 promote_admin.py your@email.com
    python3 promote_admin.py your_username
"""
import sys

from app.database import SessionLocal
from app.models import User

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 promote_admin.py <email-or-username>")
        sys.exit(1)

    identifier = sys.argv[1]
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

    if user.role == "admin":
        print(f"{user.email} is already an admin — nothing to do.")
        sys.exit(0)

    user.role = "admin"
    db.commit()
    print(f"{user.email} (@{user.username}) is now admin.")
