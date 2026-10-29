"""
Database connection and session management.
SQLite via SQLAlchemy — same pattern as the home lab dashboard.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = "sqlite:///./suggestionbox.db"

# check_same_thread=False is needed because FastAPI can hand requests
# to different threads, and SQLite connections are thread-bound by default.
engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """FastAPI dependency — yields a session, closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create tables if they don't exist, then add any columns an existing
    database is missing. Call once on startup.

    The second step is what makes updates safe: models can gain columns and
    an existing database is upgraded in place (see app/migrate.py), instead
    of being wiped and restored from a backup."""
    # Import models here so they're registered on Base before create_all runs.
    from . import models  # noqa: F401
    from .migrate import add_missing_columns
    Base.metadata.create_all(bind=engine)
    add_missing_columns(engine)
    from .migrate import retire_done_status
    retire_done_status(engine)
