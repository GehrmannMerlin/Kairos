"""M-07 pause/resume/cancel command endpoints (SQLite TestClient, no live Temporal).

`get_temporal_client` is overridden with a fake so the route never connects to a
real Temporal server; the command already took effect in the DB before the
outbox dispatch, and a dispatch failure never changes the HTTP response.
"""

from __future__ import annotations

import pytest
from app.auth.deps import get_login_limiter
from app.auth.rate_limit import InMemoryLoginLimiter
from app.domain.repository import TaskRepository
from app.domain.service import DomainService
from app.infra.db import Base
from app.infra.deps import get_db
from app.infra.temporal import get_temporal_client
from app.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


class _FakeHandle:
    async def signal(self, *args, **kwargs) -> None:
        return None


class _FakeTemporalClient:
    def get_workflow_handle(self, workflow_id: str) -> _FakeHandle:
        return _FakeHandle()


@pytest.fixture()
def client(tmp_path) -> dict:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'commands.db'}", connect_args={"check_same_thread": False}
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
    app.dependency_overrides[get_temporal_client] = lambda: _FakeTemporalClient()
    with TestClient(app) as test_client:
        yield {"client": test_client, "factory": factory}
    app.dependency_overrides.clear()


def _register(client: TestClient, email: str) -> dict:
    resp = client.post(
        "/api/auth/register",
        json={"email": email, "password": "password123", "confirm_password": "password123"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create(client: TestClient) -> int:
    resp = client.post("/api/tasks", json={})
    assert resp.status_code == 201, resp.text
    return resp.json()["task_id"]


def _to_running(factory, user_id: int, task_id: int) -> int:
    """DRAFT -> QUEUED -> RUNNING via DomainService; returns current version (3)."""
    session = factory()
    try:
        repo = TaskRepository(session)
        DomainService(repo).transition_task(
            user_id=user_id, task_id=task_id, command="submit", expected_version=1
        )
        DomainService(repo).transition_task(
            user_id=user_id, task_id=task_id, command="start", expected_version=2
        )
        return repo.get_owned(user_id, task_id).version
    finally:
        session.close()


def _to_paused(factory, user_id: int, task_id: int) -> int:
    """RUNNING -> PAUSING -> PAUSED via DomainService; returns current version (5)."""
    session = factory()
    try:
        repo = TaskRepository(session)
        svc = DomainService(repo)
        svc.transition_task(user_id=user_id, task_id=task_id, command="submit", expected_version=1)
        svc.transition_task(user_id=user_id, task_id=task_id, command="start", expected_version=2)
        svc.transition_task(user_id=user_id, task_id=task_id, command="pause", expected_version=3)
        svc.transition_task(
            user_id=user_id, task_id=task_id, command="mark_paused", expected_version=4
        )
        return repo.get_owned(user_id, task_id).version
    finally:
        session.close()


def test_pause_command_returns_pausing(client: dict) -> None:
    c = client["client"]
    user = _register(c, "alice@example.com")["user"]
    task_id = _create(c)
    version = _to_running(client["factory"], user["id"], task_id)

    resp = c.post(f"/api/tasks/{task_id}/commands/pause", json={"expected_version": version})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["command"] == "pause"
    assert data["state"] == "PAUSING"
    assert data["version"] == version + 1


def test_resume_command_returns_running(client: dict) -> None:
    c = client["client"]
    user = _register(c, "alice@example.com")["user"]
    task_id = _create(c)
    version = _to_paused(client["factory"], user["id"], task_id)

    resp = c.post(f"/api/tasks/{task_id}/commands/resume", json={"expected_version": version})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["command"] == "resume"
    assert data["state"] == "RUNNING"


def test_cancel_command_returns_cancelling(client: dict) -> None:
    c = client["client"]
    user = _register(c, "alice@example.com")["user"]
    task_id = _create(c)
    version = _to_running(client["factory"], user["id"], task_id)

    resp = c.post(f"/api/tasks/{task_id}/commands/cancel", json={"expected_version": version})
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "CANCELLING"


def test_pause_replay_same_key_no_double_transition(client: dict) -> None:
    """同 key 重试（即使带新读到的版本号）是 replay，不重复转换。"""
    c = client["client"]
    user = _register(c, "alice@example.com")["user"]
    task_id = _create(c)
    version = _to_running(client["factory"], user["id"], task_id)
    key = "k-pause-api"

    first = c.post(
        f"/api/tasks/{task_id}/commands/pause",
        json={"expected_version": version, "idempotency_key": key},
    )
    second = c.post(
        f"/api/tasks/{task_id}/commands/pause",
        json={"expected_version": version + 1, "idempotency_key": key},
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["state"] == second.json()["state"] == "PAUSING"
    assert second.json()["version"] == first.json()["version"]


def test_unknown_command_404(client: dict) -> None:
    c = client["client"]
    _register(c, "alice@example.com")
    task_id = _create(c)

    resp = c.post(f"/api/tasks/{task_id}/commands/explode", json={"expected_version": 1})
    assert resp.status_code == 404
    assert "未知命令" in resp.json()["detail"]


def test_other_user_cannot_command_task_404(client: dict) -> None:
    c = client["client"]
    alice = _register(c, "alice@example.com")["user"]
    task_id = _create(c)
    _to_running(client["factory"], alice["id"], task_id)

    _register(c, "bob@example.com")
    resp = c.post(f"/api/tasks/{task_id}/commands/pause", json={"expected_version": 1})
    assert resp.status_code == 404
