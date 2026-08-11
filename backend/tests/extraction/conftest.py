"""M-11 extraction test fixtures (in-memory SQLite + FakeStorage + snapshot seeding)."""
from __future__ import annotations

import hashlib
from urllib.parse import urlsplit

import pytest
from app.domain.repository import RunRepository, SpecVersionRepository, TaskRepository
from app.infra.db import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


class FakeStorage:
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
        return None


@pytest.fixture()
def storage() -> FakeStorage:
    return FakeStorage()


def collection_fields() -> list[dict]:
    return [
        {"name": "公司名", "type": "text", "required": True, "description": "企业名称"},
        {"name": "官网", "type": "url", "required": True, "description": "官方网站地址"},
        {"name": "电话", "type": "phone", "required": False, "description": "联系电话"},
        {"name": "邮箱", "type": "email", "required": False, "description": "联系邮箱"},
        {"name": "主营产品", "type": "text", "required": False, "description": "主营业务与产品"},
    ]


def spec_payload(fields: list[dict] | None = None) -> dict:
    return {
        "task_type": "SPECIFIED_SOURCE",
        "goal": "m11 extraction",
        "fields": fields or collection_fields(),
        "source_scope": {
            "mode": "SPECIFIED_SOURCE",
            "seed_urls": ["http://fixture.test/"],
            "source_hints": [],
        },
        "completion_conditions": [{"kind": "min_records", "target": 1}],
        "advanced_settings": {},
    }


@pytest.fixture()
def ctx():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    from app.auth.repository import UserRepository

    user = UserRepository(db).create("extraction@example.com", "hash", None)
    task = TaskRepository(db).create(
        user_id=user.id, title="M-11 extraction", task_type="SPECIFIED_SOURCE"
    )
    run = RunRepository(db).create(
        user_id=user.id, task_id=task.id, spec_version=1, plan_version=1
    )
    SpecVersionRepository(db).create(
        user_id=user.id,
        task_id=task.id,
        version=1,
        spec_type="collection",
        schema_version="m06.1",
        payload=spec_payload(),
    )
    yield {"db": db, "user": user, "task": task, "run": run}
    db.close()


def seed_snapshot(ctx, body: bytes, url: str = "http://fixture.test/") -> int:
    """Insert a PageSnapshot row + object in FakeStorage; returns the snapshot id."""
    from app.crawling.repository import PageSnapshotRepository

    db = ctx["db"]
    run = ctx["run"]
    digest = hashlib.sha256(body).hexdigest()
    key = f"snapshots/u{ctx['user'].id}/{digest}/http-abc.html"
    row = PageSnapshotRepository(db).create(
        user_id=ctx["user"].id,
        task_id=ctx["task"].id,
        run_id=run.id,
        url_resource_id=None,
        spec_version=1,
        content_hash=digest,
        storage_ref=key,
        mime_type="text/html",
        tool="http",
        tool_version="1.0",
        final_url=url,
        http_status=200,
        content_length=len(body),
        download_bytes=len(body),
        duration_ms=1,
        redirect_summary=None,
        escalation_evidence=None,
        snapshot_version=1,
        prior_snapshot_id=None,
        credential_ref=None,
        http_metadata=None,
    )
    return row.id


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()
