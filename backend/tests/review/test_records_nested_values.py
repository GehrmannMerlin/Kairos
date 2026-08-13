"""M-14：真实提取记录 payload 嵌套（values）时，RecordView/Detail 仍暴露字段 + 证据。

M-11 真实记录 payload = {values: {...}, snapshot_id, spec_version, url, ...}，字段值
嵌套在 values。M-13 to_view/to_detail 直接遍历 payload 会把 values 当单个字段，且
FieldEvidence（含 snapshot_id）无法关联到真实字段。M-14 展平 values 后，证据引用
必须浮出，供 Evidence 完整追溯。
"""

from __future__ import annotations

from app.domain.models import FieldEvidence, PageSnapshot, Record
from app.domain.repository import TaskRepository
from fastapi.testclient import TestClient


def _register(client: TestClient, email: str) -> dict:
    resp = client.post(
        "/api/auth/register",
        json={"email": email, "password": "password123", "confirm_password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _seed_nested(factory, user_id: int) -> tuple[int, int]:
    """seed：真实形态记录（values 嵌套）+ snapshot + field evidence。"""
    session = factory()
    try:
        task = TaskRepository(session).create(user_id=user_id, title="seed", task_type="directed")
        session.flush()
        task_id = task.id
        snap = PageSnapshot(
            user_id=user_id,
            task_id=task_id,
            spec_version=1,
            content_hash="h",
            storage_ref="snapshots/u/h.html",
            mime_type="text/html",
            tool="http",
            final_url="https://example.com/1",
        )
        session.add(snap)
        session.flush()
        rec = Record(
            user_id=user_id,
            task_id=task_id,
            spec_version=1,
            partition="needs_review",
            review_type="missing_required",
            payload={
                "values": {"company": "上海自动化设备有限公司", "phone": "021-12345678"},
                "snapshot_id": snap.id,
                "spec_version": 1,
                "url": "https://example.com/1",
            },
        )
        session.add(rec)
        session.flush()
        session.add(
            FieldEvidence(
                user_id=user_id,
                task_id=task_id,
                record_id=rec.id,
                snapshot_id=snap.id,
                field_name="company",
                value="上海自动化设备有限公司",
                raw_snippet="<td>上海自动化设备有限公司</td>",
                extract_method="css",
                extractor_version="m11.1",
                confidence=0.95,
            )
        )
        session.commit()
        return task_id, rec.id
    finally:
        session.close()


def test_nested_values_detail_exposes_fields_and_snapshot_evidence(client: dict) -> None:
    c, factory = client["client"], client["factory"]
    alice = _register(c, "alice@example.com")["user"]
    task_id, record_id = _seed_nested(factory, alice["id"])

    # RecordView：fields 是真实字段，不是 values/snapshot_id 元数据
    listed = c.get(f"/api/tasks/{task_id}/records").json()["items"]
    assert listed[0]["fields"]["company"] == "上海自动化设备有限公司"
    assert "values" not in listed[0]["fields"]

    # RecordDetail：字段证据带 snapshot_id（M-14 Evidence 追溯入口）
    detail = c.get(f"/api/tasks/{task_id}/records/{record_id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    company = next(f for f in body["fields"] if f["field_name"] == "company")
    assert company["value"] == "上海自动化设备有限公司"
    assert company["extract_method"] == "css"
    assert company["confidence"] == 0.95
    assert company["snapshot_id"] is not None
