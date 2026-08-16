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

import gc
from datetime import UTC, datetime, timedelta

from app.auth.repository import UserRepository
from app.domain.models import (
    DomainEvent,
    NodeAttempt,
    NodeRun,
    PlanVersion,
    Record,
    Run,
    URLResource,
)
from app.domain.repository import TaskRepository
from app.execution.repository import ExecutionRepository
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
    assert body["current_node"] is None
    assert body["last_successful_node"] is None
    assert body["legacy_execution_facts"] is True


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


def test_execution_snapshot_uses_node_facts(client: dict) -> None:
    c, factory = client["client"], client["factory"]
    alice = _register(c, "snapshot@example.com")["user"]
    session = factory()
    started_at = datetime(2026, 8, 16, 3, 4, 5, tzinfo=UTC)
    try:
        task = TaskRepository(session).create(
            user_id=alice["id"], title="snapshot", task_type="directed"
        )
        session.flush()
        task_id = task.id
        plan = PlanVersion(
            user_id=alice["id"],
            task_id=task_id,
            spec_version=1,
            version=1,
            validation_status="VALID",
            plan_fingerprint="snapshot-plan",
            payload={
                "graph": {
                    "nodes": [
                        {"node_id": "n1", "node_type": "source_search", "label": "Search"},
                        {"node_id": "n2", "node_type": "extract", "label": "Extract"},
                        {"node_id": "n3", "node_type": "fetch", "label": "Fetch"},
                        {"node_id": "n4", "node_type": "validate", "label": "Validate"},
                    ]
                }
            },
        )
        session.add(plan)
        session.commit()
        run_id = _seed_run(factory, alice["id"], task_id)
        session.add(
            PlanVersion(
                user_id=alice["id"],
                task_id=task_id,
                spec_version=2,
                version=2,
                validation_status="VALID",
                plan_fingerprint="newer-plan",
                payload={
                    "graph": {
                        "nodes": [{"node_id": "n3", "node_type": "fetch", "label": "Wrong run"}]
                    }
                },
            )
        )
        completed = NodeRun(
            user_id=alice["id"],
            task_id=task_id,
            run_id=run_id,
            node_id="n2",
            node_type="extract",
            state="SUCCEEDED",
            position=2,
            started_at=started_at - timedelta(minutes=2),
            finished_at=started_at - timedelta(minutes=1),
        )
        older_current = NodeRun(
            user_id=alice["id"],
            task_id=task_id,
            run_id=run_id,
            node_id="n1",
            node_type="source_search",
            state="RUNNING",
            position=9,
            started_at=started_at - timedelta(minutes=5),
        )
        current = NodeRun(
            user_id=alice["id"],
            task_id=task_id,
            run_id=run_id,
            node_id="n3",
            node_type="fetch",
            state="BLOCKED",
            position=1,
            started_at=started_at,
        )
        latest_completed = NodeRun(
            user_id=alice["id"],
            task_id=task_id,
            run_id=run_id,
            node_id="n4",
            node_type="validate",
            state="SUCCEEDED",
            position=4,
            started_at=started_at - timedelta(seconds=50),
            finished_at=started_at - timedelta(seconds=10),
        )
        session.add_all([older_current, completed, current, latest_completed])
        session.flush()
        decoy_owner = UserRepository(session).create("snapshot-decoy@example.com", "hash", None)
        session.add_all(
            [
                NodeAttempt(
                    user_id=alice["id"],
                    node_run_id=older_current.id,
                    attempt=1,
                    status="RUNNING",
                    error_code="STALE_POSITION_REASON",
                    started_at=older_current.started_at,
                ),
                NodeAttempt(
                    user_id=alice["id"],
                    node_run_id=completed.id,
                    attempt=1,
                    status="SUCCEEDED",
                    started_at=completed.started_at,
                    finished_at=completed.finished_at,
                ),
                NodeAttempt(
                    user_id=alice["id"],
                    node_run_id=current.id,
                    attempt=1,
                    status="FAILED",
                    error_code="OLDER_FAILURE",
                    started_at=started_at - timedelta(seconds=30),
                    finished_at=started_at - timedelta(seconds=20),
                ),
                NodeAttempt(
                    user_id=alice["id"],
                    node_run_id=current.id,
                    attempt=2,
                    status="BLOCKED",
                    error_code="RESOURCE_UNAVAILABLE",
                    error_summary="resource capacity is unavailable",
                    started_at=started_at,
                ),
                NodeAttempt(
                    user_id=decoy_owner.id,
                    node_run_id=current.id,
                    attempt=99,
                    status="FAILED",
                    error_code="CROSS_OWNER_SECRET",
                    error_summary="must not select cross-owner attempt",
                    started_at=started_at + timedelta(days=1),
                    finished_at=started_at + timedelta(days=1, seconds=1),
                ),
                NodeAttempt(
                    user_id=alice["id"],
                    node_run_id=latest_completed.id,
                    attempt=1,
                    status="FAILED",
                    started_at=started_at - timedelta(seconds=50),
                    finished_at=started_at - timedelta(seconds=40),
                ),
                NodeAttempt(
                    user_id=alice["id"],
                    node_run_id=latest_completed.id,
                    attempt=2,
                    status="SUCCEEDED",
                    started_at=started_at - timedelta(seconds=30),
                    finished_at=latest_completed.finished_at,
                ),
            ]
        )
        for index in range(4):
            session.add(
                URLResource(
                    user_id=alice["id"],
                    task_id=task_id,
                    run_id=run_id,
                    url=f"https://example.com/{index}",
                    url_hash=f"snapshot-{index}",
                    source_type="official_site",
                    status="FETCHED",
                )
            )
        session.add_all(
            [
                Record(
                    user_id=alice["id"],
                    task_id=task_id,
                    run_id=run_id,
                    spec_version=1,
                    partition="needs_review",
                    payload={"values": {"name": "unvalidated"}},
                ),
                Record(
                    user_id=alice["id"],
                    task_id=task_id,
                    run_id=run_id,
                    spec_version=1,
                    partition="passed",
                    payload={"values": {"name": "validated"}},
                    validated_at=started_at - timedelta(seconds=30),
                ),
            ]
        )
        session.add(
            DomainEvent(
                user_id=alice["id"],
                aggregate_type="task",
                aggregate_id=task_id,
                event_type="run.failed",
                aggregate_version=1,
                payload={"error_code": "OLD_RUN_FAILURE"},
                run_id=None,
                occurred_at=started_at - timedelta(days=1),
            )
        )
        session.add(
            DomainEvent(
                user_id=alice["id"],
                aggregate_type="task",
                aggregate_id=task_id,
                event_type="run.node_started",
                aggregate_version=1,
                payload={"node_id": "n3", "node_type": "fetch", "state": "RUNNING"},
                run_id=run_id,
                node_run_id=current.id,
                occurred_at=started_at - timedelta(seconds=1),
            )
        )
        session.add(
            DomainEvent(
                user_id=alice["id"],
                aggregate_type="task",
                aggregate_id=task_id,
                event_type="run.failed",
                aggregate_version=2,
                payload={"outcome_code": "CURRENT_RUN_LIMIT"},
                run_id=run_id,
                occurred_at=started_at - timedelta(milliseconds=500),
            )
        )
        session.commit()
        last_event_id = session.query(DomainEvent.id).order_by(DomainEvent.id.desc()).first()[0]
    finally:
        session.close()

    resp = c.get(f"/api/tasks/{task_id}/execution")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["current_node"] == {
        "node_id": "n3",
        "node_type": "fetch",
        "label": "Fetch",
        "state": "BLOCKED",
        "attempt": 2,
        "safe_message": "resource capacity is unavailable",
    }
    assert body["last_successful_node"] == {
        "node_id": "n4",
        "node_type": "validate",
        "label": "Validate",
        "state": "SUCCEEDED",
        "attempt": 2,
        "safe_message": None,
    }
    assert body["last_event_id"] == last_event_id
    response_activity_at = datetime.fromisoformat(body["last_activity_at"])
    if response_activity_at.tzinfo is None:
        response_activity_at = response_activity_at.replace(tzinfo=UTC)
    assert response_activity_at == started_at
    assert body["counts"]["discovered_pages"] == 4
    assert body["counts"]["fetched_pages"] == 4
    assert body["counts"]["extracted_records"] == 2
    assert body["counts"]["validated_records"] == 1
    assert body["waiting_reason_code"] == "RESOURCE_UNAVAILABLE"
    assert body["outcome_code"] == "CURRENT_RUN_LIMIT"
    assert body["legacy_execution_facts"] is False


