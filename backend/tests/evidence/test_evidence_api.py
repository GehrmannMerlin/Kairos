"""M-14 Evidence Query + content API（D-056/D-064）。

验证（A-Lite 紧凑套件）：
1. owner access PASS。
2. cross-user 404（不泄漏存在性）。
3. 显示的是存储的 Snapshot，不是 live source（live_fetch_count = 0）。
4. display fallback：image→snapshot；text snippet→text；否则→raw。
5. content endpoint owner-safe 且返回存储字节。
6. 不泄漏 MinIO storage key。
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.models import FieldEvidence, PageSnapshot
from app.domain.repository import TaskRepository
from fastapi.testclient import TestClient


def _register(client: TestClient, email: str) -> dict:
    resp = client.post(
        "/api/auth/register",
        json={"email": email, "password": "password123", "confirm_password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _seed_snapshot(
    factory,
    user_id: int,
    task_id: int,
    *,
    storage_ref: str | None,
    mime_type: str = "text/html",
    final_url: str = "https://example.com/page",
    tool: str = "http",
    with_evidence: bool = True,
) -> int:
    session = factory()
    try:
        row = PageSnapshot(
            user_id=user_id,
            task_id=task_id,
            spec_version=1,
            content_hash="h",
            storage_ref=storage_ref,
            mime_type=mime_type,
            tool=tool,
            tool_version="m10.1",
            final_url=final_url,
            http_status=200,
            content_length=100,
            captured_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
        )
        session.add(row)
        session.flush()
        snap_id = row.id
        if with_evidence:
            session.add(
                FieldEvidence(
                    user_id=user_id,
                    task_id=task_id,
                    record_id=1,
                    field_name="company",
                    value="上海自动化",
                    raw_snippet="<td>上海自动化</td>",
                    source_locator="table#biz tr:nth-child(1) td",
                    extract_method="css",
                    extractor_version="m11.1",
                    confidence=0.95,
                    snapshot_id=snap_id,
                )
            )
        session.commit()
        return snap_id
    finally:
        session.close()


def _make_task(factory, user_id: int) -> int:
    session = factory()
    try:
        task = TaskRepository(session).create(
            user_id=user_id, title="seed", task_type="directed"
        )
        session.commit()
        return task.id
    finally:
        session.close()


def test_evidence_owner_access_and_display_facts(client: dict) -> None:
    c, factory, storage = client["client"], client["factory"], client["storage"]
    alice = _register(c, "alice@example.com")["user"]
    task_id = _make_task(factory, alice["id"])
    storage.put(
        "snapshots/u1/h/html.html", "<html><body>历史快照</body></html>".encode(), "text/html"
    )
    snap_id = _seed_snapshot(
        factory, alice["id"], task_id, storage_ref="snapshots/u1/h/html.html"
    )

    resp = c.get(f"/api/tasks/{task_id}/evidence/{snap_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["evidence_id"] == snap_id
    assert body["source_url"] == "https://example.com/page"
    assert body["display_mode"] == "text"  # 有正文 snippet → text
    assert body["field_evidence"][0]["field_name"] == "company"
    assert body["field_evidence"][0]["confidence"] == 0.95
    assert body["has_content"] is True
    assert body["download_url"] == f"/tasks/{task_id}/evidence/{snap_id}/content"
    # 不泄漏内部存储引用
    assert "snapshots/u1" not in resp.text


def test_evidence_no_live_fetch_during_api(client: dict, monkeypatch) -> None:
    c, factory, storage = client["client"], client["factory"], client["storage"]
    alice = _register(c, "alice@example.com")["user"]
    task_id = _make_task(factory, alice["id"])
    storage.put("snapshots/u1/h/html.html", "<html>历史</html>".encode(), "text/html")
    snap_id = _seed_snapshot(
        factory, alice["id"], task_id, storage_ref="snapshots/u1/h/html.html"
    )

    calls = {"count": 0}

    async def _forbidden_fetch(*_args, **_kwargs):
        calls["count"] += 1
        raise AssertionError("Evidence API 不得发起 live HTTP fetch")

    monkeypatch.setattr("app.crawling.http_fetch.SafeFetchHttp.get_bytes", _forbidden_fetch)

    r1 = c.get(f"/api/tasks/{task_id}/evidence/{snap_id}")
    assert r1.status_code == 200
    r2 = c.get(f"/api/tasks/{task_id}/evidence/{snap_id}/content")
    assert r2.status_code == 200
    assert calls["count"] == 0


def test_evidence_content_streams_stored_bytes_owner_safe(client: dict) -> None:
    c, factory, storage = client["client"], client["factory"], client["storage"]
    alice = _register(c, "alice@example.com")["user"]
    task_id = _make_task(factory, alice["id"])
    storage.put("snapshots/u1/h/html.html", "<html>历史快照内容</html>".encode(), "text/html")
    snap_id = _seed_snapshot(
        factory, alice["id"], task_id, storage_ref="snapshots/u1/h/html.html"
    )

    resp = c.get(f"/api/tasks/{task_id}/evidence/{snap_id}/content")
    assert resp.status_code == 200
    assert resp.content == "<html>历史快照内容</html>".encode()
    assert resp.headers["content-type"].startswith("text/html")
    assert storage.get_calls == ["snapshots/u1/h/html.html"]


def test_evidence_cross_user_404(client: dict) -> None:
    c, factory = client["client"], client["factory"]
    alice = _register(c, "alice@example.com")["user"]
    task_id = _make_task(factory, alice["id"])
    snap_id = _seed_snapshot(factory, alice["id"], task_id, storage_ref=None)
    _register(c, "bob@example.com")

    resp = c.get(f"/api/tasks/{task_id}/evidence/{snap_id}")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "NOT_FOUND"

    resp_content = c.get(f"/api/tasks/{task_id}/evidence/{snap_id}/content")
    assert resp_content.status_code == 404


def test_evidence_display_modes(client: dict) -> None:
    c, factory, storage = client["client"], client["factory"], client["storage"]
    alice = _register(c, "alice@example.com")["user"]
    task_id = _make_task(factory, alice["id"])

    # image → snapshot
    storage.put("img.png", b"\x89PNG", "image/png")
    img = _seed_snapshot(
        factory, alice["id"], task_id, storage_ref="img.png", mime_type="image/png"
    )
    assert c.get(f"/api/tasks/{task_id}/evidence/{img}").json()["display_mode"] == "snapshot"

    # 无 snippet → raw（text/html 但无 FieldEvidence 正文）
    raw = _seed_snapshot(
        factory,
        alice["id"],
        task_id,
        storage_ref="snapshots/u1/h/raw.html",
        mime_type="text/html",
        with_evidence=False,
    )
    body = c.get(f"/api/tasks/{task_id}/evidence/{raw}").json()
    assert body["display_mode"] == "raw"
    assert body["field_evidence"] == []
