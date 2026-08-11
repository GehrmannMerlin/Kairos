"""M-10 crawling 测试共享 fixture（FakeStorage / 内存 SQLite ctx）。"""
from __future__ import annotations

import pytest
from app.auth.repository import UserRepository
from app.domain.repository import RunRepository, TaskRepository
from app.infra.db import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


class FakeStorage:
    """内存 ObjectStorage：记录 put 次数与已存在 key，便于断言 Blob 复用。"""

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}
        self.put_calls = 0

    async def ensure_bucket(self) -> None:
        pass

    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream"):
        self.put_calls += 1
        self._objects[key] = data
        return None

    async def get(self, key: str) -> bytes:
        return self._objects[key]

    async def exists(self, key: str) -> bool:
        return key in self._objects

    async def head(self, key: str):
        if key not in self._objects:
            return None
        return None


@pytest.fixture()
def storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture()
def ctx():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    user = UserRepository(db).create("crawling@example.com", "hash", None)
    task = TaskRepository(db).create(
        user_id=user.id, title="M-10 crawling", task_type="SPECIFIED_SOURCE"
    )
    run = RunRepository(db).create(
        user_id=user.id, task_id=task.id, spec_version=1, plan_version=1
    )
    yield {"db": db, "user": user, "task": task, "run": run}
    db.close()