def test_execution_counts_use_latest_run_while_legacy_totals_remain_task_wide(
    client: dict,
) -> None:
    c, factory = client["client"], client["factory"]
    alice = _register(c, "run-counts@example.com")["user"]
    session = factory()
    try:
        task = TaskRepository(session).create(
            user_id=alice["id"], title="counts", task_type="directed"
        )
        session.flush()
        old_run = Run(
            user_id=alice["id"], task_id=task.id, spec_version=1, plan_version=1, state="COMPLETED"
        )
        current_run = Run(
            user_id=alice["id"], task_id=task.id, spec_version=2, plan_version=2, state="RUNNING"
        )
        session.add_all([old_run, current_run])
        session.flush()
        session.add(
            NodeRun(
                user_id=alice["id"],
                task_id=task.id,
                run_id=current_run.id,
                node_id="current-source",
                node_type="source_search",
                state="SUCCEEDED",
                position=1,
            )
        )
        for index, status in enumerate(("FETCHED", "FETCHED", "FAILED")):
            session.add(
                URLResource(
                    user_id=alice["id"],
                    task_id=task.id,
                    run_id=old_run.id,
                    spec_version=1,
                    url=f"https://old.example/{index}",
                    url_hash=f"old-{index}",
                    status=status,
                )
            )
        # The frontier identity is task-wide, so a later run may reuse rows that
        # remain owned by the run that first discovered them.
        for index, status in enumerate(("FETCHED", "DISCOVERED")):
            session.add(
                URLResource(
                    user_id=alice["id"],
                    task_id=task.id,
                    run_id=old_run.id,
                    spec_version=1,
                    url=f"https://current.example/{index}",
                    url_hash=f"current-{index}",
                    status=status,
                )
            )
        session.add_all(
            [
                DomainEvent(
                    user_id=alice["id"],
                    aggregate_type="task",
                    aggregate_id=task.id,
                    event_type="discovery.candidates_found",
                    aggregate_version=1,
                    payload={"candidates": 99},
                    run_id=old_run.id,
                ),
                DomainEvent(
                    user_id=alice["id"],
                    aggregate_type="task",
                    aggregate_id=task.id,
                    event_type="fetch.completed",
                    aggregate_version=2,
                    payload={"url_hash": "old-0"},
                    run_id=old_run.id,
                ),
                DomainEvent(
                    user_id=alice["id"],
                    aggregate_type="task",
                    aggregate_id=task.id,
                    event_type="discovery.candidates_found",
                    aggregate_version=3,
                    payload={"candidates": 2},
                    run_id=current_run.id,
                ),
                DomainEvent(
                    user_id=alice["id"],
                    aggregate_type="task",
                    aggregate_id=task.id,
                    event_type="fetch.completed",
                    aggregate_version=4,
                    payload={"url_hash": "current-0"},
                    run_id=current_run.id,
                ),
                # Retried aggregate discovery events overlap. Unique current-run
                # URL identities, not summed aggregates, define the typed count.
                DomainEvent(
                    user_id=alice["id"],
                    aggregate_type="task",
                    aggregate_id=task.id,
                    event_type="discovery.candidates_found",
                    aggregate_version=5,
                    payload={"candidates": 2},
                    run_id=current_run.id,
                ),
                DomainEvent(
                    user_id=alice["id"],
                    aggregate_type="task",
                    aggregate_id=task.id,
                    event_type="fetch.completed",
                    aggregate_version=6,
                    payload={"url_hash": "current-1"},
                    run_id=current_run.id,
                ),
            ]
        )
        session.add_all(
            [
                Record(
                    user_id=alice["id"],
                    task_id=task.id,
                    run_id=old_run.id,
                    spec_version=1,
                    partition="passed",
                    payload={},
                    validated_at=datetime(2026, 8, 16, tzinfo=UTC),
                )
                for _ in range(3)
            ]
        )
        session.add_all(
            [
                Record(
                    user_id=alice["id"],
                    task_id=task.id,
                    run_id=current_run.id,
                    spec_version=2,
                    partition="passed",
                    payload={},
                    validated_at=datetime(2026, 8, 16, tzinfo=UTC),
                ),
                Record(
                    user_id=alice["id"],
                    task_id=task.id,
                    run_id=current_run.id,
                    spec_version=2,
                    partition="needs_review",
                    payload={},
                ),
            ]
        )
        session.commit()
        task_id = task.id
        current_run_id = current_run.id
    finally:
        session.close()

    response = c.get(f"/api/tasks/{task_id}/execution")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["run"]["run_id"] == current_run_id
    assert body["urls"] == {"discovered": 5, "fetched": 3, "failed": 1, "pending": 1}
    assert body["records"] == {"needs_review": 1, "passed": 4}
    assert body["counts"] == {
        "discovered_pages": 2,
        "fetched_pages": 2,
        "extracted_records": 2,
        "validated_records": 1,
    }
    stages = {stage["key"]: stage for stage in body["stages"]}
    assert stages["source_discovery"]["event_count"] == 2
    assert stages["source_discovery"]["url_processed"] == 2
    assert stages["fetch"]["event_count"] == 2
    assert stages["fetch"]["url_processed"] == 2
    assert stages["extraction"]["record_count"] == 2
    assert stages["validation"]["record_count"] == 2


