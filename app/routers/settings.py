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
    if "smtp_host" in payload.model_fields_set:
        settings.smtp_host = payload.smtp_host
    if payload.smtp_port is not None:
        settings.smtp_port = payload.smtp_port
    if "smtp_username" in payload.model_fields_set:
        settings.smtp_username = payload.smtp_username
    if payload.smtp_password:  # blank/omitted means "leave the existing one alone"
        settings.smtp_password = payload.smtp_password
    if "smtp_from_email" in payload.model_fields_set:
        settings.smtp_from_email = payload.smtp_from_email
    if "smtp_from_name" in payload.model_fields_set:
        settings.smtp_from_name = payload.smtp_from_name

    db.commit()
    db.refresh(settings)
    return SiteSettingsAdminOut(
        footer_text=settings.footer_text,
        footer_link_url=settings.footer_link_url,
        dark_mode_enabled=settings.dark_mode_enabled,
        notification_webhook_url=settings.notification_webhook_url,
        last_backup_at=settings.last_backup_at,
        smtp_host=settings.smtp_host,
        smtp_port=settings.smtp_port,
        smtp_username=settings.smtp_username,
        smtp_from_email=settings.smtp_from_email,
        smtp_from_name=settings.smtp_from_name,
        smtp_password_set=bool(settings.smtp_password),
    )
