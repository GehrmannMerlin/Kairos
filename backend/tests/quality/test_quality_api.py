"""M-14 Quality Query API（D-062）。

验证（A-Lite 紧凑套件）：
1. Quality 数字来自真实 DB facts（分区计数）。
2. 字段缺失/冲突/低置信度返回正确聚合。
3. Deep Link query 与 M-13 Data Query contract 一致（review_type 属于有限集合）。
4. owner isolation：跨用户 → 404。
5. Metrics Version Boundary 绑定 QualitySnapshot。
"""

from __future__ import annotations

from app.domain.models import (
    CollectionSpecVersion,
    QualitySnapshot,
    Record,
    URLResource,
)
from app.domain.repository import TaskRepository
from fastapi.testclient import TestClient


def _register(client: TestClient, email: str) -> dict:
    resp = client.post(
        "/api/auth/register",
        json={"email": email, "password": "password123", "confirm_password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _seed_spec(factory, user_id: int, task_id: int) -> None:
    session = factory()
    try:
        session.add(
            CollectionSpecVersion(
                user_id=user_id,
                task_id=task_id,
                version=1,
                spec_type="collection",
                schema_version="m06.1",
                payload={
                    "schema_version": "m06.1",
                    "task_type": "directed",
                    "goal": "采集企业信息",
                    "fields": [
                        {"name": "company", "type": "text", "required": True},
                        {"name": "phone", "type": "text", "required": False},
                    ],
                },
            )
        )
        session.commit()
    finally:
        session.close()


def _seed_records(factory, user_id: int, task_id: int) -> None:
    """真实形态：payload.values 嵌套；review_type 走列。"""
    session = factory()
    try:
        rows = [
            {
                "partition": "passed",
                "review_type": None,
                "values": {"company": "上海工业自动化有限公司", "phone": "021-12345678"},
                "url_resource_id": 1,
            },
            {
                "partition": "needs_review",
                "review_type": "missing_required",
                "values": {"company": "未知名企业"},
                "url_resource_id": 1,
            },
            {
                "partition": "needs_review",
                "review_type": "missing_required",
                "values": {},
                "url_resource_id": None,
            },
        ]
        for i, row in enumerate(rows):
            session.add(
                Record(
                    user_id=user_id,
                    task_id=task_id,
                    spec_version=1,
                    partition=row["partition"],
                    review_type=row["review_type"],
                    url_resource_id=row["url_resource_id"],
                    payload={
                        "values": row["values"],
                        "snapshot_id": 10 + i,
                        "spec_version": 1,
                        "url": f"https://example.com/{i}",
                    },
                )
            )
        session.add(
            URLResource(
                user_id=user_id,
                task_id=task_id,
                url="https://example.com/0",
                url_hash="h0",
                source_type="official_site",
                status="FETCHED",
            )
        )
        session.commit()
    finally:
        session.close()


def _seed_snapshot(factory, user_id: int, task_id: int) -> int:
    session = factory()
    try:
        row = QualitySnapshot(
            user_id=user_id,
            task_id=task_id,
            run_id=1,
            spec_version=1,
            validation_version="v1",
            dataset_version="task-1-v2",
            sampling_policy_version="sp1",
            metrics={
                "pass_rate": 0.3333,
                "missing_rate": 0.6667,
                "duplicate_rate": 0.0,
                "conflict_count": 0,
                "source_coverage": 1.0,
                "sampling_accuracy": None,
            },
            denominators={
                "total_validated_records": 3,
                "eligible_sources": 1,
                "covered_sources": 1,
            },
            sample_refs=[{"record_id": 1}],
        )
        session.add(row)
        session.commit()
        return row.id
    finally:
        session.close()


def _seed_quality_task(factory, user_id: int) -> int:
    session = factory()
    try:
        task = TaskRepository(session).create(
            user_id=user_id, title="seed", task_type="directed"
        )
        session.flush()
        task_id = task.id
    finally:
        session.close()
    _seed_spec(factory, user_id, task_id)
    _seed_records(factory, user_id, task_id)
    return task_id


def test_quality_numbers_come_from_db_facts(client: dict) -> None:
    c, factory = client["client"], client["factory"]
    alice = _register(c, "alice@example.com")["user"]
    task_id = _seed_quality_task(factory, alice["id"])

    resp = c.get(f"/api/tasks/{task_id}/quality")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["task_id"] == task_id
    assert body["summary"] == {"total_records": 3, "passed": 1, "needs_review": 2, "rejected": 0}
    assert body["diagnostics"]["missing_required"] == 2
    # 没有假指标：全部来自 DB facts
    assert body["metrics"]["pass_rate"] == 0.3333


def test_quality_field_completeness_from_spec_and_records(client: dict) -> None:
    c, factory = client["client"], client["factory"]
    alice = _register(c, "alice@example.com")["user"]
    task_id = _seed_quality_task(factory, alice["id"])

    resp = c.get(f"/api/tasks/{task_id}/quality")
    body = resp.json()
    by_field = {row["field_name"]: row for row in body["field_completeness"]}
    assert by_field["company"]["total"] == 3
    assert by_field["company"]["non_null"] == 2  # 第 3 条 values 为空
    assert by_field["company"]["missing"] == 1
    assert by_field["phone"]["non_null"] == 1


def test_quality_metric_items_deep_links_match_m13_contract(client: dict) -> None:
    c, factory = client["client"], client["factory"]
    alice = _register(c, "alice@example.com")["user"]
    task_id = _seed_quality_task(factory, alice["id"])

    resp = c.get(f"/api/tasks/{task_id}/quality")
    items = {it["key"]: it for it in resp.json()["items"]}

    def drill(key: str) -> dict:
        return {k: v for k, v in items[key]["drilldown"].items() if v is not None}

    assert drill("passed") == {"status": "passed"}
    assert drill("needs_review") == {"status": "review"}
    assert drill("missing_required") == {
        "status": "review",
        "review_type": "missing_required",
    }
    assert drill("rejected") == {"status": "rejected"}
    # 来源覆盖下钻：source_type 落在 M-13 contract
    assert drill("source:official_site") == {"source_type": "official_site"}


def test_quality_version_boundary_from_snapshot(client: dict) -> None:
    c, factory = client["client"], client["factory"]
    alice = _register(c, "alice@example.com")["user"]
    task_id = _seed_quality_task(factory, alice["id"])
    snapshot_id = _seed_snapshot(factory, alice["id"], task_id)

    resp = c.get(f"/api/tasks/{task_id}/quality")
    body = resp.json()
    assert body["snapshot_id"] == snapshot_id
    assert body["dataset_version"] == "task-1-v2"
    assert body["validation_version"] == "v1"
    assert body["sampling_policy_version"] == "sp1"
    assert body["spec_version"] == 1
    assert body["sampling"]["sample_count"] == 1


def test_quality_cross_user_404(client: dict) -> None:
    c, factory = client["client"], client["factory"]
    alice = _register(c, "alice@example.com")["user"]
    task_id = _seed_quality_task(factory, alice["id"])
    _register(c, "bob@example.com")  # cookie 切到 bob

    resp = c.get(f"/api/tasks/{task_id}/quality")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "NOT_FOUND"


def test_quality_empty_task_explicit_zero(client: dict) -> None:
    c, factory = client["client"], client["factory"]
    alice = _register(c, "alice@example.com")["user"]
    session = factory()
    try:
        task = TaskRepository(session).create(
            user_id=alice["id"], title="empty", task_type="directed"
        )
        session.commit()
        task_id = task.id
    finally:
        session.close()

    resp = c.get(f"/api/tasks/{task_id}/quality")
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["total_records"] == 0
    assert body["field_completeness"] == []
    assert body["source_coverage"] == []
    assert body["sampling"]["sample_count"] == 0