def test_execution_url_counts_union_persisted_and_reused_identities(client: dict) -> None:
    c, factory = client["client"], client["factory"]
    alice = _register(c, "url-identity-union@example.com")["user"]
    session = factory()
    try:
        task = TaskRepository(session).create(
            user_id=alice["id"], title="url identity union", task_type="directed"
        )
        session.flush()
        old_run = Run(
            user_id=alice["id"], task_id=task.id, spec_version=1, plan_version=1, state="COMPLETED"
        )
        current_run = Run(
            user_id=alice["id"], task_id=task.id, spec_version=2, plan_version=2, state="RUNNING"
        )
        session.add_all([old_run, current_run])
        session.flush()
        session.add(
            NodeRun(
                user_id=alice["id"],
                task_id=task.id,
                run_id=current_run.id,
                node_id="fetch-current",
                node_type="fetch",
                state="RUNNING",
                position=1,
            )
        )
        session.add_all(
            [
                URLResource(
                    user_id=alice["id"],
                    task_id=task.id,
                    run_id=current_run.id,
                    spec_version=2,
                    url="https://union.example/a",
                    url_hash="a",
                    status="FAILED",
                ),
                URLResource(
                    user_id=alice["id"],
                    task_id=task.id,
                    run_id=current_run.id,
                    spec_version=2,
                    url="https://union.example/b",
                    url_hash="b",
                    status="DISCOVERED",
                ),
                # The task-wide frontier keeps reused c owned by its first run.
                URLResource(
                    user_id=alice["id"],
                    task_id=task.id,
                    run_id=old_run.id,
                    spec_version=1,
                    url="https://union.example/c",
                    url_hash="c",
                    status="FETCHED",
                ),
            ]
        )
        session.add_all(
            [
                DomainEvent(
                    user_id=alice["id"],
                    aggregate_type="task",
                    aggregate_id=task.id,
                    event_type="fetch.completed",
                    aggregate_version=1,
                    payload={"node_id": "fetch-current", "url_hash": "c"},
                    run_id=current_run.id,
                ),
                # A later current-run completion supersedes persisted failure a.
                DomainEvent(
                    user_id=alice["id"],
                    aggregate_type="task",
                    aggregate_id=task.id,
                    event_type="fetch.completed",
                    aggregate_version=2,
                    payload={"node_id": "fetch-current", "url_hash": "a"},
                    run_id=current_run.id,
                ),
            ]
        )
        session.commit()
        task_id = task.id
    finally:
        session.close()

    response = c.get(f"/api/tasks/{task_id}/execution")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["counts"]["discovered_pages"] == 3
    assert body["counts"]["fetched_pages"] == 2
    stages = {stage["key"]: stage for stage in body["stages"]}
    assert stages["source_discovery"]["url_processed"] == 3
    assert stages["fetch"]["url_processed"] == 2


