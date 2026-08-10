"""Shared fixtures for domain tests (SQLite)."""

from __future__ import annotations

import pytest
from app.auth.models import User
from app.auth.repository import UserRepository
from app.domain.models import Task
from app.domain.repository import TaskRepository
from app.infra.db import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def db(tmp_path) -> DbSession:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'domain.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    yield session
    session.close()


@pytest.fixture()
def user(db: DbSession) -> User:
    return UserRepository(db).create("alice@example.com", "hash", None)


@pytest.fixture()
def task(db: DbSession, user: User) -> Task:
    return TaskRepository(db).create(user_id=user.id, title="seed", task_type="directed")
