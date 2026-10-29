"""Database setup (SQLAlchemy 2.0).

SQLite is used for development and the demo (deviation from the SAD, which planned PostgreSQL).
Set SPMS_DATABASE_URL to point at another database, e.g. PostgreSQL, without code changes.
"""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = os.getenv("SPMS_DATABASE_URL", "sqlite:///./spms.db")

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """Base class for all database models."""


def get_db():
    """FastAPI dependency that yields a database session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables for every model that has been imported."""
    Base.metadata.create_all(bind=engine)