def test_execution_read_endpoints_reduce_pages_without_retaining_full_history(
    client: dict, monkeypatch
) -> None:
    c, factory = client["client"], client["factory"]
    alice = _register(c, "bounded-read-facts@example.com")["user"]
    session = factory()
    try:
        task = TaskRepository(session).create(
            user_id=alice["id"], title="bounded read facts", task_type="directed"
        )
        session.flush()
        plan = PlanVersion(
            user_id=alice["id"],
            task_id=task.id,
            spec_version=1,
            version=1,
            validation_status="VALID",
            plan_fingerprint="bounded-read",
            payload={
                "graph": {
                    "nodes": [
                        {
                            "node_id": "n-fetch",
                            "node_type": "fetch",
                            "definition_version": "1.0.0",
                            "depends_on": [],
                        }
                    ],
                    "edges": [],
                }
            },
        )
        run = Run(
            user_id=alice["id"],
            task_id=task.id,
            spec_version=1,
            plan_version=1,
            state="RUNNING",
        )
        session.add_all([plan, run])
        session.flush()
        session.add(
            NodeRun(
                user_id=alice["id"],
                task_id=task.id,
                run_id=run.id,
                node_id="n-fetch",
                node_type="fetch",
                state="RUNNING",
                position=1,
            )
        )
        session.add_all(
            [
                DomainEvent(
                    user_id=alice["id"],
                    aggregate_type="task",
                    aggregate_id=task.id,
                    event_type="run.node_progress",
                    aggregate_version=index,
                    payload={
                        "node_id": "n-fetch",
                        "node_type": "fetch",
                        "attempt": 1,
                        "state": "RUNNING",
                        "tool": f"tool-{index}",
                    },
                    run_id=run.id,
                )
                for index in range(30)
            ]
        )
        session.commit()
        task_id = task.id
    finally:
        session.close()

    original_events_after = ExecutionRepository.events_after

    class TrackedEvent:
        live = 0
        peak = 0

        def __init__(self, event: DomainEvent) -> None:
            self.id = event.id
            self.event_type = event.event_type
            self.payload = event.payload
            self.run_id = event.run_id
            self.node_run_id = event.node_run_id
            self.occurred_at = event.occurred_at
            type(self).live += 1
            type(self).peak = max(type(self).peak, type(self).live)

        def __del__(self) -> None:
            type(self).live -= 1

    def tracked_three_event_pages(
        self,
        *,
        user_id: int,
        task_id: int,
        after_id: int,
        limit: int,
        through_id: int | None = None,
    ):
        page = original_events_after(
            self,
            user_id=user_id,
            task_id=task_id,
            after_id=after_id,
            limit=min(limit, 3),
            through_id=through_id,
        )
        return [TrackedEvent(event) for event in page]

    monkeypatch.setattr(ExecutionRepository, "events_after", tracked_three_event_pages)

    for path in (
        f"/api/tasks/{task_id}/execution",
        f"/api/tasks/{task_id}/execution/dag",
        f"/api/tasks/{task_id}/execution/nodes/n-fetch",
    ):
        TrackedEvent.peak = 0
        response = c.get(path)
        assert response.status_code == 200, response.text
        gc.collect()
        assert TrackedEvent.live == 0
        assert TrackedEvent.peak <= 6


