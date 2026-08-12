"""M-13 Records Query / Review API via TestClient（SQLite）。"""

from __future__ import annotations

from app.domain.models import FieldEvidence, Record
from app.domain.repository import TaskRepository
from fastapi.testclient import TestClient


def _register(client: TestClient, email: str) -> dict:
    resp = client.post(
        "/api/auth/register",
        json={"email": email, "password": "password123", "confirm_password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _seed_task_with_records(factory, user_id: int) -> int:
    session = factory()
    try:
        task = TaskRepository(session).create(user_id=user_id, title="seed", task_type="directed")
        session.flush()
        for i, partition in enumerate(("passed", "needs_review", "needs_review")):
            rec = Record(
                user_id=user_id,
                task_id=task.id,
                spec_version=1,
                partition=partition,
                review_type="missing_required" if partition == "needs_review" else None,
                review_reason="missing_required" if partition == "needs_review" else None,
                payload={
                    "标题": f"记录-{i}",
                    "source_type": "official_site",
                    "extract_method": "llm",
                },
            )
            session.add(rec)
            session.flush()
            if partition == "needs_review" and i == 1:
                session.add(
                    FieldEvidence(
                        user_id=user_id,
                        task_id=task.id,
                        record_id=rec.id,
                        field_name="标题",
                        value="记录-2",
                        extract_method="llm",
                        extractor_version="m11.1",
                        confidence=0.7,
                    )
                )
        session.commit()
        return task.id
    finally:
        session.close()


def test_query_records_counts_and_total(client: dict) -> None:
    c, factory = client["client"], client["factory"]
    alice = _register(c, "alice@example.com")["user"]
    task_id = _seed_task_with_records(factory, alice["id"])

    resp = c.get(f"/api/tasks/{task_id}/records")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["partition_counts"] == {"passed": 1, "needs_review": 2}
    assert body["total"] == 3
    assert body["page"] == 1
    assert body["page_size"] == 20
    assert len(body["items"]) == 3


def test_query_records_partition_and_deep_link_params(client: dict) -> None:
    c, factory = client["client"], client["factory"]
    alice = _register(c, "alice@example.com")["user"]
    task_id = _seed_task_with_records(factory, alice["id"])

    resp = c.get(f"/api/tasks/{task_id}/records?partition=needs_review")
    assert resp.status_code == 200
    assert resp.json()["total"] == 2

    # D-062 Deep Link：review_type 下钻
    resp = c.get(f"/api/tasks/{task_id}/records?review_type=missing_required")
    assert resp.status_code == 200
    assert resp.json()["total"] == 2


def test_query_records_cross_user_404(client: dict) -> None:
    c, factory = client["client"], client["factory"]
    alice = _register(c, "alice@example.com")["user"]
    task_id = _seed_task_with_records(factory, alice["id"])
    _register(c, "bob@example.com")  # cookie 切换到 bob

    resp = c.get(f"/api/tasks/{task_id}/records")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "NOT_FOUND"


def test_record_detail_includes_evidence(client: dict) -> None:
    c, factory = client["client"], client["factory"]
    alice = _register(c, "alice@example.com")["user"]
    task_id = _seed_task_with_records(factory, alice["id"])

    resp = c.get(f"/api/tasks/{task_id}/records")
    items = resp.json()["items"]
    needs_review = [it for it in items if it["partition"] == "needs_review"]
    record_id = needs_review[0]["record_id"]

    detail = c.get(f"/api/tasks/{task_id}/records/{record_id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["partition"] == "needs_review"
    assert "approve" in body["allowed_actions"]
    assert "edit" in body["allowed_actions"]
    field = next(f for f in body["fields"] if f["field_name"] == "标题")
    assert field["extract_method"] == "llm"
    assert field["extractor_version"] == "m11.1"


def test_review_approve_via_api(client: dict) -> None:
    c, factory = client["client"], client["factory"]
    alice = _register(c, "alice@example.com")["user"]
    task_id = _seed_task_with_records(factory, alice["id"])

    resp = c.get(f"/api/tasks/{task_id}/records?partition=needs_review")
    record_id = resp.json()["items"][0]["record_id"]
    data_version = resp.json()["items"][0]["data_version"]

    review = c.post(
        f"/api/tasks/{task_id}/records/{record_id}/review",
        json={"action": "approve", "expected_data_version": data_version},
    )
    assert review.status_code == 200, review.text
    assert review.json()["record"]["partition"] == "passed"

    # 复查计数
    counts = c.get(f"/api/tasks/{task_id}/records").json()["partition_counts"]
    assert counts == {"passed": 2, "needs_review": 1}
