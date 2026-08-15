"""M-06 Task Draft + Chat persistence, idempotency and ownership (TEST A).

SQLite TestClient; no live services needed.
"""

from __future__ import annotations

import pytest
from app.auth.deps import get_login_limiter
from app.auth.rate_limit import InMemoryLoginLimiter
from app.infra.db import Base
from app.infra.deps import get_db
from app.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def client(tmp_path) -> dict:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'draft.db'}", connect_args={"check_same_thread": False}
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


def _create(client: TestClient, **overrides) -> dict:
    body = {"content": "帮我搜集深圳的工业自动化设备供应商"}
    body.update(overrides)
    resp = client.post("/api/tasks", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_create_empty_draft(client: dict) -> None:
    c = client["client"]
    _register(c, "alice@example.com")
    resp = c.post("/api/tasks", json={})
    assert resp.status_code == 201
    task_id = resp.json()["task_id"]

    shell = c.get(f"/api/tasks/{task_id}")
    assert shell.status_code == 200
    data = shell.json()
    assert data["state"] == "DRAFT"
    assert data["task_type"] is None
    assert data["allowed_actions"] == ["submit", "delete"]

    chat = c.get(f"/api/tasks/{task_id}/chat")
    assert chat.status_code == 200
    assert chat.json()["messages"] == []


def test_create_draft_with_first_message(client: dict) -> None:
    c = client["client"]
    _register(c, "alice@example.com")
    created = _create(c)
    task_id = created["task_id"]

    chat = c.get(f"/api/tasks/{task_id}/chat")
    assert chat.status_code == 200
    messages = chat.json()["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert "深圳" in messages[0]["content"]


def test_create_draft_idempotent_single_message(client: dict) -> None:
    c = client["client"]
    _register(c, "alice@example.com")
    key = "req-0001"
    first = _create(c, idempotency_key=key)
    second = _create(c, idempotency_key=key)

    assert first["task_id"] == second["task_id"]
    chat = c.get(f"/api/tasks/{first['task_id']}/chat")
    assert len(chat.json()["messages"]) == 1


def test_append_message_idempotent(client: dict) -> None:
    c = client["client"]
    _register(c, "alice@example.com")
    task_id = _create(c)["task_id"]
    key = "msg-001"

    body = {"content": "补充：再加邮箱字段", "idempotency_key": key}
    resp1 = c.post(f"/api/tasks/{task_id}/messages", json=body)
    resp2 = c.post(f"/api/tasks/{task_id}/messages", json=body)
    assert resp1.status_code == 201 and resp2.status_code == 201
    assert resp1.json()["message"]["id"] == resp2.json()["message"]["id"]

    chat = c.get(f"/api/tasks/{task_id}/chat")
    assert len(chat.json()["messages"]) == 2  # original + one appended


def test_other_user_cannot_read_chat_or_draft(client: dict) -> None:
    c = client["client"]
    _register(c, "alice@example.com")["user"]
    task_id = _create(c)["task_id"]  # created as Alice
    _register(c, "bob@example.com")  # switch cookie to Bob

    assert c.get(f"/api/tasks/{task_id}/chat").status_code == 404
    assert c.get(f"/api/tasks/{task_id}/spec-draft").status_code == 404
    assert c.post(f"/api/tasks/{task_id}/messages", json={"content": "x"}).status_code == 404

    listing = c.get("/api/tasks")
    assert [t["task_id"] for t in listing.json()["tasks"]] == []


def test_save_spec_draft_roundtrip(client: dict) -> None:
    c = client["client"]
    _register(c, "alice@example.com")
    task_id = _create(c)["task_id"]

    payload = {
        "task_type": "EXPLORATORY",
        "goal": "搜集供应商",
        "fields": [{"name": "公司名", "type": "text", "required": True}],
        "auto_expand_fields": True,
    }
    saved = c.put(f"/api/tasks/{task_id}/spec-draft", json={"payload": payload})
    assert saved.status_code == 200
    assert saved.json()["payload"]["task_type"] == "EXPLORATORY"
    assert saved.json()["payload"]["fields"][0]["name"] == "公司名"

    fetched = c.get(f"/api/tasks/{task_id}/spec-draft")
    assert fetched.status_code == 200
    assert fetched.json()["payload"]["goal"] == "搜集供应商"


def test_invalid_spec_payload_422(client: dict) -> None:
    c = client["client"]
    _register(c, "alice@example.com")
    task_id = _create(c)["task_id"]

    bad = c.put(f"/api/tasks/{task_id}/spec-draft", json={"payload": {"goal": 123}})
    assert bad.status_code == 422
    assert bad.json()["detail"]["code"] == "SPEC_VALIDATION_ERROR"


def test_add_seed_url_writes_draft_context_only(client: dict) -> None:
    c = client["client"]
    _register(c, "alice@example.com")
    task_id = _create(c)["task_id"]

    resp = c.post(f"/api/tasks/{task_id}/seed-urls", json={"url": "https://example.com/suppliers"})
    assert resp.status_code == 200
    assert resp.json()["payload"]["source_scope"]["seed_urls"] == ["https://example.com/suppliers"]

    bad = c.post(f"/api/tasks/{task_id}/seed-urls", json={"url": "not-a-url"})
    assert bad.status_code == 400
    assert bad.json()["detail"]["code"] == "DOMAIN_ERROR"


@pytest.mark.parametrize("url", ["https://example.com:bad/notice", "https://[::1"])
def test_add_seed_url_rejects_malformed_urls_as_domain_errors(client: dict, url: str) -> None:
    c = client["client"]
    _register(c, "alice@example.com")
    task_id = _create(c)["task_id"]

    response = c.post(f"/api/tasks/{task_id}/seed-urls", json={"url": url})

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "DOMAIN_ERROR"