def test_execution_overview_pages_to_newest_event_facts(client: dict, monkeypatch) -> None:
    c, factory = client["client"], client["factory"]
    alice = _register(c, "paged-overview@example.com")["user"]
    session = factory()
    try:
        task = TaskRepository(session).create(
            user_id=alice["id"], title="paged", task_type="directed"
        )
        session.flush()
        run_id = _seed_run(factory, alice["id"], task.id)
        occurred = datetime(2026, 8, 16, 8, 0, tzinfo=UTC)
        session.add_all(
            [
                DomainEvent(
                    user_id=alice["id"],
                    aggregate_type="task",
                    aggregate_id=task.id,
                    event_type="task.submit",
                    aggregate_version=1,
                    payload={},
                    run_id=run_id,
                    occurred_at=occurred,
                ),
                DomainEvent(
                    user_id=alice["id"],
                    aggregate_type="task",
                    aggregate_id=task.id,
                    event_type="run.failed",
                    aggregate_version=2,
                    payload={"outcome_code": "OLD_OUTCOME"},
                    run_id=run_id,
                    occurred_at=occurred + timedelta(seconds=1),
                ),
                DomainEvent(
                    user_id=alice["id"],
                    aggregate_type="task",
                    aggregate_id=task.id,
                    event_type="run.failed",
                    aggregate_version=3,
                    payload={"outcome_code": "NEW_OUTCOME"},
                    run_id=run_id,
                    occurred_at=occurred + timedelta(seconds=2),
                ),
                DomainEvent(
                    user_id=alice["id"],
                    aggregate_type="task",
                    aggregate_id=task.id,
                    event_type="run.node_blocked",
                    aggregate_version=4,
                    payload={
                        "node_id": "n-fetch",
                        "node_type": "fetch",
                        "reason_code": "NEW_WAIT_REASON",
                    },
                    run_id=run_id,
                    occurred_at=occurred + timedelta(seconds=3),
                ),
            ]
        )
        session.commit()
        task_id = task.id
        last_event_id = session.query(DomainEvent.id).order_by(DomainEvent.id.desc()).first()[0]
    finally:
        session.close()

    original_events_after = ExecutionRepository.events_after

    def two_event_pages(
        self,
        *,
        user_id: int,
        task_id: int,
        after_id: int,
        limit: int,
        through_id: int | None = None,
    ):
        return original_events_after(
            self,
            user_id=user_id,
            task_id=task_id,
            after_id=after_id,
            limit=min(limit, 2),
            through_id=through_id,
        )

    monkeypatch.setattr(ExecutionRepository, "events_after", two_event_pages)

    response = c.get(f"/api/tasks/{task_id}/execution")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["last_event_id"] == last_event_id
    assert body["outcome_code"] == "NEW_OUTCOME"
    assert body["waiting_reason_code"] == "NEW_WAIT_REASON"
    last_activity_at = datetime.fromisoformat(body["last_activity_at"])
    if last_activity_at.tzinfo is None:
        last_activity_at = last_activity_at.replace(tzinfo=UTC)
    assert last_activity_at == occurred + timedelta(seconds=3)
    assert {stage["key"]: stage["event_count"] for stage in body["stages"]}["fetch"] == 1


