"""M-14：Records Query `source_type` 通过 URLResource 关联真实解析（D-062 下钻准确）。

真实采集记录的 payload 不含 source_type（M-11 提取 payload 为 values/snapshot_id/url），
因此 M-13 的 payload 匹配对真实记录永远返回空。修复：source_type 优先解析
Record.url_resource_id → URLResource.source_type；payload.source_type 仅作为
fixture/旧数据兜底。参数名不变，仍是 M-13 contract。
"""

from __future__ import annotations

from app.domain.models import Record, URLResource
from app.domain.repository import TaskRepository
from fastapi.testclient import TestClient


def _register(client: TestClient, email: str) -> dict:
    resp = client.post(
        "/api/auth/register",
        json={"email": email, "password": "password123", "confirm_password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _seed(factory, user_id: int) -> int:
    session = factory()
    try:
        task = TaskRepository(session).create(user_id=user_id, title="seed", task_type="directed")
        session.flush()
        task_id = task.id
        url_official = URLResource(
            user_id=user_id,
            task_id=task_id,
            url="https://a.example.com/1",
            url_hash="h-official",
            source_type="official_site",
            status="FETCHED",
        )
        url_seed = URLResource(
            user_id=user_id,
            task_id=task_id,
            url="https://b.example.com/1",
            url_hash="h-seed",
            source_type="seed",
            status="FETCHED",
        )
        session.add(url_official)
        session.add(url_seed)
        session.flush()
        # 真实记录形态：payload 无 source_type，仅 values + 引用 URLResource
        session.add(
            Record(
                user_id=user_id,
                task_id=task_id,
                spec_version=1,
                partition="passed",
                url_resource_id=url_official.id,
                payload={"values": {"company": "A"}, "url": url_official.url},
            )
        )
        session.add(
            Record(
                user_id=user_id,
                task_id=task_id,
                spec_version=1,
                partition="passed",
                url_resource_id=url_seed.id,
                payload={"values": {"company": "B"}, "url": url_seed.url},
            )
        )
        session.commit()
        return task_id
    finally:
        session.close()


def test_source_type_resolves_via_url_resource(client: dict) -> None:
    c, factory = client["client"], client["factory"]
    alice = _register(c, "alice@example.com")["user"]
    task_id = _seed(factory, alice["id"])

    resp = c.get(f"/api/tasks/{task_id}/records?source_type=official_site")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["fields"]["company"] == "A"

    resp2 = c.get(f"/api/tasks/{task_id}/records?source_type=seed")
    assert resp2.json()["total"] == 1
    assert resp2.json()["items"][0]["fields"]["company"] == "B"


def test_source_type_falls_back_to_payload_for_fixture_records(client: dict) -> None:
    c, factory = client["client"], client["factory"]
    alice = _register(c, "alice@example.com")["user"]
    session = factory()
    try:
        task = TaskRepository(session).create(
            user_id=alice["id"], title="seed", task_type="directed"
        )
        session.flush()
        task_id = task.id
        # 无 URLResource 的旧/fixture 记录：payload.source_type 兜底
        session.add(
            Record(
                user_id=alice["id"],
                task_id=task_id,
                spec_version=1,
                partition="passed",
                payload={"company": "X", "source_type": "official_site"},
            )
        )
        session.commit()
    finally:
        session.close()

    resp = c.get(f"/api/tasks/{task_id}/records?source_type=official_site")
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 1
