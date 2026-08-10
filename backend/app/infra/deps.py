"""Shared construction helpers used by both the API and the Worker.

Module-level cache keeps a single session factory / storage instance per
process, which is safe here because they are process-scoped.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.infra.db import create_session_factory
from app.infra.object_storage import MinioObjectStorage, ObjectStorage, create_object_storage


@lru_cache
def get_settings_cached() -> Settings:
    return get_settings()


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return create_session_factory(get_settings())


@lru_cache
def get_object_storage() -> MinioObjectStorage:
    return create_object_storage(get_settings())


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a scoped database session."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def storage() -> ObjectStorage:
    return get_object_storage()
