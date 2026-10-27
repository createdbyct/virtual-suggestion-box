"""
Full-database backup and restore — shared by the superadmin export/import
endpoints (app/routers/admin.py) and the standalone cron script
(backup_db.py), so none of them can drift out of sync.

WHY THIS IS MODEL-DRIVEN
The earlier version listed every table and column by hand. Each new
feature then silently fell out of the backup (survey questions and
answers, per-form settings, notification config, organization/
department...) and a wipe-and-restore deploy quietly destroyed that data.

Now export and import walk the ORM models themselves, so a new column on
an existing table is included automatically. A brand-new *table* must be
registered in BACKUP_MODELS (or deliberately listed in EXCLUDED_MODELS) —
and export refuses to run, loudly, if any model is in neither list. A
backup that fails is far better than one that quietly loses data.

See the export/import section of app/routers/admin.py for why this is
structured JSON rather than a raw .db copy.
"""
import datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import Session

from .database import Base
from .models import (
    AuthSession, Nominee, PasswordResetToken, Project, ProjectShare,
    RecoveryCode, SiteSettings, Submission, SurveyAnswer, SurveyQuestion,
    User, WatchedForm,
)

# 2: model-driven; adds survey questions/answers, watch settings, every
# per-form and per-user setting, and notification/SMTP config.
# Version-1 backups (the old hand-listed format) still import fine —
# anything they lack just takes the model's default.
EXPORT_VERSION = 2

# Parents before children — import inserts in this order, and deletes in
# reverse. (key in the JSON, model)
BACKUP_MODELS = [
    ("users", User),
    ("projects", Project),
    ("project_shares", ProjectShare),
    ("submissions", Submission),
    ("nominees", Nominee),
    ("survey_questions", SurveyQuestion),
    ("survey_answers", SurveyAnswer),
    ("recovery_codes", RecoveryCode),
    ("watched_forms", WatchedForm),
]

# Stored as a single row (id=1) under "site_settings" rather than a list.
SINGLETON_MODELS = [("site_settings", SiteSettings)]

# Deliberately not backed up: short-lived by design, and forcing a fresh
# login after a restore is expected, not a bug.
EXCLUDED_MODELS = [AuthSession, PasswordResetToken]


def _iso(dt):
    return dt.isoformat() if dt else None


def _column_keys(model):
    return [attr.key for attr in model.__mapper__.column_attrs]


def _datetime_keys(model):
    return {
        attr.key for attr in model.__mapper__.column_attrs
        if isinstance(attr.columns[0].type, DateTime)
    }


def _check_covers_all_models():
    covered = {m for _, m in BACKUP_MODELS} | {m for _, m in SINGLETON_MODELS} | set(EXCLUDED_MODELS)
    missing = [
        mapper.class_.__name__ for mapper in Base.registry.mappers
        if mapper.class_ not in covered
    ]
    if missing:
        raise RuntimeError(
            "Backup would be incomplete — these models are in neither BACKUP_MODELS "
            f"nor EXCLUDED_MODELS in app/backup.py: {', '.join(missing)}"
        )


def _serialize(obj, model) -> dict:
    dt_keys = _datetime_keys(model)
    row = {}
    for key in _column_keys(model):
        value = getattr(obj, key)
        row[key] = _iso(value) if key in dt_keys else value
    return row


def build_backup_dict(db: Session) -> dict:
    _check_covers_all_models()

    data = {
        "export_version": EXPORT_VERSION,
        "exported_at": _iso(datetime.datetime.utcnow()),
    }
    for key, model in BACKUP_MODELS:
        data[key] = [_serialize(obj, model) for obj in db.query(model).order_by(model.id).all()]
    for key, model in SINGLETON_MODELS:
        obj = db.query(model).filter(model.id == 1).first()
        data[key] = _serialize(obj, model) if obj else None
    return data


def _build_kwargs(row: dict, model) -> dict:
    """Only keys that are real columns — extra keys from a newer backup are
    ignored, missing keys fall back to the model's own defaults."""
    valid = set(_column_keys(model))
    dt_keys = _datetime_keys(model)
    kwargs = {}
    for key, value in row.items():
        if key not in valid:
            continue
        if key in dt_keys and isinstance(value, str):
            value = datetime.datetime.fromisoformat(value)
        kwargs[key] = value
    return kwargs


def restore_backup_dict(db: Session, payload: dict) -> dict:
    """Wipes everything, then restores from `payload`. Returns row counts."""
    if "users" not in payload:
        raise ValueError("This doesn't look like a valid backup file")
    version = payload.get("export_version", 1)
    if version > EXPORT_VERSION:
        raise ValueError(
            f"This backup was made by a newer version of the app (format {version}, "
            f"this one understands up to {EXPORT_VERSION}) — update the app before importing it"
        )

    # Wipe — sessions/tokens first (they point at users), then children
    # before parents. This also logs out whoever is doing the import, since
    # post-restore their session's user_id may not be the same account.
    for model in EXCLUDED_MODELS:
        db.query(model).delete()
    for _, model in reversed(BACKUP_MODELS):
        db.query(model).delete()
    for _, model in SINGLETON_MODELS:
        db.query(model).delete()
    db.commit()

    counts = {}
    for key, model in BACKUP_MODELS:
        rows = payload.get(key, [])
        for row in rows:
            db.add(model(**_build_kwargs(row, model)))
        db.commit()
        counts[key] = len(rows)

    for key, model in SINGLETON_MODELS:
        row = payload.get(key)
        if row:
            kwargs = _build_kwargs(row, model)
            kwargs["id"] = 1
            db.add(model(**kwargs))
            db.commit()

    return counts
