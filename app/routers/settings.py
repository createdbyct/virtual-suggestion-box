"""
Site-wide settings — footer credit/link, whether dark mode is offered at
all. A singleton row (id=1), read by every page (so it has to be public),
written only by a superadmin.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..auth import require_superadmin
from ..database import get_db
from ..models import SiteSettings, User
from ..schemas import SiteSettingsOut, SiteSettingsAdminOut, SiteSettingsUpdate

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _get_or_create_settings(db: Session) -> SiteSettings:
    settings = db.query(SiteSettings).filter(SiteSettings.id == 1).first()
    if not settings:
        settings = SiteSettings(id=1)
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


@router.get("", response_model=SiteSettingsOut)
def get_settings(db: Session = Depends(get_db)):
    """Public — every page (including anonymous public forms) needs this
    to render the footer and decide whether to offer the dark mode toggle."""
    return _get_or_create_settings(db)


@router.patch("", response_model=SiteSettingsAdminOut)
def update_settings(
    payload: SiteSettingsUpdate,
    superadmin: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    settings = _get_or_create_settings(db)

    if "footer_text" in payload.model_fields_set:
        settings.footer_text = payload.footer_text
    if "footer_link_url" in payload.model_fields_set:
        settings.footer_link_url = payload.footer_link_url
    if payload.dark_mode_enabled is not None:
        settings.dark_mode_enabled = payload.dark_mode_enabled
    if "notification_webhook_url" in payload.model_fields_set:
        settings.notification_webhook_url = payload.notification_webhook_url

    db.commit()
    db.refresh(settings)
    return settings
