"""CollectionTemplate API routing + owner isolation."""

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
        f"sqlite:///{tmp_path / 'templates.db'}", connect_args={"check_same_thread": False}
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


def _template_body(**overrides) -> dict:
    body = {
        "name": "供应商模板",
        "task_type": "EXPLORATORY",
        "goal_template": "帮我搜集{city}的工业自动化设备供应商",
        "variables": [{"name": "city", "label": "城市", "required": True}],
        "field_schema": [{"name": "公司名", "type": "text", "required": True}],
        "completion_conditions": [],
        "advanced_settings": {},
        "field_expansion": {},
    }
    body.update(overrides)
    return body


def test_create_and_use_template(client: dict) -> None:
    c = client["client"]
    _register(c, "alice@example.com")

    created = c.post("/api/templates", json=_template_body())
    assert created.status_code == 201, created.text
    tpl = created.json()
    assert tpl["version"] == 1
    assert tpl["goal_template"] == "帮我搜集{city}的工业自动化设备供应商"

    used = c.post(f"/api/templates/{tpl['template_id']}/use", json={"variables": {"city": "深圳"}})
    assert used.status_code == 200
    task_id = used.json()["task_id"]

    shell = c.get(f"/api/tasks/{task_id}")
    data = shell.json()
    assert data["state"] == "DRAFT"
    assert data["task_type"] == "EXPLORATORY"

    draft = c.get(f"/api/tasks/{task_id}/spec-draft")
    assert draft.json()["payload"]["goal"] == "帮我搜集深圳的工业自动化设备供应商"


def test_cross_user_template_404(client: dict) -> None:
    c = client["client"]
    _register(c, "alice@example.com")
    tpl = c.post("/api/templates", json=_template_body()).json()
    _register(c, "bob@example.com")

    assert c.get(f"/api/templates/{tpl['template_id']}").status_code == 404
    assert (
        c.post(
            f"/api/templates/{tpl['template_id']}/use", json={"variables": {"city": "X"}}
        ).status_code
        == 404
    )
    assert c.patch(f"/api/templates/{tpl['template_id']}", json=_template_body()).status_code == 404
    listing = c.get("/api/templates")
    assert listing.json()["templates"] == []


def test_update_creates_new_version(client: dict) -> None:
    c = client["client"]
    _register(c, "alice@example.com")
    tpl = c.post("/api/templates", json=_template_body()).json()

    updated = c.patch(
        f"/api/templates/{tpl['template_id']}",
        json=_template_body(goal_template="帮我搜集{city}的 B"),
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2


def test_duplicate_and_favorite(client: dict) -> None:
    c = client["client"]
    _register(c, "alice@example.com")
    tpl = c.post("/api/templates", json=_template_body()).json()

    dup = c.post(f"/api/templates/{tpl['template_id']}/duplicate")
    assert dup.status_code == 200
    assert dup.json()["name"].endswith("（副本）")

    fav = c.post(f"/api/templates/{tpl['template_id']}/favorite", json={"favorite": True})
    assert fav.status_code == 200
    assert fav.json()["is_favorite"] is True


def test_create_template_from_task(client: dict) -> None:
    c = client["client"]
    _register(c, "alice@example.com")
    task_id = c.post("/api/tasks", json={"content": "帮我搜集深圳的工业自动化设备供应商"}).json()[
        "task_id"
    ]

    payload = {
        "schema_version": "m06.1",
        "task_type": "EXPLORATORY",
        "task_name": None,
        "goal": "帮我搜集深圳的工业自动化设备供应商",
        "fields": [{"name": "公司名", "type": "text", "required": True}],
        "auto_expand_fields": False,
        "source_scope": {"mode": "EXPLORATORY", "seed_urls": [], "source_hints": []},
        "completion_conditions": [],
        "advanced_settings": {},
        "field_expansion": {},
        "template_variables": [{"name": "city", "label": "城市", "value": "深圳"}],
    }
    confirmed = c.post(
        f"/api/tasks/{task_id}/spec-confirm", json={"expected_version": 1, "payload": payload}
    )
    assert confirmed.status_code == 200

    tpl = c.post(f"/api/tasks/{task_id}/template")
    assert tpl.status_code == 200
    assert "{city}" in tpl.json()["goal_template"]
    assert "深圳" not in tpl.json()["goal_template"]
