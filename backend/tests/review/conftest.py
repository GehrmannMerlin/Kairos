"""tests/review shared fixtures：SQLite 会话 + 两用户 + task + API TestClient。"""

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
        f"sqlite:///{tmp_path / 'review.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    yield session
    session.close()


@pytest.fixture()
def user_a(db: DbSession) -> User:
    return UserRepository(db).create("alice@example.com", "hash", None)


@pytest.fixture()
def user_b(db: DbSession) -> User:
    return UserRepository(db).create("bob@example.com", "hash", None)


@pytest.fixture()
def task_a(db: DbSession, user_a: User) -> Task:
    return TaskRepository(db).create(user_id=user_a.id, title="seed", task_type="directed")


@pytest.fixture()
def client(tmp_path) -> dict:
    """TestClient + sessionmaker，供 records API 测试（同 test_task_shell 模式）。"""
    from app.auth.deps import get_login_limiter
    from app.auth.rate_limit import InMemoryLoginLimiter
    from app.infra.deps import get_db
    from app.main import create_app
    from fastapi.testclient import TestClient

    engine = create_engine(
        f"sqlite:///{tmp_path / 'records_api.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def _override_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    limiter = InMemoryLoginLimiter(max_attempts=3, window_seconds=100)
    app = create_app()
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_login_limiter] = lambda: limiter
    with TestClient(app) as test_client:
        yield {"client": test_client, "factory": factory}
    app.dependency_overrides.clear()
