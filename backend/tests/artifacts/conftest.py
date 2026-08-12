"""tests/artifacts shared fixtures：SQLite + 两用户 + task + 异步 FakeObjectStorage。"""

from __future__ import annotations

import pytest
from app.auth.models import User
from app.auth.repository import UserRepository
from app.domain.models import Task
from app.domain.repository import TaskRepository
from app.infra.db import Base
from app.infra.object_storage import ObjectMetadata
from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker


class FakeObjectStorage:
    """M-15 测试用内存对象存储（全 async，与 MinIO adapter 协议一致）。"""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> ObjectMetadata:
        self.objects[key] = data
        return ObjectMetadata(
            key=key, size=len(data), content_type=content_type, etag=None, content_sha256=""
        )

    async def get(self, key: str) -> bytes:
        return self.objects[key]

    async def exists(self, key: str) -> bool:
        return key in self.objects

    async def head(self, key: str) -> ObjectMetadata | None:
        if key not in self.objects:
            return None
        return ObjectMetadata(
            key=key, size=len(self.objects[key]), content_type=None, etag=None, content_sha256=""
        )

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    async def ensure_bucket(self) -> None:
        return None


@pytest.fixture()
def db(tmp_path) -> DbSession:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'artifacts.db'}", connect_args={"check_same_thread": False}
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
def storage() -> FakeObjectStorage:
    return FakeObjectStorage()


@pytest.fixture()
def client(tmp_path) -> dict:
    """TestClient + sessionmaker + 假存储，供 artifacts API 测试（同 tests/review 模式）。"""
    from app.auth.deps import get_login_limiter
    from app.auth.rate_limit import InMemoryLoginLimiter
    from app.infra.deps import get_db, storage as storage_dep
    from app.main import create_app
    from fastapi.testclient import TestClient

    engine = create_engine(
        f"sqlite:///{tmp_path / 'artifacts_api.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def _override_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    fake_storage = FakeObjectStorage()
    limiter = InMemoryLoginLimiter(max_attempts=3, window_seconds=100)
    app = create_app()
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_login_limiter] = lambda: limiter
    app.dependency_overrides[storage_dep] = lambda: fake_storage
    with TestClient(app) as test_client:
        yield {"client": test_client, "factory": factory, "storage": fake_storage}
    app.dependency_overrides.clear()
