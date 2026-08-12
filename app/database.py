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
    """Create tables if they don't exist. Call once on startup."""
    # Import models here so they're registered on Base before create_all runs.
    from . import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
