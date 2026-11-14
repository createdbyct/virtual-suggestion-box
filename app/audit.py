"""
Audit log helper. Call `record(...)` BEFORE the db.commit() that makes the
change, so the entry and the change succeed or fail together.

Keep `detail` short and free of secrets — never put a password, token or
response text in it.
"""
from typing import Optional

from sqlalchemy.orm import Session

from .models import AuditLog, User

# The vocabulary, so the admin viewer and docs agree with the code.
ACTIONS = (
    "share_added", "share_role_changed", "share_removed", "ownership_transferred",
    "form_deleted", "response_deleted",
    "user_updated", "user_deleted", "password_reset_by_admin", "password_changed",
    "backup_exported", "backup_restored", "backup_settings_changed", "backup_run_now",
)


def record(db: Session, actor: Optional[User], action: str, target_type: Optional[str] = None,
           target_id: Optional[int] = None, detail: Optional[str] = None) -> None:
    assert action in ACTIONS, f"unknown audit action: {action}"
    db.add(AuditLog(
        actor_id=actor.id if actor else None,
        actor_name=(f"{actor.name} (@{actor.username})" if actor else None),
        action=action, target_type=target_type, target_id=target_id,
        detail=(detail[:300] if detail else None),
    ))
