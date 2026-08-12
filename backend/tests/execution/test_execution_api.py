"""M-14 Execution overview + Timeline Query API（D-055/D-063）。

验证（A-Lite 紧凑套件）：
1. stage aggregation 反映 Run/DomainEvent 真实事实。
2. timeline 稳定排序（occurred_at, id）。
3. category filter 只返回匹配事件。
4. task/run/node refs 串联。
5. redaction：payload 中的 secret 键不进入响应。
6. owner isolation：跨用户 → 404。
7. after_id 分页返回 next_cursor + has_more。
"""

from __future__ import annotations

from app.domain.models import Record, URLResource
from app.domain.repository import TaskRepository
from fastapi.testclient import TestClient
from tests.execution.conftest import _seed_events, _seed_run


def _register(client: TestClient, email: str) -> dict:
    resp = client.post(
        "/api/auth/register",
        json={"email": email, "password": "password123", "confirm_password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _seed_urls_and_records(factory, user_id: int, task_id: int) -> None:
    session = factory()
    try:
        session.add(
            URLResource(
                user_id=user_id,
                task_id=task_id,
                url="https://example.com/1",
                url_hash="h1",
                source_type="official_site",
                status="FETCHED",
            )
        )
        session.add(
            URLResource(
                user_id=user_id,
                task_id=task_id,
                url="https://example.com/2",
                url_hash="h2",
                source_type="official_site",
                status="FAILED",
            )
        )
        session.add(
            Record(
                user_id=user_id,
                task_id=task_id,
                spec_version=1,
                partition="passed",
                payload={"values": {"company": "A"}},
            )
        )
        session.add(
            Record(
                user_id=user_id,
                task_id=task_id,
                spec_version=1,
                partition="needs_review",
                review_type="missing_required",
                payload={"values": {}},
            )
        )
        session.commit()
    finally:
        session.close()


def test_execution_overview_stage_aggregation(client: dict) -> None:
    c, factory = client["client"], client["factory"]
    alice = _register(c, "alice@example.com")["user"]
    session = factory()
    try:
        task = TaskRepository(session).create(
            user_id=alice["id"], title="seed", task_type="directed"
        )
        session.flush()
        task_id = task.id
    finally:
        session.close()
    run_id = _seed_run(factory, alice["id"], task_id)
    _seed_events(factory, alice["id"], task_id, run_id)
    _seed_urls_and_records(factory, alice["id"], task_id)

    resp = c.get(f"/api/tasks/{task_id}/execution")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["run"]["run_id"] == run_id
    assert body["run"]["state"] == "RUNNING"
    stages = {s["key"]: s for s in body["stages"]}
    # task.* 事件（submit/plan_generated/complete）归入 goal_plan
    assert stages["goal_plan"]["event_count"] == 3
    assert stages["source_discovery"]["event_count"] == 1
    assert stages["fetch"]["event_count"] == 2
    assert stages["fetch"]["url_processed"] == 2  # 1 fetched + 1 failed
    assert stages["fetch"]["error_count"] == 1
    assert stages["extraction"]["event_count"] == 1
    assert stages["validation"]["record_count"] == 2
    # url / record 事实
    assert body["urls"]["fetched"] == 1
    assert body["urls"]["failed"] == 1
    assert body["records"] == {"passed": 1, "needs_review": 1}


def test_timeline_stable_ordering_and_refs(client: dict) -> None:
    c, factory = client["client"], client["factory"]
    alice = _register(c, "alice@example.com")["user"]
    session = factory()
    try:
        task = TaskRepository(session).create(
            user_id=alice["id"], title="seed", task_type="directed"
        )
        session.flush()
        task_id = task.id
    finally:
        session.close()
    run_id = _seed_run(factory, alice["id"], task_id)
    _seed_events(factory, alice["id"], task_id, run_id)

    resp = c.get(f"/api/tasks/{task_id}/execution/timeline")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    items = body["items"]
    assert len(items) == 8
    # 稳定排序：occurred_at 单调不减，同刻按 id 升序
    for a, b in zip(items, items[1:], strict=False):
        assert a["timestamp"] <= b["timestamp"]
    # refs 串联
    assert all(it["run_id"] == run_id for it in items)
    first = items[0]
    assert first["event_id"] > 0
    assert first["stage"] in {"goal_plan", "source_discovery", "fetch", "extraction", "validation"}


def test_timeline_category_filter(client: dict) -> None:
    c, factory = client["client"], client["factory"]
    alice = _register(c, "alice@example.com")["user"]
    session = factory()
    try:
        task = TaskRepository(session).create(
            user_id=alice["id"], title="seed", task_type="directed"
        )
        session.flush()
        task_id = task.id
    finally:
        session.close()
    run_id = _seed_run(factory, alice["id"], task_id)
    _seed_events(factory, alice["id"], task_id, run_id)

    err = c.get(f"/api/tasks/{task_id}/execution/timeline?category=error")
    err_types = {it["summary"] for it in err.json()["items"]}
    assert any("失败" in s for s in err_types)

    pause = c.get(f"/api/tasks/{task_id}/execution/timeline?category=pause_resume")
    assert pause.json()["items"] == []  # 无该类别事件 → 0，不伪造


def test_timeline_redaction_of_secret_like_payload(client: dict) -> None:
    c, factory = client["client"], client["factory"]
    alice = _register(c, "alice@example.com")["user"]
    session = factory()
    try:
        task = TaskRepository(session).create(
            user_id=alice["id"], title="seed", task_type="directed"
        )
        session.flush()
        task_id = task.id
    finally:
        session.close()
    run_id = _seed_run(factory, alice["id"], task_id)
    session = factory()
    try:
        from app.domain.models import DomainEvent

        session.add(
            DomainEvent(
                user_id=alice["id"],
                aggregate_type="task",
                aggregate_id=task_id,
                event_type="fetch.failed",
                aggregate_version=1,
                payload={
                    "status": "FAILED",
                    "error_code": "network_timeout",
                    "api_key": "sk-super-secret",
                    "cookie": "session=abc",
                },
                run_id=run_id,
            )
        )
        session.commit()
    finally:
        session.close()

    resp = c.get(f"/api/tasks/{task_id}/execution/timeline")
    raw = resp.text
    assert "sk-super-secret" not in raw
    assert "session=abc" not in raw
    assert "api_key" not in raw


def test_timeline_pagination_cursor(client: dict) -> None:
    c, factory = client["client"], client["factory"]
    alice = _register(c, "alice@example.com")["user"]
    session = factory()
    try:
        task = TaskRepository(session).create(
            user_id=alice["id"], title="seed", task_type="directed"
        )
        session.flush()
        task_id = task.id
    finally:
        session.close()
    run_id = _seed_run(factory, alice["id"], task_id)
    _seed_events(factory, alice["id"], task_id, run_id)

    page1 = c.get(f"/api/tasks/{task_id}/execution/timeline?limit=3")
    assert page1.status_code == 200
    body1 = page1.json()
    assert len(body1["items"]) == 3
    assert body1["has_more"] is True
    assert body1["next_cursor"] == body1["items"][-1]["event_id"]

    page2 = c.get(
        f"/api/tasks/{task_id}/execution/timeline?limit=3&after_id={body1['next_cursor']}"
    )
    body2 = page2.json()
    ids1 = {it["event_id"] for it in body1["items"]}
    ids2 = {it["event_id"] for it in body2["items"]}
    assert ids1.isdisjoint(ids2)


def test_execution_cross_user_404(client: dict) -> None:
    c, factory = client["client"], client["factory"]
    alice = _register(c, "alice@example.com")["user"]
    session = factory()
    try:
        task = TaskRepository(session).create(
            user_id=alice["id"], title="seed", task_type="directed"
        )
        session.flush()
        task_id = task.id
    finally:
        session.close()
    _register(c, "bob@example.com")

    resp = c.get(f"/api/tasks/{task_id}/execution")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "NOT_FOUND"