def test_execution_overview_freezes_event_upper_bound_during_paging(
    client: dict, monkeypatch
) -> None:
    c, factory = client["client"], client["factory"]
    alice = _register(c, "bounded-live-overview@example.com")["user"]
    session = factory()
    try:
        task = TaskRepository(session).create(
            user_id=alice["id"], title="bounded live history", task_type="directed"
        )
        session.flush()
        run_id = _seed_run(factory, alice["id"], task.id)
        session.add(
            DomainEvent(
                user_id=alice["id"],
                aggregate_type="task",
                aggregate_id=task.id,
                event_type="run.started",
                aggregate_version=1,
                payload={"state": "RUNNING"},
                run_id=run_id,
            )
        )
        session.commit()
        task_id = task.id
        frozen_event_id = session.query(DomainEvent.id).order_by(DomainEvent.id.desc()).first()[0]
    finally:
        session.close()

    original_events_after = ExecutionRepository.events_after
    appended_ids: list[int] = []

    def append_while_paging(
        self,
        *,
        user_id: int,
        task_id: int,
        after_id: int,
        limit: int,
        through_id: int | None = None,
    ):
        assert through_id == frozen_event_id
        if not appended_ids:
            event = DomainEvent(
                user_id=user_id,
                aggregate_type="task",
                aggregate_id=task_id,
                event_type="run.failed",
                aggregate_version=2,
                payload={"outcome_code": "APPENDED_AFTER_SNAPSHOT"},
                run_id=run_id,
            )
            self._db.add(event)
            self._db.flush()
            appended_ids.append(event.id)
        return original_events_after(
            self,
            user_id=user_id,
            task_id=task_id,
            after_id=after_id,
            limit=limit,
            through_id=through_id,
        )

    monkeypatch.setattr(ExecutionRepository, "events_after", append_while_paging)

    response = c.get(f"/api/tasks/{task_id}/execution")

    assert response.status_code == 200, response.text
    assert appended_ids[0] > frozen_event_id
    assert response.json()["last_event_id"] == frozen_event_id
    assert response.json()["outcome_code"] is None


def test_canonical_node_failure_projects_to_safe_timeline_fields(client: dict) -> None:
    c, factory = client["client"], client["factory"]
    alice = _register(c, "canonical-timeline@example.com")["user"]
    session = factory()
    try:
        task = TaskRepository(session).create(
            user_id=alice["id"], title="canonical", task_type="directed"
        )
        session.flush()
        task_id = task.id
    finally:
        session.close()
    run_id = _seed_run(factory, alice["id"], task_id)
    session = factory()
    try:
        session.add(
            DomainEvent(
                user_id=alice["id"],
                aggregate_type="task",
                aggregate_id=task_id,
                event_type="run.node_failed",
                aggregate_version=1,
                payload={
                    "node_id": "n-fetch",
                    "node_type": "fetch",
                    "attempt": 2,
                    "state": "FAILED",
                    "reason_code": "NETWORK_TIMEOUT",
                    "private_note": "sensitive-value",
                },
                run_id=run_id,
            )
        )
        session.commit()
    finally:
        session.close()

    resp = c.get(f"/api/tasks/{task_id}/execution/timeline")

    assert resp.status_code == 200, resp.text
    item = resp.json()["items"][0]
    assert item["categories"] == ["error", "retry"]
    assert item["status"] == "FAILED"
    assert item["error_code"] == "NETWORK_TIMEOUT"
    assert "sensitive-value" not in resp.text
