"""M-15 Artifact Export/Download API（owner-safe，越权 404）。"""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.models import Record
from app.domain.repository import TaskRepository
from fastapi.testclient import TestClient


def _register(client: TestClient, email: str) -> dict:
    resp = client.post(
        "/api/auth/register",
        json={"email": email, "password": "password123", "confirm_password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _seed_task_with_passed(factory, user_id: int) -> int:
    session = factory()
    try:
        task = TaskRepository(session).create(
            user_id=user_id, title="上海政策", task_type="directed"
        )
        session.flush()
        session.add(
            Record(
                user_id=user_id,
                task_id=task.id,
                spec_version=1,
                partition="passed",
                payload={"标题": "记录A"},
            )
        )
        from app.domain.models import CollectionSpecVersion

        session.add(
            CollectionSpecVersion(
                user_id=user_id,
                task_id=task.id,
                version=1,
                spec_type="collection",
                schema_version="m06.1",
                payload={
                    "task_type": "directed",
                    "fields": [{"name": "标题", "type": "text", "required": True}],
                },
                confirmed_at=datetime.now(UTC),
                confirmed_by=user_id,
            )
        )
        session.commit()
        return task.id
    finally:
        session.close()


def test_export_and_download_owner_safe(client: dict) -> None:
    c, factory, storage = client["client"], client["factory"], client["storage"]
    alice = _register(c, "alice@example.com")["user"]
    task_id = _seed_task_with_passed(factory, alice["id"])

    resp = c.post(
        f"/api/tasks/{task_id}/artifacts/export",
        json={"export_type": "formal", "scope": "all", "filter": {}},
    )
    assert resp.status_code == 200, resp.text
    ref = resp.json()
    assert ref["row_count"] == 1

    # alice 下载成功（CSV bytes，BOM + UTF-8）。download_url 相对 /api 前缀。
    dl = c.get(f"/api{ref['download_url']}")
    assert dl.status_code == 200, dl.text
    assert dl.headers["content-type"].startswith("text/csv")
    assert dl.content.startswith(b"\xef\xbb\xbf")
    assert "标题" in dl.content.decode("utf-8-sig")

    # bob 注册会替换 TestClient 会话 cookie → 越权下载任务 404（不泄漏存在性）
    _register(c, "bob@example.com")
    bob_resp = c.get(f"/api{ref['download_url']}")
    assert bob_resp.status_code == 404
    assert len(storage.objects) == 1  # 单 blob
