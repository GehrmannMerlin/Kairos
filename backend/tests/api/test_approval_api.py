"""M-08 approval API: query / approve / reject / revoke + owner isolation."""

from __future__ import annotations

import pytest
from app.approval.service import ApprovalService
from app.auth.deps import get_login_limiter
from app.auth.rate_limit import InMemoryLoginLimiter
from app.infra.db import Base
from app.infra.deps import get_db
from app.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def approval_client(tmp_path) -> dict:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'approval_api.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def _override_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    limiter = InMemoryLoginLimiter(max_attempts=3, window_seconds=100)
    app = create_app()
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_login_limiter] = lambda: limiter
    with TestClient(app) as client:
        yield {"client": client, "factory": factory}
    app.dependency_overrides.clear()


def _register(client: TestClient, email: str) -> dict:
    resp = client.post(
        "/api/auth/register",
        json={"email": email, "password": "password123", "confirm_password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_approval(factory, user_id: int, task_id: int) -> int:
    session = factory()
    try:
        from app.approval.schemas import ApprovalScope

        svc = ApprovalService(session)
        approval = svc.request_approval(
            user_id=user_id,
            task_id=task_id,
            spec_version=1,
            plan_version=1,
            node_id="n1",
            node_type="fetch",
            action_type="fetch_non_public",
            target="https://example.com/private/{id}",
            parameters={"non_public": True},
            scope=ApprovalScope.THIS_ACTION,
            status_payload={"run_id": 1},
        )
        return approval.id
    finally:
        session.close()


def test_approval_query_and_approve(approval_client: dict) -> None:
    c, factory = approval_client["client"], approval_client["factory"]
    alice = _register(c, "alice@example.com")["user"]
    task_id = c.post("/api/tasks", json={"content": "抓取网站"}).json()["task_id"]
    approval_id = _create_approval(factory, alice["id"], task_id)

    got = c.get(f"/api/approvals/{approval_id}")
    assert got.status_code == 200
    assert got.json()["state"] == "PENDING"
    assert got.json()["target"] == "https://example.com/private/{id}"

    approved = c.post(f"/api/approvals/{approval_id}/approve", json={"expected_version": 1})
    assert approved.status_code == 200
    assert approved.json()["state"] == "APPROVED"

    # 二次操作被拒（已处理）
    again = c.post(f"/api/approvals/{approval_id}/approve", json={"expected_version": 1})
    assert again.status_code == 400


def test_approval_reject_and_revoke(approval_client: dict) -> None:
    c, factory = approval_client["client"], approval_client["factory"]
    alice = _register(c, "alice@example.com")["user"]
    task_id = c.post("/api/tasks", json={"content": "抓取网站"}).json()["task_id"]
    a1 = _create_approval(factory, alice["id"], task_id)
    a2 = _create_approval(factory, alice["id"], task_id)

    rejected = c.post(f"/api/approvals/{a1}/reject", json={"expected_version": 1})
    assert rejected.json()["state"] == "REJECTED"

    revoked = c.post(f"/api/approvals/{a2}/revoke", json={"expected_version": 1})
    assert revoked.json()["state"] == "REVOKED"


def test_approval_list_and_owner_isolation(approval_client: dict) -> None:
    c, factory = approval_client["client"], approval_client["factory"]
    alice = _register(c, "alice@example.com")["user"]
    task_id = c.post("/api/tasks", json={"content": "抓取网站"}).json()["task_id"]
    approval_id = _create_approval(factory, alice["id"], task_id)

    listing = c.get(f"/api/tasks/{task_id}/approvals")
    assert listing.status_code == 200
    assert listing.json()["approvals"][0]["approval_id"] == approval_id

    pending = c.get(f"/api/tasks/{task_id}/approvals/pending")
    assert pending.status_code == 200
    assert len(pending.json()["approvals"]) == 1

    # User B 不能读取/批准 A 的 Approval（owner-safe 404）
    _register(c, "bob@example.com")
    assert c.get(f"/api/approvals/{approval_id}").status_code == 404
    assert (
        c.post(f"/api/approvals/{approval_id}/approve", json={"expected_version": 1}).status_code
        == 404
    )
