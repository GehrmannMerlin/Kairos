"""SQLAlchemy engine / session management.

M-01 keeps a single synchronous engine. Long-running or async-heavy paths will
move to their own repositories in later modules; the session factory stays the
single extension point they rely on.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import Settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def create_session_factory(settings: Settings) -> sessionmaker[Session]:
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        # Only meaningful for SQLite tests; harmless for PostgreSQL.
        connect_args={"connect_timeout": 5}
        if settings.database_url.startswith("postgresql")
        else {},
    )
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def ping(session: Session) -> bool:
    """Cheap liveness probe against the database."""
    from sqlalchemy import text

    session.execute(text("SELECT 1"))
    return True
