"""Task shell query API behavior via TestClient (SQLite).

M-05 only adds read-only owner-safe task queries; commands arrive in M-06+.
"""

from __future__ import annotations

import pytest
from app.auth.deps import get_login_limiter
from app.auth.rate_limit import InMemoryLoginLimiter
from app.domain.repository import TaskRepository
from app.infra.db import Base
from app.infra.deps import get_db
from app.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def client(tmp_path) -> dict:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'tasks.db'}", connect_args={"check_same_thread": False}
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


def _seed_task(factory, user_id: int, *, title: str = "seed"):
    session = factory()
    try:
        task = TaskRepository(session).create(user_id=user_id, title=title, task_type="directed")
        session.refresh(task)
        return task
    finally:
        session.close()


def test_owner_can_list_and_read_own_task(client: dict) -> None:
    c, factory = client["client"], client["factory"]
    alice = _register(c, "alice@example.com")["user"]
    task = _seed_task(factory, alice["id"])

    listing = c.get("/api/tasks")
    assert listing.status_code == 200
    assert [t["task_id"] for t in listing.json()["tasks"]] == [task.id]

    shell = c.get(f"/api/tasks/{task.id}")
    assert shell.status_code == 200
    data = shell.json()
    assert data["task_id"] == task.id
    assert data["title"] == "seed"
    assert data["state"] == "DRAFT"
    assert data["version"] == 1
    assert data["current_spec_version"] is None
    assert data["current_plan_version"] is None
    # DRAFT allows submit + delete from the M-04 state machine.
    assert data["allowed_actions"] == ["submit", "delete"]


def test_other_user_gets_404_no_leak(client: dict) -> None:
    c, factory = client["client"], client["factory"]
    alice = _register(c, "alice@example.com")["user"]
    _register(c, "bob@example.com")  # cookie now belongs to bob
    task = _seed_task(factory, alice["id"])

    shell = c.get(f"/api/tasks/{task.id}")
    assert shell.status_code == 404
    assert shell.json()["detail"]["code"] == "NOT_FOUND"

    listing = c.get("/api/tasks")
    assert listing.status_code == 200
    assert listing.json()["tasks"] == []


def test_unauthenticated_requires_login(client: dict) -> None:
    c = client["client"]
    assert c.get("/api/tasks").status_code == 401
    assert c.get("/api/tasks/1").status_code == 401


def test_unknown_task_404(client: dict) -> None:
    c = client["client"]
    _register(c, "alice@example.com")
    resp = c.get("/api/tasks/999999")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "NOT_FOUND"
